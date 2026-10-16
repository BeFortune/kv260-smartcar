"""Verify the delivered pair, critical reports, software gates and protected sources."""

import datetime
import hashlib
import json
from pathlib import Path
import zipfile

WORK = Path(__file__).resolve().parent
REPO = Path("D:/Learning/FPGA/pynq_ffn")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    expected_codes = {
        "preflight/vivado_part": 0,
        "hls/make_config": 0, "hls/csim": 0, "hls/csynth": 0,
        "hls/rtl_abi_check": 0, "hls/cosim_small": 1,
        "hls/cosim_smoke": 0, "hls/impl": 0, "hls/ip_package_check": 0,
        "vivado/create_bd": 1, "vivado/create_bd_attempt02": 1,
        "vivado/create_bd_attempt03": 0, "vivado/synthesis": 0,
        "vivado/implementation": 0, "vivado/export_bit_xsa": 1,
        "vivado/finalize_bit_xsa": 0,
        "software/hwh_abi_check": 1, "software/hwh_abi_check_attempt02": 0,
        "software/pynq_backend_final_tests": 0,
        "software/notebook_cpu_validation": 0,
    }
    stages = []
    for name, expected in expected_codes.items():
        path = WORK / "evidence" / (name + ".command.json")
        record = json.loads(path.read_text(encoding="utf-8"))
        assert record["status"] == "completed" and record["exit_code"] == expected, name
        stages.append({"stage": name, "exit_code": expected,
                       "started": record["started"], "finished": record["finished"]})
    source_manifest = json.loads((WORK / "evidence/source_manifest.json").read_text(encoding="utf-8"))
    for row in source_manifest["source_files"]:
        assert digest(Path(row["source"])) == digest(Path(row["copied_to"])) == row["sha256"]
    hwh = json.loads((WORK / "evidence/software/hwh_abi_check.json").read_text(encoding="utf-8"))
    assert hwh["passed"] and hwh["bit_and_xsa_embedded_bit_identical"]
    assert hwh["full_ddr_low_high_mappings_verified"] and hwh["driver_validate_registers_passed"]
    delivery = REPO / "artifacts/vivado_20261015/retry04/overlay"
    manifest = json.loads((delivery / "manifest.json").read_text(encoding="utf-8"))
    for row in manifest["files"]:
        path = delivery / row["name"]
        assert path.stat().st_size == row["bytes"] and digest(path) == row["sha256"]
    with zipfile.ZipFile(delivery / "ffn_overlay_wrapper.xsa") as archive:
        assert archive.read("ffn_overlay_wrapper.bit") == (delivery / "ffn_overlay_wrapper.bit").read_bytes()
        assert archive.read("ffn_overlay.hwh") == (delivery / "ffn_overlay_wrapper.hwh").read_bytes()
    timing = (WORK / "evidence/vivado/implemented_timing_summary.rpt").read_text(encoding="utf-8")
    assert "All user specified timing constraints are met." in timing
    assert "unconstrained_internal_endpoints (0)" in timing
    gate = dict(line.split("=", 1) for line in (WORK / "evidence/vivado/timing_gate.txt").read_text().splitlines())
    assert float(gate["WNS_NS"]) == 2.338 and float(gate["WHS_NS"]) == 0.010
    assert "C/RTL co-simulation finished: PASS" in (WORK / "evidence/hls/cosim_smoke.log").read_text(encoding="utf-8", errors="replace")
    notebook = json.loads((REPO / "notebooks/01_ps_to_pl.ipynb").read_text(encoding="utf-8"))
    text = "\n".join("".join(cell.get("source", [])) for cell in notebook["cells"])
    assert "ALLOW_PL_DOWNLOAD = False" in text and "ALLOW_PL_DOWNLOAD = True" not in text
    assert "ffn_overlay_wrapper.bit" in text and "IP_NAME = 'ffn_accel_0'" in text
    cpu = json.loads((WORK / "evidence/software/notebook_cpu_validation.json").read_text(encoding="utf-8"))
    assert cpu["passed"] and not cpu["hardware_execution"] and not cpu["pynq_imported"]
    assert "20 passed" in (WORK / "evidence/software/pynq_backend_final_tests.log").read_text(encoding="utf-8")
    report = {
        "passed": True,
        "verified_at": datetime.datetime.now().astimezone().isoformat(),
        "attempt": "retry04", "stages": stages,
        "matching_pair_verified": True,
        "implemented_timing_passed": True,
        "wns_ns": 2.338, "whs_ns": 0.010, "tns_ns": 0.0,
        "protected_hls_sources_unchanged": True,
        "hwh_abi_addr64_and_ddr_mappings_passed": True,
        "notebook_download_disabled": True,
        "mocked_driver_tests_passed": 20,
        "cpu_only_notebook_cells_passed": 6,
        "cosim_scope": "one-token smoke only; 30-token attempt stopped exit 1; full RTL verification NOT performed",
        "board_execution": "not_run",
        "independent_clean_rebuild": "not_run",
        "delivery_directory": str(delivery),
        "deliverable_hashes": manifest["files"],
        "driver_sha256": digest(REPO / "ps/pynq_backend.py"),
        "notebook_sha256": digest(REPO / "notebooks/01_ps_to_pl.ipynb"),
        "validation_record_sha256": digest(REPO / "docs/04_验证记录.md"),
    }
    destination = WORK / "evidence/software/final_verification.json"
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
