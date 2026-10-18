"""Verify frozen source/config/ABI inputs without invoking any AMD or device tool."""
import hashlib
import json
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
CANDIDATE = ROOT / "hls/candidates/interleaved_r04/v2"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    snapshot = HERE.parent / "source_snapshot_v2"
    manifest = json.loads((snapshot / "input_manifest.json").read_text())
    for name, info in manifest["files"].items():
        assert digest(snapshot / name) == info["sha256"], name
        assert digest(CANDIDATE / name) == info["sha256"], name
    baseline = json.loads((HERE.parent / "baseline_manifest.json").read_text())
    for name, sha in baseline.items():
        assert digest(HERE.parent / "baseline" / name) == sha
        assert digest(ROOT / "hls" / name) == sha, "Production baseline changed: " + name
    source = (snapshot / "ffn_accel.cpp").read_text()
    old = (HERE.parent / "baseline/ffn_accel.cpp").read_text()
    interface = lambda text: re.findall(r"^#pragma HLS INTERFACE .*", text, re.MULTILINE)
    assert interface(source) == interface(old), "Source ABI directives differ"
    assert "false" not in "\n".join(s for s in source.splitlines() if "DEPENDENCE" in s)
    assert source.count("distance=8 true") == 2
    assert source.count("op=fadd impl=fulldsp latency=6") == 2
    assert source.count("op=fmul impl=maxdsp latency=3") == 2
    for scope, tb in (("full", "testbench.cpp"), ("small", "testbench_small.cpp")):
        cfg_path = snapshot / "build" / (scope + ".cfg")
        entries = dict(s.split("=", 1) for s in cfg_path.read_text().splitlines()
                       if "=" in s and not s.startswith("#"))
        assert entries["part"] == "xck26-sfvc784-2LV-c"
        assert entries["clock"] == "10"
        assert entries["syn.top"] == "ffn_accel"
        assert entries["syn.interface.m_axi_addr64"] == "true"
        assert (cfg_path.parent / entries["syn.file"]).resolve() == snapshot / "ffn_accel.cpp"
        assert (cfg_path.parent / entries["tb.file"]).resolve() == snapshot / tb
        for key in ("syn.file_cflags", "syn.file_csimflags", "tb.file_cflags"):
            assert "-ffp-contract=off" in entries[key]
            assert "-DFFN_USE_HLS_MATH" in entries[key]
            assert "fast-math" not in entries[key]
    with (HERE / "input_checks.json").open("x", encoding="utf-8") as stream:
        json.dump({"status": "pass", "production_sources_unchanged": baseline,
                   "frozen_sources_match_candidate": True, "source_interface_directives_equal": True,
                   "relative_configs_resolve": True, "hardware_execution": False}, stream, indent=2)
    print("PASS frozen hashes, unchanged production, identical source ABI, full/small relative configs")


if __name__ == "__main__":
    main()
