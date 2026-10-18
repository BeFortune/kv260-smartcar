"""Offline tests: mock MMIO is NOT PL validation. Run with pytest -q this file."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import weakref

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[3]
spec = importlib.util.spec_from_file_location("board_validate", HERE / "board_validate.py")
v = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)
sys.path.insert(0, str(PROJECT))
from ps.pynq_backend import PynqFFN, HardwareStateError


def install_runtime_state(pynq, root, tmp_path, monkeypatch):
    from types import SimpleNamespace, ModuleType
    import hashlib
    bit = root / "overlay/ffn_overlay_wrapper.bit"
    state = {"bitfile_name": str(bit), "timestamp": "mock_timestamp",
             "active_name": "MOCK", "bitfile_hash": hashlib.sha1(bit.read_bytes()).hexdigest()}
    (tmp_path / "global_pl_state.json").write_text(json.dumps(state))
    pynq.PL = SimpleNamespace(bitfile_name=str(bit), timestamp=state["timestamp"])
    server, global_state = ModuleType("pynq.pl_server"), ModuleType("pynq.pl_server.global_state")
    global_state.STATE_DIR = str(tmp_path)
    server.global_state = global_state
    monkeypatch.setitem(sys.modules, "pynq.pl_server", server)
    monkeypatch.setitem(sys.modules, "pynq.pl_server.global_state", global_state)


def metadata():
    regs = {"CTRL": {"address_offset": 0, "size": 32,
                     "fields": {k: {"bit_offset": n, "bit_width": 1} for k, n in
                                {"AP_START": 0, "AP_DONE": 1, "AP_IDLE": 2, "AP_READY": 3, "AUTO_RESTART": 7}.items()}},
            "num_tokens": {"address_offset": 0x58, "size": 32}}
    regs.update({k: {"address_offset": n, "size": 64} for k, n in
                 {"x": 0x10, "y": 0x1c, "w1": 0x28, "b1": 0x34, "w2": 0x40, "b2": 0x4c}.items()})
    return regs


class Buffer(np.ndarray):
    def __new__(cls, shape, address):
        obj = np.zeros(shape, np.float32).view(cls)
        obj.device_address = address
        obj.freed = False
        return obj
    def sync_to_device(self):
        pass
    def sync_from_device(self):
        pass
    def freebuffer(self):
        self.freed = True


class MockIP:
    def __init__(self, complete=True):
        self.status, self.complete, self.starts = 4, complete, 0
        self.regs, self.buffers = {}, []
    def allocate(self, *, shape, dtype):
        value = Buffer(shape, (1 << 35) + len(self.buffers) * 0x100000)
        self.buffers.append(value)
        return value
    def read(self, offset):
        value = self.status
        self.status &= ~2
        return value
    def write(self, offset, value):
        self.regs[offset] = value
        if offset == 0 and value == 1:
            self.starts += 1
            self.status = 0
            if self.complete:
                def ptr(offset):
                    address = self.regs[offset] | self.regs[offset + 4] << 32
                    return next(b for b in self.buffers if b.device_address == address)
                n = self.regs[0x58]
                ptr(0x1c)[:n] = ptr(0x10)[:n] + ptr(0x4c)
                self.status = 6


class MemoryEvidence:
    def __init__(self, path):
        self.directory = path
        self.events = []
    def event(self, kind, data):
        self.events.append((kind, data))


def fake_fixture(tmp_path):
    weights = {f"{layer}.{key}": np.zeros(shape, np.float32)
               for layer in v.LAYERS for key, shape in
               {"w1": (128, 256), "b1": (256,), "w2": (256, 128), "b2": (128,)}.items()}
    cuts = {}
    for index, layer in enumerate(v.LAYERS):
        weights[f"{layer}.b2"][:] = index
        n = 660 if layer.startswith("camera") else 4
        x = np.arange(n * 128, dtype=np.float32).reshape(n, 128) / 2000
        cuts[f"input_{layer}"] = x
        cuts[f"ffn_{layer}"] = x + index
    v.dump_npz(np, tmp_path / "weights.npz", weights)
    v.dump_npz(np, tmp_path / "split-ort_0_0.npz", cuts)


def test_real_driver_mock_rotation_and_illegal_gates(tmp_path):
    fake_fixture(tmp_path)
    raw = MockIP()
    ip = v.CountedIP(raw)
    backend = PynqFFN(ip, metadata(), allocate_fn=raw.allocate)
    evidence = MemoryEvidence(tmp_path)
    result = v.layer_checks(np, backend, ip, tmp_path, tmp_path, evidence)
    assert result["cases"] == 36
    assert result["python_gates_no_mmio"] == 6
    assert raw.starts == 36
    assert all(r["max_abs_error"] == 0 for r in result["rows"])
    backend.close()
    assert all(b.freed for b in raw.buffers)


def test_real_driver_mock_timeout_never_free(tmp_path):
    raw = MockIP(complete=False)
    backend = PynqFFN(raw, metadata(), allocate_fn=raw.allocate, timeout_s=.001, poll_interval_s=0)
    weights = {k: np.zeros(shape, np.float32) for k, shape in
               {"w1": (128, 256), "b1": (256,), "w2": (256, 128), "b2": (128,)}.items()}
    with pytest.raises(TimeoutError):
        backend.forward("camera_0", np.zeros((1, 128), np.float32), weights)
    with pytest.raises(HardwareStateError):
        backend.close()
    assert not any(b.freed for b in raw.buffers)


def test_quarantine_mock_process_survives_interrupt(tmp_path):
    # The child owns ONLY an object() mock, never hardware or buffers. It is killed
    # after proving the keepalive behavior; killing real PL jobs is forbidden.
    code = f'''import importlib.util, argparse, signal
from pathlib import Path
spec=importlib.util.spec_from_file_location("bv", {str(HERE / 'board_validate.py')!r})
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
e=m.Evidence(Path({str(tmp_path / 'quarantine')!r}), argparse.Namespace(mock=True))
def sleeper(seconds):
    Path({str(tmp_path / 'alive')!r}).write_text("alive")
    if not hasattr(sleeper,"first"):
        sleeper.first=True
        raise KeyboardInterrupt()
    import time; time.sleep(1)
from types import SimpleNamespace
ip=m.CountedIP(object()); ip.starts=1
m.quarantine(e, SimpleNamespace(ip=ip), TimeoutError("MOCK ONLY"), sleeper=sleeper)
'''
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 15
        while not (tmp_path / "alive").exists() and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(.02)
        assert (tmp_path / "alive").exists(), proc.communicate(timeout=2)
        time.sleep(.15)
        assert proc.poll() is None
        report = json.loads((tmp_path / "quarantine/report.json").read_text(encoding="utf8"))
        assert report["failure_mode"] == "failed_quarantined"
        assert report["hardware_execution"] is True
        assert report["kernel_starts_observed"] == 1
        assert report["status"] == "失败"
    finally:
        if proc.poll() is None:
            proc.kill()  # mock-only child, explicitly no PL resources
        proc.communicate(timeout=5)


def deployment(tmp_path):
    root = tmp_path / "deployment"
    root.mkdir()
    (root / "ps").mkdir()
    for p in (PROJECT / "ps").glob("*.py"):
        shutil.copy2(p, root / "ps" / p.name)
    shutil.copytree(PROJECT / "artifacts/bundle_20261014", root / "bundle")
    (root / "overlay").mkdir()
    bit = root / "overlay/ffn_overlay_wrapper.bit"
    bit.write_bytes(b"MOCK_BIT_NOT_PROGRAMMABLE")
    hwh = bit.with_suffix(".hwh")
    shutil.copy2(PROJECT / "artifacts/vivado_20261015/retry04/overlay/ffn_overlay_wrapper.hwh", hwh)
    files = {p.relative_to(root).as_posix(): {"bytes": p.stat().st_size, "sha256": v.sha(p)}
             for p in root.rglob("*") if p.is_file()}
    manifest = tmp_path / "external.json"
    manifest.write_text(json.dumps({"schema": "ffn_board.deployment.v1", "files": files}))
    argv = ["--root", str(root), "--deployment-manifest", str(manifest), "--manifest-sha256", v.sha(manifest),
            "--bit-sha256", v.sha(bit), "--hwh-sha256", v.sha(hwh), "--output-dir", str(tmp_path / "result")]
    return root, argv


def test_static_default_fresh_process_no_project_or_pynq_import(tmp_path):
    root, argv = deployment(tmp_path)
    script = f'''import runpy,sys
m=runpy.run_path({str(HERE / 'board_validate.py')!r})
assert m['main']({argv!r})==0
assert 'pynq' not in sys.modules
assert 'ps.runtime' not in sys.modules
'''
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    report = json.loads((tmp_path / "result/report.json").read_text(encoding="utf8"))
    assert report["phases"]["static"]["status"] == "已验证"
    assert not report["hardware_execution"]
    assert report["phases"]["hardware"]["status"] == "未执行"
    assert not list((root / "ps").rglob("*.pyc"))
    with pytest.raises(FileExistsError):
        v.main(argv)


def test_static_hash_tampering_blocks_import(tmp_path):
    root, argv = deployment(tmp_path)
    (root / "ps/runtime.py").write_text("raise AssertionError('MUST NOT IMPORT')")
    assert v.main(argv) == 1
    report = json.loads((tmp_path / "result/report.json").read_text(encoding="utf8"))
    assert "hash/size mismatch" in report["error"]


def test_static_rejects_stale_bytecode(tmp_path):
    root, argv = deployment(tmp_path)
    (root / "ps/stale.pyc").write_bytes(b"unexpected executable")
    with pytest.raises(ValueError, match="source-only"):
        v.static_check(v.parse_args(argv))


def test_permissions_block_before_pynq_import(tmp_path, monkeypatch):
    _, argv = deployment(tmp_path)
    monkeypatch.setattr(v.platform, "machine", lambda: "x86_64")
    assert v.main(argv + ["--allow-pl", "--reuse-programmed-overlay", "--recording", "unused", "--expected-frames", "117"]) == 1
    report = json.loads((tmp_path / "result/report.json").read_text(encoding="utf8"))
    assert report["error_type"] == "PermissionError"


def test_count_mismatch_stops_before_overlay_import(tmp_path, monkeypatch):
    _, argv = deployment(tmp_path)
    monkeypatch.setattr(v.platform, "system", lambda: "Linux")
    monkeypatch.setattr(v.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(v.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(v, "load_project", lambda root: (np, None, lambda paths: [object()], None, None, lambda x: None))
    assert v.main(argv + ["--allow-pl", "--reuse-programmed-overlay", "--recording", "not_opened", "--expected-frames", "117"]) == 1
    report = json.loads((tmp_path / "result/report.json").read_text(encoding="utf8"))
    assert "actual 1 != expected 117" in report["error"]
    assert report["phases"]["hardware"]["status"] == "未执行"
    assert "pynq" not in sys.modules


def test_hwh_drift_rejected_before_import(tmp_path):
    root, argv = deployment(tmp_path)
    p = root / "overlay/ffn_overlay_wrapper.hwh"
    text = p.read_text().replace('NAME="num_tokens"', 'NAME="wrong_tokens"')
    p.write_text(text)
    with pytest.raises(ValueError, match="token register"):
        v.hwh_registers(p, "ffn_accel_0")


def test_cli_rejects_unfair_timing():
    base = ["--root", "r", "--deployment-manifest", "m", "--manifest-sha256", "0" * 64,
            "--bit-sha256", "0" * 64, "--hwh-sha256", "0" * 64, "--output-dir", "o"]
    for extra in (["--timing-frames", "1"], ["--warmup", "0"], ["--threads", "1"],
                  ["--recording", "a"], ["--timeout-s", "nan"], ["--allow-pl"]):
        with pytest.raises(SystemExit):
            v.parse_args(base + extra)


def test_nonfinite_outputs_and_int_truncation():
    with pytest.raises(AssertionError):
        v.compare(np, np.array([np.nan]), np.array([0.]), 1e-3)
    v.compare_integer(np, np.array([-1.9, 2.9]), np.array([-1.1, 2.1]))
    with pytest.raises(AssertionError, match="Integer"):
        v.compare_integer(np, np.array([2.99]), np.array([3.]))


def test_performance_modes_have_one_owner_and_equal_warmups(tmp_path):
    active = []
    calls = []
    class Sessions(dict):
        def clear(self):
            super().clear()
            active.pop()
    class Runtime:
        def __init__(self, bundle, mode, threads, backend=None):
            assert not active, "concurrent runtime pool owners"
            active.append((mode, threads))
            self.mode, self.threads = mode, threads
            self.sessions = Sessions()
        def run(self, images):
            calls.append((self.mode, self.threads))
            self.last_timings_ms = {"total": .1, "stage_0": .05}
            return {}
    measured = v.MeasuredBackend(None)
    evidence = MemoryEvidence(tmp_path)
    report = v.performance(np, Runtime, "unused", list(range(30)), lambda x: x, [1, 4], 3, measured, evidence)
    assert len(report["conditions"]) == 6
    from collections import Counter
    assert set(Counter(calls).values()) == {33}
    assert not active
    assert all(c["run_total_ms"]["n"] == 30 for c in report["conditions"])


def test_clock_args_defaults_and_invalid_values():
    base = ["--root", "r", "--deployment-manifest", "m", "--manifest-sha256", "0" * 64,
            "--bit-sha256", "0" * 64, "--hwh-sha256", "0" * 64, "--output-dir", "o"]
    args = v.parse_args(base)
    assert args.expected_clock_mhz == 100
    assert args.clock_tolerance_percent == .5
    for extra in (["--expected-clock-mhz", "nan"], ["--expected-clock-mhz", "0"],
                  ["--clock-tolerance-percent", "inf"], ["--clock-tolerance-percent", "-1"],
                  ["--clock-tolerance-percent", "1"]):
        with pytest.raises(SystemExit):
            v.parse_args(base + extra)


@pytest.mark.parametrize("actual,passes", [(100., True), (99.999, True), (100.5, True),
                                           (150., False), (float("nan"), False),
                                           (float("inf"), False), (0., False)])
def test_actual_clock_guard_readonly(tmp_path, actual, passes):
    class Clocks:
        @property
        def fclk0_mhz(self):
            return actual
        @fclk0_mhz.setter
        def fclk0_mhz(self, value):
            pytest.fail("Clock setter must never be used")
    args = argparse.Namespace(expected_clock_mhz=100., clock_tolerance_percent=.5)
    evidence = v.Evidence(tmp_path / "evidence", args)
    ip = v.CountedIP(MockIP())
    if passes:
        assert v.validate_clock(Clocks(), args, evidence, ip, "prestart") == actual
    else:
        with pytest.raises(v.ClockMismatchError):
            v.validate_clock(Clocks(), args, evidence, ip, "prestart")
        assert evidence.report["failure_mode"] == "failed_clock_mismatch"
    row = evidence.report["clock_checks"][-1]
    assert row["raw_state"]["ctrl"] == "0x4"
    assert row["hardware_programmed"] is False
    assert row["compute_executed"] is False
    assert ip.writes == ip.starts == 0
    evidence.save()  # nonfinite getter must remain strict-JSON serializable


def test_endcheck_detects_drift_against_initial_validated_clock(tmp_path):
    from types import SimpleNamespace
    args = argparse.Namespace(expected_clock_mhz=100., clock_tolerance_percent=.5)
    evidence = v.Evidence(tmp_path / "endcheck", args)
    ip = v.CountedIP(MockIP())
    first = v.validate_clock(SimpleNamespace(fclk0_mhz=99.6), args, evidence, ip, "prestart")
    ip.starts = 1
    # Both are within expected tolerance, but the drift from initial is >0.5MHz.
    with pytest.raises(v.ClockMismatchError):
        v.validate_clock(SimpleNamespace(fclk0_mhz=100.4), args, evidence, ip, "endcheck", initial=first)
    assert evidence.report["compute_executed"] is True
    assert evidence.report["clock_checks"][-1]["initial_validated_clock_mhz"] == first
    assert ip.writes == 0


@pytest.mark.parametrize("actual", [150., float("nan"), float("inf")])
def test_main_mismatch_programmed_but_no_backend_no_alloc_no_start(tmp_path, monkeypatch, actual):
    from types import SimpleNamespace, ModuleType
    root, argv = deployment(tmp_path)
    monkeypatch.setattr(v.platform, "system", lambda: "Linux")
    monkeypatch.setattr(v.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(v.os, "geteuid", lambda: 0, raising=False)
    record = SimpleNamespace(left=root / "ps/runtime.py", front=root / "ps/runtime.py", right=root / "ps/runtime.py")
    def forbidden_backend(*a, **kw):
        pytest.fail("Clock failure must precede backend construction and allocation")
    monkeypatch.setattr(v, "load_project", lambda r: (np, None, lambda paths: [record], None, forbidden_backend, lambda r: None))
    raw = MockIP()
    class Clocks:
        @property
        def fclk0_mhz(self):
            return actual
        @fclk0_mhz.setter
        def fclk0_mhz(self, value):
            pytest.fail("No automatic clock repair")
    overlay = SimpleNamespace(ip_dict={"ffn_accel_0": {"registers": metadata()}}, ffn_accel_0=raw)
    pynq = ModuleType("pynq")
    def overlay_factory(path, *, download):
        assert download is False
        assert Path(path) == root / "overlay/ffn_overlay_wrapper.bit"
        return overlay
    pynq.Overlay = overlay_factory
    pynq.Clocks = Clocks()
    install_runtime_state(pynq, root, tmp_path, monkeypatch)
    monkeypatch.setitem(sys.modules, "pynq", pynq)
    assert v.main(argv + ["--allow-pl", "--reuse-programmed-overlay", "--recording", str(root / "ps/runtime.py"), "--expected-frames", "1", "--stage", "correctness"]) == 1
    report = json.loads((tmp_path / "result/report.json").read_text(encoding="utf8"))
    assert report["failure_mode"] == "failed_clock_mismatch"
    assert report["hardware_programmed"] is False
    assert report["compute_executed"] is report["hardware_execution"] is False
    assert report["kernel_starts_observed"] == 0
    assert not raw.buffers and raw.starts == 0 and not raw.regs
    events = [json.loads(line) for line in (tmp_path / "result/events.jsonl").read_text(encoding="utf8").splitlines()]
    mismatch = next(e["data"] for e in events if e["kind"] == "failed_clock_mismatch")
    assert mismatch["expected_clock_mhz"] == 100
    assert mismatch["raw_state"]["ctrl"] == "0x4"
    assert mismatch["hardware_programmed"] is False and mismatch["compute_executed"] is False
    assert mismatch["actual_clock_raw"] == repr(actual)


@pytest.mark.parametrize("end_clock,exit_code", [(100., 0), (150., 1)])
def test_main_100_gate_then_same_clock_endcheck(tmp_path, monkeypatch, end_clock, exit_code):
    from types import SimpleNamespace, ModuleType
    root, argv = deployment(tmp_path)
    monkeypatch.setattr(v.platform, "system", lambda: "Linux")
    monkeypatch.setattr(v.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(v.os, "geteuid", lambda: 0, raising=False)
    record = SimpleNamespace(left=root / "ps/runtime.py", front=root / "ps/runtime.py", right=root / "ps/runtime.py")
    backends = []
    class Backend:
        def __init__(self, ip, regs, timeout_s):
            self.ip, self.closed = ip, False
            backends.append(self)
        def close(self):
            self.closed = True
    monkeypatch.setattr(v, "load_project", lambda r: (np, None, lambda paths: [record], None, Backend, lambda r: None))
    raw = MockIP()
    class Clocks:
        def __init__(self):
            self.reads = 0
        @property
        def fclk0_mhz(self):
            self.reads += 1
            return 100. if self.reads == 1 else end_clock
        @fclk0_mhz.setter
        def fclk0_mhz(self, value):
            pytest.fail("No clock setter")
    clock = Clocks()
    overlay = SimpleNamespace(ip_dict={"ffn_accel_0": {"registers": metadata()}},
                              ffn_accel_0=raw, clock_dict={"cached_mhz": 1.})
    pynq = ModuleType("pynq")
    def overlay_factory(path, *, download):
        assert download is False
        assert Path(path) == root / "overlay/ffn_overlay_wrapper.bit"
        return overlay
    pynq.Overlay = overlay_factory
    pynq.Clocks = clock
    install_runtime_state(pynq, root, tmp_path, monkeypatch)
    monkeypatch.setitem(sys.modules, "pynq", pynq)
    monkeypatch.setattr(v, "make_references", lambda *a: tmp_path)
    def layers(np_, backend, ip, *args):
        ip.starts += 1  # mock-only evidence; no physical kernel
        return {"mock": True}
    monkeypatch.setattr(v, "layer_checks", layers)
    monkeypatch.setattr(v, "regression", lambda *a: {"mock": True})
    assert v.main(argv + ["--allow-pl", "--reuse-programmed-overlay", "--recording", str(root / "ps/runtime.py"), "--expected-frames", "1", "--stage", "correctness"]) == exit_code
    report = json.loads((tmp_path / "result/report.json").read_text(encoding="utf8"))
    assert clock.reads == 2
    assert [r["checkpoint"] for r in report["clock_checks"]] == ["before_backend_and_first_start", "endcheck"]
    assert report["clock_checks"][1]["initial_validated_clock_mhz"] == 100
    assert len(backends) == 1 and backends[0].closed
    assert report["compute_executed"] is True
    if exit_code:
        assert report["failure_mode"] == "failed_clock_mismatch"
    assert not raw.regs and not raw.buffers


@pytest.mark.parametrize("case", ["150_to_100", "already100", "postfail", "busy", "hash", "not150"])
def test_main_retest_single_field_or_fail_before_backend(tmp_path, monkeypatch, case):
    from types import SimpleNamespace, ModuleType
    root, argv = deployment(tmp_path)
    monkeypatch.setattr(v.platform, "system", lambda: "Linux")
    monkeypatch.setattr(v.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(v.os, "geteuid", lambda: 0, raising=False)
    record = SimpleNamespace(left=root / "ps/runtime.py", front=root / "ps/runtime.py", right=root / "ps/runtime.py")
    constructed, clock_writes, overlay_calls = [], [], []
    class Backend:
        def __init__(self, ip, regs, timeout_s):
            constructed.append(self)
        def close(self):
            pass
    monkeypatch.setattr(v, "load_project", lambda r: (np, None, lambda paths: [record], None, Backend, lambda r: None))
    state = {"ctrl": 4, "pl_raw": [0x1010f00 if case == "already100" else 0x1010a00, 1, 2, 3],
             "pll_raw": {"IOPLL_CTRL": 0x15a00, "RPLL_CTRL": 1, "APLL_CTRL": 2, "DPLL_CTRL": 3, "VPLL_CTRL": 4},
             "fclk0_mhz": 100. if case == "already100" else 150., "source_mhz": 1500.}
    class Field:
        @property
        def DIVISOR0(self):
            return state["pl_raw"][0] >> 8 & 63
        @DIVISOR0.setter
        def DIVISOR0(self, value):
            assert value == 15
            clock_writes.append(("DIVISOR0", value))
            state["pl_raw"][0] = 0x1010f00
            state["fclk0_mhz"] = 100.
            if case == "postfail":
                state["pl_raw"][1] ^= 1
    class Clocks:
        _instance = SimpleNamespace(PL_CLK_CTRLS=[Field()])
        @property
        def fclk0_mhz(self):
            return state["fclk0_mhz"]
        @fclk0_mhz.setter
        def fclk0_mhz(self, value):
            pytest.fail("fclk0 setter forbidden")
        def set_pl_clk(self, *a, **kw):
            pytest.fail("set_pl_clk forbidden")
    import copy
    monkeypatch.setattr(v, "read_clock_snapshot", lambda clocks, ip: {**copy.deepcopy(state), "ctrl": int(ip.read(0))})
    raw = MockIP()
    if case == "busy":
        raw.status = 0x85
    if case == "not150":
        state["fclk0_mhz"] = 125.
    overlay = SimpleNamespace(ip_dict={"ffn_accel_0": {"registers": metadata()}}, ffn_accel_0=raw, clock_dict={})
    def overlay_factory(path, *, download):
        assert download is False
        overlay_calls.append(path)
        return overlay
    pynq = ModuleType("pynq")
    pynq.Overlay, pynq.Clocks = overlay_factory, Clocks()
    install_runtime_state(pynq, root, tmp_path, monkeypatch)
    monkeypatch.setitem(sys.modules, "pynq", pynq)
    if case == "hash":
        other = tmp_path / "other.bit"
        other.write_bytes(b"WRONG BIT")
        pynq.PL.bitfile_name = str(other)
    monkeypatch.setattr(v, "make_references", lambda *a: tmp_path)
    monkeypatch.setattr(v, "layer_checks", lambda *a: {"mock_only": True})
    monkeypatch.setattr(v, "regression", lambda *a: {"mock_only": True})
    result = v.main(argv + ["--allow-pl", "--reuse-programmed-overlay", "--allow-pl0-divisor0-15",
                           "--recording", str(root / "ps/runtime.py"), "--expected-frames", "1", "--stage", "correctness"])
    good = case in ("150_to_100", "already100")
    assert result == (0 if good else 1)
    assert len(constructed) == int(good)
    assert clock_writes == ([("DIVISOR0", 15)] if case in ("150_to_100", "postfail") else [])
    assert len(overlay_calls) == (0 if case == "hash" else 1)
    assert not raw.buffers and not raw.regs and raw.starts == 0
    report = json.loads((tmp_path / "result/report.json").read_text(encoding="utf8"))
    assert report["hardware_programmed"] is False
    assert report["compute_executed"] is False
    if good:
        assert report["phases"]["hardware"]["overlay_loads"] == 0
        assert report["clock_checks"][0]["actual_clock_mhz"] == 100.


def test_cpu_real_bundle_scheduling_smoke(tmp_path):
    """Real ORT golden/split and injected CPU FFN; NOT board/PL validation."""
    pytest.importorskip("onnxruntime")
    cv2 = pytest.importorskip("cv2")
    from ps.runtime import Runtime
    from ps.backends import NumpyFFN
    from ps.preprocess import preprocess_record, Record
    cv2.setNumThreads(1)
    paths = []
    rng = np.random.default_rng(17)
    for name in ("left", "front", "right"):
        path = tmp_path / f"{name}.png"
        assert cv2.imwrite(str(path), rng.integers(0, 256, (480, 640, 3), dtype=np.uint8))
        paths.append(path)
    record = Record("synthetic_cpu_only", 1, 1, tmp_path, paths[1], paths[0], paths[2], 0., 0.)
    evidence = MemoryEvidence(tmp_path)
    bundle = PROJECT / "artifacts/bundle_20261014"
    references = v.make_references(np, Runtime, bundle, [[record]], preprocess_record, 4, evidence)
    class CPUBackend:
        def __init__(self):
            self.cpu = NumpyFFN()
            self.last_timing = {}
        def forward(self, layer, x, weights):
            result = self.cpu.forward(layer, x, weights)
            self.last_timing = {"prepare_ms": 0., "hardware_wait_ms": 0., "readback_ms": 0., "total_ms": 0.}
            return result
    result = v.regression(np, Runtime, bundle, [[record]], preprocess_record, 4,
                          v.MeasuredBackend(CPUBackend()), references, evidence)
    assert result["frames_completed"] == [1]
    assert result["integer_speed_exact_match"]
    (tmp_path / "cpu_smoke.json").write_text(json.dumps({"status": "已验证", "hardware_execution": False,
                                                       "input": "synthetic images; real unchanged bundle", **result}))
