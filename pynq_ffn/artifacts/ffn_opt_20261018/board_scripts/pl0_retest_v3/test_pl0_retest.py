"""Offline-only PL0 gate mocks. No imports of the PYNQ package."""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("pl0_v3", HERE / "board_validate.py")
v = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)


def snapshot(div0=10):
    return {"ctrl": 4, "pl_raw": [0x01010000 | div0 << 8, 0x1011000, 0, 0],
            "pll_raw": {"IOPLL_CTRL": 0x15a00, "RPLL_CTRL": 1,
                        "APLL_CTRL": 2, "DPLL_CTRL": 3, "VPLL_CTRL": 4},
            "fclk0_mhz": 150. if div0 == 10 else 100., "source_mhz": 1500.}


@pytest.mark.parametrize("div0", [10, 15])
def test_pure_before_allowed_states(div0):
    assert v.check_pl0_before(snapshot(div0)) == div0


@pytest.mark.parametrize("field,value", [("ctrl", 0), ("ctrl", 5), ("ctrl", 0x84),
                                           ("fclk0_mhz", 125.), ("fclk0_mhz", float("nan")),
                                           ("source_mhz", 1200.)])
def test_pure_before_refuses_busy_and_wrong_rates(field, value):
    state = snapshot()
    state[field] = value
    with pytest.raises(RuntimeError):
        v.check_pl0_before(state)


@pytest.mark.parametrize("raw", [0x01010b00, 0x01020a00, 0x01010a01, 0x00010a00])
def test_pure_before_refuses_wrong_raw(raw):
    state = snapshot()
    state["pl_raw"][0] = raw
    with pytest.raises(RuntimeError):
        v.check_pl0_before(state)


class FieldRegister:
    def __init__(self, state, writes, corrupt_after=False):
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "writes", writes)
        object.__setattr__(self, "corrupt_after", corrupt_after)

    def __setattr__(self, field, value):
        assert field == "DIVISOR0", "Only DIVISOR0 field writes allowed"
        assert value == 15
        self.writes.append((field, value))
        self.state["pl_raw"][0] = (self.state["pl_raw"][0] & ~v.PL0_DIV0_MASK) | value << 8
        self.state["fclk0_mhz"] = 100.
        if self.corrupt_after:
            self.state["pl_raw"][2] ^= 1


def mock_change(tmp_path, state=None, corrupt_after=False):
    from types import SimpleNamespace
    state = snapshot() if state is None else state
    writes = []
    class Clocks:
        _instance = SimpleNamespace(PL_CLK_CTRLS=[FieldRegister(state, writes, corrupt_after)])
        def set_pl_clk(self, *a, **kw):
            pytest.fail("set_pl_clk forbidden")
        @property
        def fclk0_mhz(self):
            return state["fclk0_mhz"]
        @fclk0_mhz.setter
        def fclk0_mhz(self, value):
            pytest.fail("fclk0 setter forbidden")
    class IP:
        def read(self, offset):
            assert offset == 0
            return state["ctrl"]
        def write(self, *a):
            pytest.fail("No IP writes before clock guard")
    evidence = v.Evidence(tmp_path / "evidence", SimpleNamespace(mock_only=True))
    return Clocks(), IP(), lambda c, i: copy.deepcopy(state), evidence, writes


@pytest.mark.parametrize("div0,expected_writes", [(10, [("DIVISOR0", 15)]), (15, [])])
def test_mock_only_div0_write_or_already100_no_write(tmp_path, div0, expected_writes):
    clocks, ip, read_snapshot, evidence, writes = mock_change(tmp_path, snapshot(div0))
    assert v.guarded_div0_change(clocks, ip, read_snapshot, evidence) == 100.
    assert writes == expected_writes
    row = evidence.report["pl0_divisor_change"]
    assert row["status"] == "已验证"
    assert row["before_sha256"] == v.snapshot_digest(row["before"])
    assert row["after_sha256"] == v.snapshot_digest(row["after"])


def test_mock_failed_after_does_not_rollback(tmp_path):
    clocks, ip, read_snapshot, evidence, writes = mock_change(tmp_path, corrupt_after=True)
    with pytest.raises(RuntimeError, match="another PL clock"):
        v.guarded_div0_change(clocks, ip, read_snapshot, evidence)
    assert writes == [("DIVISOR0", 15)]
    assert not evidence.report["pl0_divisor_change"]["automatic_rollback"]
    assert evidence.report["pl0_divisor_change"]["status"] == "失败"


@pytest.mark.parametrize("mutation", ["busy", "raw", "rate", "pll"])
def test_mock_refused_before_never_writes(tmp_path, mutation):
    state = snapshot()
    if mutation == "busy":
        state["ctrl"] = 0x85
    elif mutation == "raw":
        state["pl_raw"][0] ^= 1
    elif mutation == "rate":
        state["fclk0_mhz"] = 125.
    else:
        state["pll_raw"]["IOPLL_CTRL"] = 0
    clocks, ip, read_snapshot, evidence, writes = mock_change(tmp_path, state)
    with pytest.raises(RuntimeError):
        v.guarded_div0_change(clocks, ip, read_snapshot, evidence)
    assert writes == []


def test_snapshot_uses_confirmed_register_paths_and_is_readonly():
    from types import SimpleNamespace
    state = snapshot()
    reads = []
    class Register:
        def __init__(self, name, value):
            object.__setattr__(self, "name", name)
            object.__setattr__(self, "value", value)
        def __getitem__(self, key):
            assert key == slice(None)
            reads.append(self.name)
            return self.value
        def __setattr__(self, field, value):
            pytest.fail("Readonly snapshot cannot write any register fields")
    pll = {k: Register(k, value) for k, value in state["pll_raw"].items()}
    crl = SimpleNamespace(**{k: pll[k] for k in ("IOPLL_CTRL", "RPLL_CTRL")})
    crf = SimpleNamespace(**{k: pll[k] for k in ("APLL_CTRL", "DPLL_CTRL", "VPLL_CTRL")})
    def pll_mhz(reg):
        assert reg is crl.IOPLL_CTRL
        return 1500.
    clocks = SimpleNamespace(_instance=SimpleNamespace(_crl_registers=crl, _crf_registers=crf,
                             PL_CLK_CTRLS=[Register(str(i), raw) for i, raw in enumerate(state["pl_raw"])],
                             get_pll_mhz=pll_mhz), fclk0_mhz=150.)
    ip = SimpleNamespace(read=lambda offset: 4)
    assert v.read_clock_snapshot(clocks, ip) == state
    assert set(reads) == set(state["pll_raw"]) | {"0", "1", "2", "3"}


def test_no_programming_or_general_clock_setter_in_production_source():
    import ast
    tree = ast.parse((HERE / "board_validate.py").read_text(encoding="utf8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in ("download", "set_pl_clk", "load_global_state", "reset")
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute) and target.attr in (
                    "DIVISOR0", "DIVISOR1", "SRCSEL", "SRC_FIELD", "CLKACT", "fclk0_mhz"):
                    assert target.attr == "DIVISOR0"
                    assert isinstance(node.value, ast.Constant) and node.value.value == 15


def test_loaded_file_same_content_different_deployment_path(tmp_path):
    old, new = tmp_path / "old.bit", tmp_path / "new.bit"
    old.write_bytes(b"MOCK BIT CONTENT")
    new.write_bytes(old.read_bytes())
    row = v.verify_loaded_bitfile(str(old), v.sha(new))
    assert row["file_sha256"] == v.sha(new)
    assert "NOT FPGA configuration" in row["authority"]


@pytest.mark.parametrize("mode", ["wrong_hash", "missing", "empty", "directory"])
def test_loaded_file_refuses_untrusted_or_unreadable(tmp_path, mode):
    bit = tmp_path / "old.bit"
    bit.write_bytes(b"MOCK")
    value = {"wrong_hash": str(bit), "missing": str(bit) + "missing",
             "empty": "", "directory": str(tmp_path)}[mode]
    with pytest.raises((RuntimeError, OSError)):
        v.verify_loaded_bitfile(value, "0" * 64)


def test_pure_before_refuses_wrong_iopll():
    state = snapshot()
    state["pll_raw"]["IOPLL_CTRL"] ^= 1
    with pytest.raises(RuntimeError, match="IOPLL raw"):
        v.check_pl0_before(state)


def test_pure_after_only_div0_and_digests():
    before, after = snapshot(), snapshot(15)
    v.check_pl0_after(before, after)
    assert before["pl_raw"][0] ^ after["pl_raw"][0] == 5 << 8
    assert v.snapshot_digest(before) != v.snapshot_digest(after)
    assert v.snapshot_digest(before) == v.snapshot_digest(copy.deepcopy(before))
    v.check_pl0_after(after, after)


@pytest.mark.parametrize("mutation", ["div1", "reserved", "pl1", "pll", "busy", "wrong_rate"])
def test_pure_after_refuses_any_other_change(mutation):
    before, after = snapshot(), snapshot(15)
    if mutation == "div1":
        after["pl_raw"][0] ^= 1 << 16
    elif mutation == "reserved":
        after["pl_raw"][0] ^= 1 << 28
    elif mutation == "pl1":
        after["pl_raw"][1] ^= 1
    elif mutation == "pll":
        after["pll_raw"]["RPLL_CTRL"] ^= 1
    elif mutation == "busy":
        after["ctrl"] = 0
    else:
        after["fclk0_mhz"] = 150.
    with pytest.raises(RuntimeError):
        v.check_pl0_after(before, after)
