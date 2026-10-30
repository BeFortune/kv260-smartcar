"""Isolated r04_v2 FP32 driver candidate, with mandatory injected hardware.

No PYNQ import, Overlay download, DMA, reset or device discovery is performed.
This is a PS preparation cache, not an on-chip weight cache.
"""
from __future__ import annotations

import hashlib
import math
import time
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

import numpy as np

POINTER_OFFSETS = MappingProxyType({"x": 0x10, "y": 0x1C, "w1": 0x28,
                                   "b1": 0x34, "w2": 0x40, "b2": 0x4C})
TOKEN_OFFSET = 0x58
WEIGHT_SHAPES = MappingProxyType({"w1": (128, 256), "b1": (256,),
                                 "w2": (256, 128), "b2": (128,)})
LAYERS = frozenset({"camera_0", "camera_1", "cross_0", "cross_1"})
MAX_TOKENS = 660
# Strong process-lifetime ownership, not an automatic reset/recovery mechanism.
_QUARANTINED_BACKENDS = []


class HardwareStateError(RuntimeError):
    pass


@dataclass(frozen=True)
class PreparedLayerInfo:
    layer: str
    sha256: str
    weight_bytes: int


@dataclass(frozen=True)
class _LayerBinding:
    info: PreparedLayerInfo
    frozen_weights: Mapping
    buffers: Mapping


def freeze_weights(weights):
    """Independent immutable bytes-backed FP32 arrays; NumPy cannot unfreeze them."""
    if not isinstance(weights, Mapping) or set(weights) != set(WEIGHT_SHAPES):
        raise ValueError("Expected exactly w1, b1, w2, b2")
    arrays = {}
    digest = hashlib.sha256()
    for name, shape in WEIGHT_SHAPES.items():
        value = weights[name]
        if (not isinstance(value, np.ndarray) or value.dtype != np.float32
                or value.shape != shape or not np.isfinite(value).all()):
            raise ValueError(f"{name} must be finite float32 with shape {shape}")
        raw = value.tobytes(order="C")
        frozen = np.frombuffer(raw, dtype=np.float32).reshape(shape)
        if not np.isfinite(frozen).all():
            raise ValueError(f"{name} changed to non-finite while being snapshotted")
        arrays[name] = frozen
        digest.update(raw)
    return MappingProxyType(arrays), digest.hexdigest()


def _number(value):
    return int(value, 0) if isinstance(value, str) else int(value)


def validate_registers(registers):
    """Validate matching HWH metadata against literal r04_v2 s_axilite offsets."""
    if not isinstance(registers, Mapping):
        raise ValueError("Matching HWH register metadata is required")
    ctrl = registers.get("CTRL", registers.get("AP_CTRL"))
    if (not isinstance(ctrl, Mapping) or _number(ctrl.get("address_offset", -1)) != 0
            or _number(ctrl.get("size", 0)) != 32):
        raise ValueError("Expected 32-bit ap_ctrl_hs CTRL at 0x00")
    fields = ctrl.get("fields")
    if not isinstance(fields, Mapping) or not fields:
        raise ValueError("HWH CTRL fields are required")
    fields = {str(key).upper(): value for key, value in fields.items()}
    if "AP_CONTINUE" in fields:
        raise ValueError("ap_ctrl_chain is not supported")
    for name, bit in {"AP_START": 0, "AP_DONE": 1, "AP_IDLE": 2,
                      "AP_READY": 3, "AUTO_RESTART": 7}.items():
        field = fields.get(name)
        if (not isinstance(field, Mapping) or _number(field.get("bit_offset", -1)) != bit
                or _number(field.get("bit_width", 0)) != 1):
            raise ValueError(f"HWH CTRL {name} must be one bit at {bit}")
    for name, offset in POINTER_OFFSETS.items():
        combined = registers.get(name)
        if (isinstance(combined, Mapping) and _number(combined.get("size", 0)) == 64
                and _number(combined.get("address_offset", -1)) == offset):
            continue
        low, high = registers.get(name + "_1"), registers.get(name + "_2")
        if (isinstance(low, Mapping) and isinstance(high, Mapping)
                and _number(low.get("address_offset", -1)) == offset
                and _number(high.get("address_offset", -1)) == offset + 4
                and _number(low.get("size", 0)) == _number(high.get("size", 0)) == 32):
            continue
        raise ValueError(f"HWH must describe a 64-bit {name} pointer at {offset:#x}")
    tokens = registers.get("num_tokens")
    if (not isinstance(tokens, Mapping) or _number(tokens.get("address_offset", -1)) != 0x58
            or _number(tokens.get("size", 0)) != 32):
        raise ValueError("HWH num_tokens must be 32 bits at 0x58")


class PreparedFFN:
    """prepare(layer, Mapping[w1,b1,w2,b2]) then run(layer, x).

    The injected IP and allocator must be exclusively owned by this instance.
    Allocations must expose device_address, cache syncs, and freebuffer.
    """
    def __init__(self, ip, registers, *, allocator, clock=None, timeout_s=5.0,
                 poll_interval_s=0.0001):
        validate_registers(registers)
        if not callable(allocator):
            raise ValueError("An explicit allocator is required")
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("timeout_s must be positive and finite")
        if not math.isfinite(poll_interval_s) or poll_interval_s < 0:
            raise ValueError("poll_interval_s must be nonnegative and finite")
        self._ip = ip
        self._allocator = allocator
        self._clock = clock if clock is not None else time
        self._timeout = timeout_s
        self._poll_interval = poll_interval_s
        self._buffers = []
        self._regions = []
        self._layers = {}
        self._x = self._y = None
        self._closed = False
        self._inflight = False
        self._poisoned = False
        self.last_timing = {}

    @property
    def state(self):
        return ("closed" if self._closed else "quarantined" if self._poisoned
                else "inflight" if self._inflight else "ready")

    def _quarantine(self):
        self._poisoned = True
        if not any(backend is self for backend in _QUARANTINED_BACKENDS):
            _QUARANTINED_BACKENDS.append(self)

    def _check_open(self):
        if self._closed:
            raise HardwareStateError("FFN backend is closed")
        if self._poisoned or self._inflight:
            raise HardwareStateError("FFN backend is quarantined; verified hardware reset required")

    def _allocate(self, shape):
        buffer = self._allocator(shape=shape, dtype=np.float32)
        self._buffers.append(buffer)
        if (not isinstance(buffer, np.ndarray) or buffer.dtype != np.float32
                or buffer.shape != shape or not buffer.flags.c_contiguous
                or not buffer.flags.writeable):
            raise ValueError("Allocator must return a writable C-contiguous float32 buffer of the requested shape")
        if not all(callable(getattr(buffer, name, None)) for name in
                   ("sync_to_device", "sync_from_device", "freebuffer")):
            raise ValueError("Allocated buffer must implement cache syncs and freebuffer")
        try:
            address = int(buffer.device_address)
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError("Allocated buffer needs a valid device_address") from error
        end = address + buffer.nbytes
        if not (0 <= address < end <= (1 << 64)) or address % 4:
            raise ValueError("device_address range must be aligned float32 and fit unsigned 64-bit AXI")
        if any(address < stop and begin < end for begin, stop in self._regions):
            raise ValueError("Allocator returned overlapping AXI address ranges")
        self._regions.append((address, end))
        return buffer

    def prepare(self, layer, weights):
        self._check_open()
        if layer not in LAYERS:
            raise ValueError("Unknown FFN layer")
        if layer in self._layers:
            raise ValueError("Layer is already prepared; use a new backend to rebind weights")
        frozen, signature = freeze_weights(weights)
        status = int(self._ip.read(0))
        if not status & 4 or status & 0x81:
            raise HardwareStateError("FFN IP must be idle before preparation")
        buffers = {}
        for name, value in frozen.items():
            buffer = self._allocate(value.shape)
            np.copyto(buffer, value)
            buffer.sync_to_device()
            buffers[name] = buffer
        if self._x is None:
            self._x = self._allocate((660, 128))
            self._y = self._allocate((660, 128))
            self._y.sync_to_device()
        info = PreparedLayerInfo(layer, signature, sum(value.nbytes for value in frozen.values()))
        self._layers[layer] = _LayerBinding(info, frozen, MappingProxyType(buffers))
        return info

    def _pointer(self, name, buffer):
        address = int(buffer.device_address)
        offset = POINTER_OFFSETS[name]
        self._ip.write(offset, address & 0xFFFFFFFF)
        self._ip.write(offset + 4, address >> 32)

    def run(self, layer, x):
        self._check_open()
        if layer not in self._layers:
            raise ValueError("Layer has not been prepared")
        if (not isinstance(x, np.ndarray) or x.dtype != np.float32 or x.ndim < 2
                or x.shape[-1] != 128 or not np.isfinite(x).all()):
            raise ValueError("FFN input must be finite float32 with last dimension 128")
        tokens = x.size // 128
        if not 1 <= tokens <= 660:
            raise ValueError("FFN supports 1..660 tokens")
        started = self._clock.perf_counter()
        status = int(self._ip.read(0))
        if not status & 4 or status & 0x81:
            raise HardwareStateError("FFN IP must be idle with START/AUTO_RESTART clear")
        active_x = self._x[:tokens]
        active_y = self._y[:tokens]
        np.copyto(active_x.reshape(x.shape), x)
        active_x.sync_to_device()
        self._pointer("x", self._x)
        self._pointer("y", self._y)
        for name, buffer in self._layers[layer].buffers.items():
            self._pointer(name, buffer)
        self._ip.write(TOKEN_OFFSET, tokens)
        submitted = self._clock.perf_counter()
        self._inflight = True
        try:
            self._ip.write(0, 1)
            deadline = self._clock.monotonic() + self._timeout
            while True:
                status = int(self._ip.read(0))
                if status & 2:
                    if status & 0x81:
                        raise HardwareStateError("DONE observed with START/AUTO_RESTART set; ownership uncertain")
                    break
                if self._clock.monotonic() >= deadline:
                    raise TimeoutError("FFN timed out; buffers quarantined. Keep process alive until verified PL reset")
                if self._poll_interval:
                    self._clock.sleep(self._poll_interval)
            self._inflight = False
        except BaseException:
            if self._inflight:
                self._quarantine()
            raise
        completed = self._clock.perf_counter()
        active_y.sync_from_device()
        result = np.array(active_y, dtype=np.float32, copy=True).reshape(x.shape)
        if not np.isfinite(result).all():
            raise FloatingPointError("PL FFN returned non-finite values")
        finished = self._clock.perf_counter()
        self.last_timing = {"prepare_ms": (submitted - started) * 1000,
                            "hardware_wait_ms": (completed - submitted) * 1000,
                            "readback_ms": (finished - completed) * 1000,
                            "total_ms": (finished - started) * 1000}
        return result

    def close(self):
        if self._closed:
            return
        if self._poisoned or self._inflight:
            self._quarantine()
            raise HardwareStateError("Refusing to free buffers with uncertain completion; keep process alive until verified reset")
        if self._buffers:
            try:
                status = int(self._ip.read(0))
                if not status & 4 or status & 0x81:
                    raise HardwareStateError("IP is not safely idle")
            except BaseException as error:
                self._quarantine()
                raise HardwareStateError("Refusing to free buffers without verified idle state") from error
        for buffer in reversed(self._buffers):
            buffer.freebuffer()
        self._buffers.clear()
        self._regions.clear()
        self._layers.clear()
        self._x = self._y = None
        self._closed = True
