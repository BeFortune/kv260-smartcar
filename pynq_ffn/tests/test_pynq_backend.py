from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ps.pynq_backend import (HardwareStateError, PynqFFN, POINTER_OFFSETS,
                            TOKEN_OFFSET, WEIGHT_SHAPES, validate_registers)


def metadata():
    fields = {name: {"bit_offset": offset, "bit_width": 1}
              for name, offset in {"AP_START": 0, "AP_DONE": 1, "AP_IDLE": 2,
                                   "AP_READY": 3, "AUTO_RESTART": 7}.items()}
    registers = {"CTRL": {"address_offset": 0, "size": 32, "fields": fields},
                 "num_tokens": {"address_offset": 0x58, "size": 32}}
    registers.update({name: {"address_offset": offset, "size": 64}
                      for name, offset in {"x": 0x10, "y": 0x1C, "w1": 0x28,
                                           "b1": 0x34, "w2": 0x40, "b2": 0x4C}.items()})
    return registers


class Buffer(np.ndarray):
    def __new__(cls, shape, address):
        result = np.zeros(shape, dtype=np.float32).view(cls)
        result.device_address = address
        result.to_device = 0
        result.from_device = 0
        result.freed = False
        result.events = []
        return result

    def __array_finalize__(self, obj):
        if obj is not None:
            self.device_address = getattr(obj, "device_address", 0)

    def sync_to_device(self):
        self.to_device += 1
        self.events.append(("to_device", self.device_address))

    def sync_from_device(self):
        self.from_device += 1
        self.events.append(("from_device", self.device_address))

    def freebuffer(self):
        self.freed = True
        self.events.append(("free", self.device_address))


class Hardware:
    def __init__(self):
        self.buffers = []
        self.registers = {}
        self.status = 4
        self.complete = True
        self.fail_start = False
        self.bad_output = False
        self.starts = 0
        self.events = []

    def allocate(self, *, shape, dtype):
        assert dtype == np.float32
        buffer = Buffer(shape, (1 << 35) + 0x100000 * len(self.buffers) + 0x1000)
        buffer.events = self.events
        self.buffers.append(buffer)
        return buffer

    def read(self, offset):
        assert offset == 0
        result = self.status
        self.events.append(("read_control", result))
        self.status &= ~2
        return result

    def write(self, offset, value):
        self.events.append(("write", offset, value))
        self.registers[offset] = value
        if offset == 0 and value == 1:
            self.starts += 1
            self.status = 0
            if self.fail_start:
                raise OSError("write completion unknown")
            if self.complete:
                def pointer(name):
                    low = POINTER_OFFSETS[name]
                    address = self.registers[low] | (self.registers[low + 4] << 32)
                    return next(b for b in self.buffers if b.device_address == address)
                tokens = self.registers[TOKEN_OFFSET]
                pointer("y")[:tokens] = pointer("x")[:tokens] + pointer("b2")
                if self.bad_output:
                    pointer("y")[0, 0] = np.nan
                self.status = 6


def weights():
    result = {name: np.zeros(shape, dtype=np.float32)
              for name, shape in WEIGHT_SHAPES.items()}
    result["b2"][:] = 2
    return result


def test_metadata_checks_offsets_widths_and_split_pointer_names():
    validate_registers(metadata())
    split = metadata()
    for name, offset in POINTER_OFFSETS.items():
        del split[name]
        split[name + "_1"] = {"address_offset": hex(offset), "size": "32"}
        split[name + "_2"] = {"address_offset": offset + 4, "size": 32}
    validate_registers(split)
    wrong = metadata()
    wrong["x"]["size"] = 32
    with pytest.raises(ValueError, match="64-bit x"):
        validate_registers(wrong)
    wrong = metadata()
    wrong["num_tokens"]["address_offset"] = 0x60
    with pytest.raises(ValueError, match="num_tokens"):
        validate_registers(wrong)


def test_64bit_addresses_cache_sync_buffer_reuse_and_cleanup():
    hardware = Hardware()
    driver = PynqFFN(hardware, metadata(), allocate_fn=hardware.allocate)
    x = np.ones((1, 4, 128), dtype=np.float32)
    result = driver.forward("cross_0", x, weights())
    np.testing.assert_array_equal(result, x + 2)
    assert result.shape == x.shape
    assert len(hardware.buffers) == 6
    assert all(hardware.registers[offset + 4] == 8 for offset in POINTER_OFFSETS.values())
    assert all(buffer.to_device == 1 for buffer in hardware.buffers)
    assert hardware.buffers[-1].from_device == 1
    result[:] = -100
    assert (hardware.buffers[-1][:4] == 3).all()
    driver.forward("cross_0", x, weights())
    assert len(hardware.buffers) == 6
    assert hardware.starts == 2
    assert hardware.buffers[-1].from_device == 2
    assert driver.last_timing["total_ms"] >= 0
    driver.close()
    driver.close()
    assert all(buffer.freed for buffer in hardware.buffers)
    with pytest.raises(HardwareStateError, match="closed"):
        driver.forward("cross_0", x, weights())


@pytest.mark.parametrize("shape", [(0, 128), (661, 128), (4, 127)])
def test_invalid_shape_does_not_allocate_or_start(shape):
    hardware = Hardware()
    with PynqFFN(hardware, metadata(), allocate_fn=hardware.allocate) as driver:
        with pytest.raises(ValueError):
            driver.forward("camera_0", np.zeros(shape, dtype=np.float32), weights())
    assert not hardware.buffers
    assert not hardware.starts


def test_wrong_dtype_nan_unknown_layer_and_weights_are_rejected():
    hardware = Hardware()
    with PynqFFN(hardware, metadata(), allocate_fn=hardware.allocate) as driver:
        for x in [np.zeros((4, 128), dtype=np.float64),
                  np.full((4, 128), np.nan, dtype=np.float32)]:
            with pytest.raises(ValueError):
                driver.forward("cross_0", x, weights())
        with pytest.raises(ValueError, match="Unknown"):
            driver.forward("missing", np.zeros((4, 128), dtype=np.float32), weights())
        wrong = weights()
        wrong["w1"] = wrong["w1"].T
        with pytest.raises(ValueError, match="w1"):
            driver.forward("cross_0", np.zeros((4, 128), dtype=np.float32), wrong)
    assert not hardware.starts


def test_changed_weights_and_busy_device_refused():
    hardware = Hardware()
    with PynqFFN(hardware, metadata(), allocate_fn=hardware.allocate) as driver:
        x = np.zeros((1, 4, 128), dtype=np.float32)
        driver.forward("cross_0", x, weights())
        changed = weights()
        changed["b2"][0] = 99
        with pytest.raises(ValueError, match="Weights changed"):
            driver.forward("cross_0", x, changed)
        hardware.status = 0
        with pytest.raises(HardwareStateError, match="idle"):
            driver.forward("cross_0", x, weights())
        hardware.status = 0x84
        with pytest.raises(HardwareStateError, match="idle"):
            driver.forward("cross_0", x, weights())
    assert hardware.starts == 1


def test_timeout_quarantines_buffers_and_never_frees_inflight_memory():
    hardware = Hardware()
    hardware.complete = False
    driver = PynqFFN(hardware, metadata(), timeout_s=0.001,
                     allocate_fn=hardware.allocate, poll_interval_s=0)
    with pytest.raises(TimeoutError, match="quarantined"):
        driver.forward("cross_0", np.zeros((4, 128), dtype=np.float32), weights())
    with pytest.raises(HardwareStateError, match="Refusing to free"):
        driver.close()
    with pytest.raises(HardwareStateError, match="quarantined"):
        driver.forward("cross_0", np.zeros((4, 128), dtype=np.float32), weights())
    assert not any(buffer.freed for buffer in hardware.buffers)


def test_ambiguous_start_failure_keeps_buffers_alive():
    hardware = Hardware()
    hardware.fail_start = True
    driver = PynqFFN(hardware, metadata(), allocate_fn=hardware.allocate)
    with pytest.raises(OSError):
        driver.forward("cross_0", np.zeros((4, 128), dtype=np.float32), weights())
    with pytest.raises(HardwareStateError):
        driver.close()
    assert not any(buffer.freed for buffer in hardware.buffers)


def test_nonfinite_completed_output_fails_but_can_release_memory():
    hardware = Hardware()
    hardware.bad_output = True
    driver = PynqFFN(hardware, metadata(), allocate_fn=hardware.allocate)
    with pytest.raises(FloatingPointError):
        driver.forward("cross_0", np.zeros((4, 128), dtype=np.float32), weights())
    driver.close()
    assert all(buffer.freed for buffer in hardware.buffers)


def test_literal_abi_and_control_field_contract():
    assert POINTER_OFFSETS == {"x": 0x10, "y": 0x1C, "w1": 0x28,
                               "b1": 0x34, "w2": 0x40, "b2": 0x4C}
    assert TOKEN_OFFSET == 0x58
    wrong = metadata()
    wrong["CTRL"]["size"] = 1
    with pytest.raises(ValueError, match="32-bit"):
        validate_registers(wrong)
    wrong = metadata()
    wrong["CTRL"]["fields"]["AP_START"]["bit_offset"] = 7
    with pytest.raises(ValueError, match="AP_START"):
        validate_registers(wrong)
    wrong = metadata()
    wrong["CTRL"]["fields"]["AP_CONTINUE"] = {"bit_offset": 4, "bit_width": 1}
    with pytest.raises(ValueError, match="chain"):
        validate_registers(wrong)
    wrong = metadata()
    del wrong["CTRL"]["fields"]
    with pytest.raises(ValueError, match="fields"):
        validate_registers(wrong)
    wrong = metadata()
    wrong["num_tokens"] = None
    with pytest.raises(ValueError, match="num_tokens"):
        validate_registers(wrong)


def test_cache_sync_order_precedes_start_and_follows_done():
    hardware = Hardware()
    with PynqFFN(hardware, metadata(), allocate_fn=hardware.allocate) as driver:
        driver.forward("cross_1", np.ones((4, 128), dtype=np.float32), weights())
    events = hardware.events
    start = events.index(("write", 0, 1))
    done = next(i for i, event in enumerate(events)
                if i > start and event[0] == "read_control" and event[1] & 2)
    uploads = [i for i, event in enumerate(events) if event[0] == "to_device"]
    downloads = [i for i, event in enumerate(events) if event[0] == "from_device"]
    frees = [i for i, event in enumerate(events) if event[0] == "free"]
    assert len(uploads) == 6 and all(i < start for i in uploads)
    assert len(downloads) == 1 and downloads[0] > done
    assert all(i > downloads[0] for i in frees)
    for offset in (0x10, 0x1C, 0x28, 0x34, 0x40, 0x4C):
        assert hardware.registers[offset] != 0
        assert hardware.registers[offset + 4] != 0


@pytest.mark.parametrize("tokens", [1, 660])
def test_successful_token_edges_and_noncontiguous_input(tokens):
    hardware = Hardware()
    with PynqFFN(hardware, metadata(), allocate_fn=hardware.allocate) as driver:
        data = np.ones((tokens, 256), dtype=np.float32)[:, ::2]
        assert not data.flags.c_contiguous
        result = driver.forward("camera_1", data, weights())
        np.testing.assert_array_equal(result, data + 2)
        assert hardware.registers[0x58] == tokens


def test_quarantine_keeps_backend_and_buffers_alive_after_gc():
    import gc
    import weakref
    hardware = Hardware()
    hardware.complete = False
    driver = PynqFFN(hardware, metadata(), allocate_fn=hardware.allocate,
                     timeout_s=0.001, poll_interval_s=0)
    reference = weakref.ref(driver)
    try:
        with driver:
            driver.forward("cross_0", np.ones((4, 128), dtype=np.float32), weights())
    except TimeoutError:
        pass
    else:
        pytest.fail("Timeout expected")
    buffer_refs = [weakref.ref(buffer) for buffer in hardware.buffers]
    hardware.buffers.clear()
    del driver
    gc.collect()
    assert reference() is not None
    assert all(ref() is not None and not ref().freed for ref in buffer_refs)


@pytest.mark.parametrize("error_type", [OSError, KeyboardInterrupt])
def test_poll_error_or_interrupt_quarantines(error_type):
    class FailingReadHardware(Hardware):
        def read(self, offset):
            if self.starts:
                raise error_type("poll interrupted")
            return super().read(offset)
    hardware = FailingReadHardware()
    driver = PynqFFN(hardware, metadata(), allocate_fn=hardware.allocate)
    with pytest.raises(error_type):
        driver.forward("cross_0", np.ones((4, 128), dtype=np.float32), weights())
    with pytest.raises(HardwareStateError, match="Refusing"):
        driver.close()
    assert not any(buffer.freed for buffer in hardware.buffers)


def test_delayed_done_and_stale_done_are_handled():
    class DelayedHardware(Hardware):
        def __init__(self):
            super().__init__()
            self.status = 6
            self.pending_polls = 3
        def read(self, offset):
            if self.starts and self.pending_polls:
                self.pending_polls -= 1
                return 0
            return super().read(offset)
    hardware = DelayedHardware()
    with PynqFFN(hardware, metadata(), allocate_fn=hardware.allocate,
                 poll_interval_s=0) as driver:
        result = driver.forward("cross_0", np.ones((4, 128), dtype=np.float32), weights())
        np.testing.assert_array_equal(result, 3)
    assert hardware.pending_polls == 0


@pytest.mark.parametrize("address", [-1, 1 << 64])
def test_invalid_device_address_is_rejected_before_start(address):
    hardware = Hardware()
    def allocate(**kwargs):
        buffer = hardware.allocate(**kwargs)
        buffer.device_address = address
        return buffer
    with PynqFFN(hardware, metadata(), allocate_fn=allocate) as driver:
        with pytest.raises(ValueError, match="64-bit"):
            driver.forward("cross_0", np.ones((4, 128), dtype=np.float32), weights())
    assert hardware.starts == 0
    assert all(buffer.freed for buffer in hardware.buffers)
