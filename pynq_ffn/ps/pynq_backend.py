"""Explicit PYNQ driver for the accompanying float32 HLS reference IP."""
from __future__ import annotations

import hashlib
import math
import time
from typing import Callable, Mapping

import numpy as np

POINTER_OFFSETS = {"x": 0x10, "y": 0x1C, "w1": 0x28, "b1": 0x34,
                   "w2": 0x40, "b2": 0x4C}
TOKEN_OFFSET = 0x58
WEIGHT_SHAPES = {"w1": (128, 256), "b1": (256,),
                 "w2": (256, 128), "b2": (128,)}
LAYERS = {"camera_0", "camera_1", "cross_0", "cross_1"}
# A timed-out AXI master may still own these buffers. Keep them alive until reset.
_QUARANTINED_BACKENDS: list["PynqFFN"] = []


class HardwareStateError(RuntimeError):
    pass


def _number(value) -> int:
    return int(value, 0) if isinstance(value, str) else int(value)


def validate_registers(registers: Mapping) -> None:
    """Check exported HWH metadata, not a guessed register layout."""
    if not isinstance(registers, Mapping):
        raise ValueError("Pass overlay.ip_dict[ip_name]['registers'] from the matching HWH")
    control = registers.get("CTRL", registers.get("AP_CTRL"))
    if (not isinstance(control, Mapping)
            or _number(control.get("address_offset", -1)) != 0
            or _number(control.get("size", 0)) != 32):
        raise ValueError("Expected 32-bit ap_ctrl_hs CTRL register at 0x00")
    fields = control.get("fields")
    if not isinstance(fields, Mapping) or not fields:
        raise ValueError("HWH CTRL fields are required to verify ap_ctrl_hs")
    fields = {str(name).upper(): value for name, value in fields.items()}
    if "AP_CONTINUE" in fields:
        raise ValueError("ap_ctrl_chain is not supported")
    for name, offset in {"AP_START": 0, "AP_DONE": 1, "AP_IDLE": 2,
                         "AP_READY": 3, "AUTO_RESTART": 7}.items():
        field = fields.get(name)
        if (not isinstance(field, Mapping)
                or _number(field.get("bit_offset", -1)) != offset
                or _number(field.get("bit_width", 0)) != 1):
            raise ValueError(f"HWH CTRL {name} must be one bit at offset {offset}")
    for name, offset in POINTER_OFFSETS.items():
        combined = registers.get(name)
        if isinstance(combined, Mapping):
            if (_number(combined.get("address_offset", -1)) == offset
                    and _number(combined.get("size", 0)) == 64):
                continue
        low, high = registers.get(name + "_1"), registers.get(name + "_2")
        if (isinstance(low, Mapping) and isinstance(high, Mapping)
                and _number(low.get("address_offset", -1)) == offset
                and _number(high.get("address_offset", -1)) == offset + 4
                and _number(low.get("size", 0)) == 32
                and _number(high.get("size", 0)) == 32):
            continue
        raise ValueError(f"HWH must describe the 64-bit {name} pointer at {offset:#x}")
    tokens = registers.get("num_tokens", {})
    if (not isinstance(tokens, Mapping)
            or _number(tokens.get("address_offset", -1)) != TOKEN_OFFSET
            or _number(tokens.get("size", 0)) != 32):
        raise ValueError("HWH num_tokens must be a 32-bit register at 0x58")


class PynqFFN:
    """No overlay download, device-tree changes, reset, or serial IO is implicit.

    The caller supplies an IP from an explicitly loaded, verified overlay and
    its actual HWH register metadata. This class is single-threaded.
    """

    def __init__(self, ip, registers: Mapping, *, timeout_s: float = 5.0,
                 allocate_fn: Callable | None = None, poll_interval_s: float = 0.0001):
        validate_registers(registers)
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("timeout_s must be positive and finite")
        if not math.isfinite(poll_interval_s) or poll_interval_s < 0:
            raise ValueError("poll_interval_s must be nonnegative and finite")
        if allocate_fn is None:
            from pynq import allocate
            allocate_fn = allocate
        self.ip = ip
        self.allocate = allocate_fn
        self.timeout_s = timeout_s
        self.poll_interval_s = poll_interval_s
        self._buffers = []
        self._weights = {}
        self._x = self._y = None
        self._inflight = False
        self._poisoned = False
        self._closed = False
        self.last_timing = {}

    def _check_open(self):
        if self._closed:
            raise HardwareStateError("FFN backend is closed")
        if self._poisoned or self._inflight:
            raise HardwareStateError("FFN backend is quarantined; a verified hardware reset is required")

    def _allocate(self, shape):
        buffer = self.allocate(shape=shape, dtype=np.float32)
        self._buffers.append(buffer)
        address = int(buffer.device_address)
        if not 0 <= address < (1 << 64):
            raise ValueError("PL device_address must fit an unsigned 64-bit AXI address")
        return buffer

    def _write_pointer(self, name, buffer):
        address = int(buffer.device_address)
        offset = POINTER_OFFSETS[name]
        self.ip.write(offset, address & 0xFFFFFFFF)
        self.ip.write(offset + 4, (address >> 32) & 0xFFFFFFFF)

    def _prepare_weights(self, layer_name: str, weights: Mapping):
        if set(weights) != set(WEIGHT_SHAPES):
            raise ValueError("Expected exactly w1, b1, w2, b2")
        arrays = {}
        digest = hashlib.sha256()
        for name, shape in WEIGHT_SHAPES.items():
            value = np.asarray(weights[name])
            if value.dtype != np.float32 or value.shape != shape or not np.isfinite(value).all():
                raise ValueError(f"{name} must be finite float32 with shape {shape}")
            arrays[name] = np.ascontiguousarray(value)
            digest.update(arrays[name].tobytes())
        signature = digest.hexdigest()
        if layer_name in self._weights:
            previous, buffers = self._weights[layer_name]
            if previous != signature:
                raise ValueError("Weights changed for a cached layer; create a new backend")
            return buffers
        buffers = {}
        for name, value in arrays.items():
            buffer = self._allocate(value.shape)
            np.copyto(buffer, value)
            buffer.sync_to_device()
            buffers[name] = buffer
        self._weights[layer_name] = (signature, buffers)
        return buffers

    def _quarantine(self):
        self._poisoned = True
        if not any(backend is self for backend in _QUARANTINED_BACKENDS):
            _QUARANTINED_BACKENDS.append(self)

    def forward(self, layer_name: str, x: np.ndarray, weights: Mapping) -> np.ndarray:
        self._check_open()
        if layer_name not in LAYERS:
            raise ValueError("Unknown FFN layer")
        x = np.asarray(x)
        if x.dtype != np.float32 or x.ndim < 2 or x.shape[-1] != 128 or not np.isfinite(x).all():
            raise ValueError("FFN input must be finite float32 with last dimension 128")
        tokens = x.size // 128
        if not 1 <= tokens <= 660:
            raise ValueError("FFN supports 1..660 tokens per invocation")
        started = time.perf_counter()
        status = int(self.ip.read(0x00))
        if not status & 0x04 or status & 0x81:
            raise HardwareStateError("FFN IP is not idle, or start/auto_restart is already set")
        buffers = self._prepare_weights(layer_name, weights)
        if self._x is None:
            self._x = self._allocate((660, 128))
        if self._y is None:
            self._y = self._allocate((660, 128))
        np.copyto(self._x[:tokens], np.ascontiguousarray(x).reshape(tokens, 128))
        self._x.sync_to_device()
        # Flush CPU-dirty output cache lines before the PL writes the same DDR.
        self._y.sync_to_device()
        self._write_pointer("x", self._x)
        self._write_pointer("y", self._y)
        for name, buffer in buffers.items():
            self._write_pointer(name, buffer)
        self.ip.write(TOKEN_OFFSET, tokens)
        submitted = time.perf_counter()
        self._inflight = True
        try:
            self.ip.write(0x00, 0x01)
            deadline = time.monotonic() + self.timeout_s
            while True:
                status = int(self.ip.read(0x00))
                if status & 0x02:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("FFN timed out; buffers quarantined. Do not exit this process before a verified PL reset")
                if self.poll_interval_s:
                    time.sleep(self.poll_interval_s)
            completed = time.perf_counter()
            self._inflight = False
            self._y.sync_from_device()
            result = np.array(self._y[:tokens], dtype=np.float32, copy=True).reshape(x.shape)
            if not np.isfinite(result).all():
                raise FloatingPointError("PL FFN returned non-finite values")
            finished = time.perf_counter()
            self.last_timing = {"prepare_ms": (submitted - started) * 1000,
                                "hardware_wait_ms": (completed - submitted) * 1000,
                                "readback_ms": (finished - completed) * 1000,
                                "total_ms": (finished - started) * 1000}
            return result
        except BaseException:
            if self._inflight:
                self._quarantine()
            raise

    def close(self):
        if self._closed:
            return
        if self._poisoned or self._inflight:
            self._quarantine()
            raise HardwareStateError("Refusing to free buffers while PL completion is uncertain; keep process alive until reset")
        for buffer in reversed(self._buffers):
            buffer.freebuffer()
        self._buffers.clear()
        self._weights.clear()
        self._x = self._y = None
        self._closed = True

    def __enter__(self):
        self._check_open()
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._poisoned and exc_type is not None:
            return False
        self.close()
        return False
