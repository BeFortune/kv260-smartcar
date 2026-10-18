"""Offline loader gates; fake modules only, never import real PYNQ."""
from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("checked_loader", HERE / "program_overlay.py")
loader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loader)


def test_clock_changes_rejected():
    original = {"pl_raw": [1, 2, 3, 4], "pll_raw": {"IOPLL": 5}, "cpu_raw": {"ACPU_CTRL": 6}}
    loader.equal_clocks(original, original.copy())
    for name in original:
        changed = dict(original, **{name: {"different": 1}})
        with pytest.raises(RuntimeError, match="changed clock"):
            loader.equal_clocks(original, changed)


def test_clock_gate_idle_and_real_frequency(monkeypatch):
    state = {"ctrl": 4, "pl_raw": [1] * 4, "pll_raw": {}, "fclk0_mhz": 99.999}
    monkeypatch.setattr(loader.gates, "read_clock_snapshot", lambda *a: dict(state))
    class Register:
        def __getitem__(self, key):
            return 3
    clocks = SimpleNamespace(_instance=SimpleNamespace(_crf_registers=SimpleNamespace(ACPU_CTRL=Register())), cpu_mhz=1333.32)
    assert loader.clock_state(clocks, object())["fclk0_mhz"] == 99.999
    state["fclk0_mhz"] = 150
    with pytest.raises(RuntimeError, match="not target"):
        loader.clock_state(clocks, object())
    state.update(ctrl=1, fclk0_mhz=100)
    with pytest.raises(RuntimeError, match="idle"):
        loader.clock_state(clocks, object())


def test_identity_requires_pinned_pair_and_no_shutdown(monkeypatch, tmp_path):
    bit = tmp_path / "old.bit"
    bit.write_bytes(b"bit")
    bit.with_suffix(".hwh").write_bytes(b"hwh")
    state = {"bitfile_name": str(bit), "timestamp": "old", "shutdown_ips": {}}
    loader.verify_identity(state, str(bit), "old", loader.gates.sha(bit), loader.gates.sha(bit.with_suffix(".hwh")))
    with pytest.raises(RuntimeError, match="identity"):
        loader.verify_identity(state, "different", "old", "a", "b")
    with pytest.raises(RuntimeError, match="pinned"):
        loader.verify_identity(state, str(bit), "old", "a" * 64, "b" * 64)
    state["shutdown_ips"] = {"unknown": {}}
    with pytest.raises(RuntimeError, match="shutdown"):
        loader.verify_identity(state, str(bit), "old", loader.gates.sha(bit), loader.gates.sha(bit.with_suffix(".hwh")))


def test_firmware_target_rejects_existing_and_dangling():
    class Candidate:
        def __init__(self, exists, linked):
            self.present, self.linked = exists, linked
        def exists(self):
            return self.present
        def is_symlink(self):
            return self.linked
    loader.verify_firmware_target(Candidate(False, False))
    for present, linked in ((True, False), (True, True), (False, True)):
        with pytest.raises(RuntimeError, match="do not overwrite"):
            loader.verify_firmware_target(Candidate(present, linked))


def test_distinct_persisted_and_runtime_timestamps(tmp_path):
    bit = tmp_path / "new.bit"
    bit.write_bytes(b"newbit")
    bit.with_suffix(".hwh").write_bytes(b"newhwh")
    args = SimpleNamespace(bit_sha256=loader.gates.sha(bit), hwh_sha256=loader.gates.sha(bit.with_suffix(".hwh")))
    state = {"bitfile_name": str(bit), "timestamp": "metadata_t1", "shutdown_ips": {}}
    loader.verify_post_identity(state, str(bit), "runtime_t2", "runtime_t2", "old_t0", args)
    with pytest.raises(RuntimeError, match="programming timestamp"):
        loader.verify_post_identity(state, str(bit), "runtime_t2", "different", "old_t0", args)
    state["timestamp"] = "old_t0"
    with pytest.raises(RuntimeError, match="persistent"):
        loader.verify_post_identity(state, str(bit), "runtime_t2", "runtime_t2", "old_t0", args)


def test_exact_base_download_only():
    tree = ast.parse((HERE / "program_overlay.py").read_text(encoding="utf-8"))
    calls = [ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)]
    downloads = [name for name in calls if name.endswith(".download")]
    assert downloads == ["Bitstream.download"]
    assert not any(name.endswith((".set_pl_clk", ".allocate", ".reset")) for name in calls)
    assert "pynq" not in sys.modules


def test_program_order_uses_no_overlay_download(monkeypatch, tmp_path):
    monkeypatch.setenv("XILINX_XRT", "/usr")
    monkeypatch.delenv("XCL_EMULATION_MODE", raising=False)
    monkeypatch.setattr(loader.validation, "require_target", lambda: None)
    monkeypatch.setattr(loader, "verify_loader", lambda inspection: None)
    monkeypatch.setattr(loader, "verify_identity", lambda *a: None)
    monkeypatch.setattr(loader.gates, "hwh_registers", lambda *a: {})
    monkeypatch.setattr(loader.signal, "signal", lambda *a: None)
    before = {"ctrl": 4, "pl_raw": [1] * 4, "pll_raw": {}, "cpu_raw": {"ACPU_CTRL": 3}, "fclk0_mhz": 100}
    monkeypatch.setattr(loader, "clock_state", lambda *a: dict(before))
    (tmp_path / "global_pl_state.json").write_text(json.dumps({"bitfile_name": "/old/old.bit", "timestamp": "old"}))
    bit = tmp_path / "new_unique_r04v2.bit"
    bit.write_bytes(b"new")
    events = []
    pl = SimpleNamespace(bitfile_name="/old/old.bit", timestamp="old")

    class Overlay:
        def __init__(self, path, *, download):
            assert download is False
            events.append(("metadata", path))
            self.bitfile_name = path
            self.dtbo, self.partial = None, False
            self.parser, self.clock_dict = object(), {0: {"enable": 1, "divisor0": 10}}
            self.ip_dict = {"ffn_accel_0": {"phys_addr": 0xA0000000}}
            self.ffn_accel_0, self.timestamp = object(), ""

        def download(self, *a):
            raise AssertionError("Overlay clock-setting download must not be called")

        def is_loaded(self):
            return self.timestamp == pl.timestamp

    class Bitstream:
        @staticmethod
        def download(overlay, parser):
            assert parser is overlay.parser
            events.append(("base_download", overlay.bitfile_name))
            overlay.timestamp, pl.timestamp, pl.bitfile_name = "new", "new", overlay.bitfile_name
            (tmp_path / "global_pl_state.json").write_text(json.dumps({"bitfile_name": pl.bitfile_name, "timestamp": "persisted_before_download"}))

    fake = SimpleNamespace(Bitstream=Bitstream, Clocks=object(), Overlay=Overlay, PL=pl)
    monkeypatch.setitem(sys.modules, "pynq", fake)
    monkeypatch.setitem(sys.modules, "pynq.pl_server", SimpleNamespace(global_state=SimpleNamespace(STATE_DIR=str(tmp_path))))
    monkeypatch.setitem(sys.modules, "pynq._3rdparty", SimpleNamespace(xrt=SimpleNamespace(XRT_SUPPORTED=True, XRT_EMULATION=False)))
    args = SimpleNamespace(ip_name="ffn_accel_0", bit_sha256="newbit", hwh_sha256="newhwh")
    inspection = {"runtime_state_sha256": loader.gates.sha(tmp_path / "global_pl_state.json"), "snapshot": before}
    report = {}
    result = loader.program_checked_overlay(args, bit, {}, inspection,
                                            lambda kind, data: events.append((kind, data)), report)
    assert result["status"] == "已验证" and result["kernel_starts"] == 0
    assert report["hardware_programmed"] is True
    assert result["runtime_state_after"]["timestamp"] != result["programming_runtime_timestamp"]
    assert [row[0] for row in events].count("base_download") == 1
    assert next(i for i, row in enumerate(events) if row[0] == "before_programming") < next(
        i for i, row in enumerate(events) if row[0] == "base_download")
