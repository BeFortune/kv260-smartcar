"""Finalize local evidence for the completed r04v2 board run; no device access."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROJECT = ROOT.parent.parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    output = ROOT / "final_status.json"
    if output.exists():
        raise FileExistsError(output)
    archive = ROOT / "board_evidence01.zip"
    expected_archive = "f0ef8ff44db105af1e0ad8085ab7c04d7184a1f10d79d51c9a928b71d63348e9"
    require(sha(archive) == expected_archive, "Board archive SHA mismatch")
    collected = ROOT / "collected_board01"
    collection = load(collected / "manifest.json")
    require(len(collection["files"]) == 385, "Board collection file count changed")
    for name, spec in collection["files"].items():
        path = collected / name
        require(path.stat().st_size == spec["bytes"] and sha(path) == spec["sha256"],
                "Collected board file changed: " + name)
    system = ROOT / "system_build"
    delivery = load(system / "delivery_manifest.json")
    require(len(delivery["files"]) == 109, "System collection file count changed")
    for name, spec in delivery["files"].items():
        path = system / name
        require(path.stat().st_size == spec["bytes"] and sha(path) == spec["sha256"],
                "Collected system file changed: " + name)
    build = load(system / "system_acceptance_report.json")
    require(build["status"] == "已验证", "System review failed")
    require(all(record["exit_code"] == 0 for record in build["stage_records"].values()), "System stage failed")
    source_hashes = {
        "ps/runtime.py": "49ace8fbf61a29e2e54efc174b45477cd08fc5fe469d13547df17326b9745576",
        "hls/ffn_accel.cpp": "829c9ec63604e327ad4ea13855b405113ea74fea6a62721d512ded6341b0b9b6",
        "hls/ffn_accel.hpp": "715f0ca11cc5e9920569654f7296c8443a0186f988557bd27ec1af3f476c7c35",
        "hls/candidates/interleaved_r04/v2/ffn_accel.cpp": "553b9c9b36fee41d791908f1fc3b8314ea4631db403743f5e80665d18fad1350",
        "hls/candidates/interleaved_r04/v2/ffn_accel.hpp": "fcdced1362b460a6cea73fc5869564e6797c041469a50b9c56deba1253cd7478",
    }
    for name, expected in source_hashes.items():
        require(sha(PROJECT / name) == expected, "Source freeze changed: " + name)
    analysis = load(ROOT / "board_analysis01.json")
    require(analysis["status"] == "已验证", "Board independent analysis failed")
    original_program = load(collected / "supplement/program01/report.json")
    verified_program = load(collected / "programming/report.json")
    require(original_program["status"] == "失败" and original_program["hardware_programmed"]
            and original_program["kernel_starts"] == 0, "Original warning-bearing programming record changed")
    require(verified_program["status"] == "已验证" and verified_program["kernel_starts"] == 0,
            "Independent post-program verification failed")
    run = collected / "board_run"
    require(load(run / "report.json")["status"] == "已验证", "Parent board run failed")
    pl = load(run / "split_pl4/report.json")
    require(pl["status"] == "已验证" and pl["hardware_execution"]
            and pl["kernel_starts_observed"] == pl["expected_kernel_starts"] == 517,
            "PL compute coverage failed")
    events = [json.loads(line) for line in (run / "split_pl4/events.jsonl").read_text(encoding="utf-8").splitlines()]
    names = [event["kind"] for event in events]
    cleanup = ("cleanup_idle", "backend_closed_after_idle", "ort_sessions_cleared")
    require(all(names.count(name) == 1 for name in cleanup), "Cleanup evidence missing or duplicated")
    require([names.index(name) for name in cleanup] == sorted(names.index(name) for name in cleanup),
            "Unsafe cleanup order")
    require(not any("quarantin" in name or name == "failure" for name in names), "PL failure event present")
    clock = pl["final_clock_snapshot"]
    require(clock["ctrl"] == 4 and abs(clock["fclk0_mhz"] - 100) <= 0.5, "Final idle/clock gate failed")
    for key in ("pl_raw", "pll_raw", "cpu_raw"):
        require(clock[key] == verified_program["after_snapshot"][key]
                == verified_program["before_snapshot"][key], "Clock register drift: " + key)
    modes = analysis["modes"]
    require(analysis["pl_vs_golden_p50_ratio"] > 1 and analysis["pl_vs_split_ort_p50_ratio"] > 1,
            "Performance conclusion differs from observed result")
    result = {
        "schema": "r04v2.board_final.v1", "status": "已验证",
        "experiment_date": "2026-10-18", "closure_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "Completed real KV260 recorded-input run; not simulation, live camera or vehicle",
        "closure_check": "Local evidence and hashes only; no new remote or hardware action",
        "evidence_integrity": {"status": "已验证", "board_archive": archive.name,
            "board_archive_sha256": expected_archive, "board_files_rehashed": 385, "system_files_rehashed": 109,
            "board_manifest_sha256": sha(collected / "manifest.json"),
            "system_delivery_manifest_sha256": sha(system / "delivery_manifest.json"),
            "independent_analysis_sha256": sha(ROOT / "board_analysis01.json")},
        "source_hashes": source_hashes,
        "artifacts": build["same_export"],
        "system_implementation": {"status": "已验证", "gates": build["gates"],
            "ps_config_comparison": build["ps_config_comparison"]},
        "programming": {"status": "已验证", "download_attempts": 1,
            "original_warning_gate_status": "失败", "original_report": "collected_board01/supplement/program01/report.json",
            "independent_verification_status": "已验证", "verification_report": "collected_board01/programming/report.json",
            "warning": verified_program["original_warning"], "repeat_downloads": 0,
            "clock_register_writes_by_scripts": 0, "rollback_executed": False},
        "real_pl_compute": {"status": "已验证", "frames": 117, "warmup": 3, "threads": 4,
            "kernel_start_writes_observed": 517, "single_layer_cases": 36, "initial_one_token_smoke": 1,
            "final_clock_snapshot": clock, "cleanup_order": list(cleanup), "timeouts": 0,
            "four_output_max_abs_error": analysis["four_output_max_abs_error"]["split_pl4"],
            "speed_integer_mismatches": 0, "cumulative_cut_max_abs_error": analysis["cumulative_cut_max_abs_error"]},
        "performance": {"status": "失败", "criterion": "PS+PL faster than same-board CPU under comparable conditions",
            "golden4_p50_ms": modes["golden4"]["run_total_ms"]["p50"],
            "split_ort4_spinning_false_p50_ms": modes["split_ort4"]["run_total_ms"]["p50"],
            "split_pl4_spinning_false_p50_ms": modes["split_pl4"]["run_total_ms"]["p50"],
            "pl_vs_golden_p50_ratio": analysis["pl_vs_golden_p50_ratio"],
            "pl_vs_split_ort_p50_ratio": analysis["pl_vs_split_ort_p50_ratio"],
            "detailed_metrics": "board_analysis01.json"},
        "last_verified_board_state": {"status": "已验证", "date_utc": pl["finished_utc"],
            "kernel": "r04v2", "ctrl": 4, "fclk0_mhz": clock["fclk0_mhz"],
            "buffers": "closed only after confirmed idle", "rollback_executed": False,
            "scope": "State at end of recorded test, not a later live re-read"},
        "not_executed": ["full 659/660-token RTL", "long-duration stability/power/temperature",
            "live camera freshness", "serial and vehicle", "15Hz live closed-loop acceptance",
            "replace formal r03 source", "refresh delivery ZIP", "automatic reset or rollback"],
        "preserved": ["r03 source and prior system artifacts", "original program01 warning failure",
            "failed helper tests and connection/environment attempts", "round-two simulation/OOC evidence"],
    }
    with output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"status": result["status"], "performance": result["performance"],
                      "evidence_files": 494, "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
