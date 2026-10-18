"""Close round-two evidence locally without changing archived reports or devices."""
from __future__ import annotations

import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import xml.etree.ElementTree as ET
import zipfile

import numpy as np

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent.parent
OUTPUT = ROOT / "final_status.json"
RUNTIME_SHA = "49ace8fbf61a29e2e54efc174b45477cd08fc5fe469d13547df17326b9745576"
OLD_RUNTIME_SHA = "023327eadeaa935ec8392ec16095c6df2a1155368595afd37fe233b4cc6ed298"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def file_record(path, expected=None):
    data = path.read_bytes()
    actual = digest(data)
    require(expected is None or actual == expected, f"SHA mismatch: {path}")
    return {"path": path.relative_to(PROJECT).as_posix(), "bytes": len(data), "sha256": actual}


def check_collection(archive, directory, manifest_name, expected_count):
    with zipfile.ZipFile(archive) as package:
        require(len(package.namelist()) == len(set(package.namelist())), "Duplicate archive entries")
        raw_manifest = package.read(manifest_name)
        require(raw_manifest == (directory / manifest_name).read_bytes(), "Manifest copy differs")
        manifest = json.loads(raw_manifest)
        require(len(manifest["files"]) == expected_count, "Unexpected collection file count")
        for name, expected in manifest["files"].items():
            relative = PurePosixPath(name)
            require(not relative.is_absolute() and ".." not in relative.parts, "Unsafe collection member")
            data = package.read(name)
            require(len(data) == expected["bytes"] and digest(data) == expected["sha256"],
                    f"Archive member mismatch: {name}")
            require(data == (directory / name).read_bytes(), f"Local collected copy differs: {name}")
    return {"status": "已验证", "files_rehashed": expected_count,
            "archive_and_local_copies_equal": True,
            "manifest": (directory / manifest_name).relative_to(ROOT).as_posix()}


def junit(path):
    suites = list(ET.parse(path).getroot().iter("testsuite"))
    counts = {key: sum(int(s.get(key, "0")) for s in suites)
              for key in ("tests", "failures", "errors", "skipped")}
    require(counts["failures"] == counts["errors"] == 0, f"Failed test report: {path}")
    counts["passed"] = counts["tests"] - counts["skipped"]
    return {"status": "已验证", **counts, "evidence": file_record(path)}


def runtime_run(source):
    tree = ast.parse(source)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Runtime")
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "run")
    return ast.dump(method, include_attributes=False)


def main():
    if OUTPUT.exists():
        raise FileExistsError("Closure already recorded; preserve it and use a new version for any later run")
    report = {"status": "未执行", "experiment_date": "2026-10-18",
              "checked_at_utc": datetime.now(timezone.utc).isoformat(),
              "scope": "Local evidence verification only; no new CPU/HLS benchmark, remote command or hardware action",
              "checker": file_record(Path(__file__).resolve())}
    try:
        expected = {
            "ps/runtime.py": RUNTIME_SHA,
            "hls/ffn_accel.cpp": "829c9ec63604e327ad4ea13855b405113ea74fea6a62721d512ded6341b0b9b6",
            "hls/ffn_accel.hpp": "715f0ca11cc5e9920569654f7296c8443a0186f988557bd27ec1af3f476c7c35",
            "hls/candidates/interleaved_r04/ffn_accel.cpp": "bb7d5cdce5597b4c09c9a9505953aa5cc171ffd71f66265eb78cd1f6c063a781",
            "hls/candidates/interleaved_r04/ffn_accel.hpp": "b958fb3cf1ee318ab4fc11295835022c4565df4fc4fd13fe4bc24a5250e58eec",
            "hls/candidates/interleaved_r04/v2/ffn_accel.cpp": "553b9c9b36fee41d791908f1fc3b8314ea4631db403743f5e80665d18fad1350",
            "hls/candidates/interleaved_r04/v2/ffn_accel.hpp": "fcdced1362b460a6cea73fc5869564e6797c041469a50b9c56deba1253cd7478",
        }
        report["source_files"] = [file_record(PROJECT / name, sha) for name, sha in expected.items()]
        archives = {
            "ort/ort_thread_study_frozen.zip": "bbaab463ea213ac0ebabbea1aea6ff8c26055b5cfa3f8f6651bd6c4504b392ca",
            "ort/runtime_spin_optin_frozen.zip": "e07319c0fbe792dfa4b37ba86dc8b44e5e04e427485df9275b71992a641d7c42",
            "ort/run01_evidence.zip": "6f260235a109facb60041b566da01768ce59bca9be084ead662a607d0789078c",
            "ort/production_run01_evidence.zip": "339722ab31a1bb8ae72441484cb3026c45f3e02e626f6ce1b3a6cce6545d8974",
            "hls/interleaved_r04_hls_v1.zip": "e4c20cf9ed6c7c627767763651ecb83a3f80ffe360ed830b50a9e31e03f1c69a",
            "hls/interleaved_r04_hls_v2.zip": "fc40b31a3a73fa838ac6d9be338be5a51f4881313353d50e6806b90ebb8c0ab5",
            "hls/hls_round2_final.zip": "2dbb3e0901c5f17f07cf3ea7dc640a5424457f21981dd8497d5a590e3c301a83",
            "hls/remote_final/v2_ooc/work/ffn_accel.zip": "3e7eb615f7fc0ecf61ad5e56443eab6f4665e698045cef2565a4990e340e4185",
        }
        report["archives"] = [file_record(ROOT / name, sha) for name, sha in archives.items()]
        report["collections"] = {
            "ort_study": check_collection(ROOT / "ort/run01_evidence.zip", ROOT / "ort/remote_run01",
                                          "manifest.json", 144),
            "ort_production": check_collection(ROOT / "ort/production_run01_evidence.zip",
                                               ROOT / "ort/remote_production_run01", "manifest.json", 21),
            "hls": check_collection(ROOT / "hls/hls_round2_final.zip", ROOT / "hls/remote_final",
                                    "collection_manifest.json", 191),
        }
        freeze = load(ROOT / "ort/production_deployment_freeze.json")
        report["production_freeze"] = [file_record(PROJECT / name, sha) for name, sha in freeze["files"].items()]
        with zipfile.ZipFile(ROOT / "ort/ort_thread_study_frozen.zip") as old:
            old_source = old.read("ps/runtime.py")
        with zipfile.ZipFile(ROOT / "ort/runtime_spin_optin_frozen.zip") as new:
            new_source = new.read("ps/runtime.py")
        require(digest(old_source) == OLD_RUNTIME_SHA and digest(new_source) == RUNTIME_SHA,
                "Runtime frozen version mismatch")
        require(runtime_run(old_source) == runtime_run(new_source), "Runtime.run AST changed")
        report["runtime_policy"] = {"status": "已验证", "runtime_sha256": RUNTIME_SHA,
            "default_spinning": None, "explicit_false": "both allow_spinning keys are 0",
            "runtime_run_ast_unchanged": True, "old_study_freeze_not_relaxed": True}

        comparison = load(ROOT / "ort/comparison_report.json")
        study = load(ROOT / "ort/remote_run01/run01/study.json")
        require(study["status"] == "已验证" and study["exit_code"] == 0, "ORT study not successful")
        performance = sorted((ROOT / "ort/remote_run01/run01").glob("perf_*/report.json"))
        require(len(performance) == 12, "Missing formal ORT conditions")
        for path in performance:
            result = load(path)
            require(result["n"] == 117 and not result["profiling"] and result["comparison"]["pass"],
                    f"Formal ORT condition failed: {path}")
        report["ort_controlled_study"] = {"status": "已验证", "formal_conditions": 12,
            "frames_each": 117, "warmup_each": 3, "forward_and_reverse_orders": True,
            "split4_p50_on_ms": comparison["split4_p50_on_ms"],
            "split4_p50_off_ms": comparison["split4_p50_off_ms"],
            "p50_ratio": comparison["p50_ratio"], "thread_count_on_off": comparison["thread_count_on_off"],
            "conclusion": "Spinning contention is a major removable overhead on this four-core multi-session setup; not a unique-root-cause claim"}
        run = ROOT / "ort/remote_production_run01/production_run01"
        validation = load(run / "validation.json")
        require(validation["status"] == "已验证" and validation["exit_code"] == 0
                and validation["comparison"]["pass"], "Production validation failed")
        production = {}
        arrays = {}
        for name in ("golden4", "split4_spinning_off"):
            result = load(run / name / "report.json")
            process = load(run / name / "process.json")
            require(result["status"] == "已验证" and result["exit_code"] == process["exit_code"] == 0,
                    f"Production process failed: {name}")
            require(result["versions"]["runtime_sha256"] == RUNTIME_SHA
                    and result["n"] == 117 and result["warmup"] == 3, "Production version/coverage changed")
            samples = np.array([s["run_total_ms"] for s in result["samples"]], dtype=np.float64)
            require(len(samples) == 117, "Production raw sample count changed")
            stats = {"mean": float(samples.mean()), "p50": float(np.percentile(samples, 50)),
                     "p95": float(np.percentile(samples, 95)), "p99": float(np.percentile(samples, 99)),
                     "max": float(samples.max())}
            for key, value in stats.items():
                require(abs(value - result["run_total_ms"][key]) < 1e-9, f"Statistics mismatch: {name}/{key}")
            production[name] = {"status": "已验证", "n": 117, "warmup": 3,
                "threads": result["threads"], "spinning": result["spinning"], "run_total_ms": stats}
            with np.load(run / name / "outputs.npz", allow_pickle=False) as outputs:
                arrays[name] = {key: outputs[key].copy() for key in outputs.files}
        require(set(arrays["golden4"]) == set(arrays["split4_spinning_off"]), "Output names differ")
        errors = {}
        for name, reference in arrays["golden4"].items():
            candidate = arrays["split4_spinning_off"][name]
            require(reference.shape == candidate.shape and reference.dtype == candidate.dtype
                    and np.isfinite(reference).all() and np.isfinite(candidate).all(), "Invalid outputs")
            errors[name] = float(np.abs(reference.astype(np.float64) - candidate).max())
            require(np.array_equal(reference, candidate), f"Production outputs not exact: {name}")
        integers_equal = np.array_equal(arrays["golden4"]["speed_raw"].astype(np.int64),
                                        arrays["split4_spinning_off"]["speed_raw"].astype(np.int64))
        require(integers_equal, "Production integer speeds differ")
        report["ort_production"] = {"status": "已验证", "location": "KV260 CPU; no PL execution",
            **production, "max_abs_error": errors, "speed_integer_mismatches": 0,
            "production_same_round_spin_on_pair": "未执行",
            "timing_scope": "Runtime.run incl. FFN diagnostics; preloaded input; decoding/preprocessing/initialization excluded"}

        remote = ROOT / "hls/remote_final"
        stages = {}
        for variant, names in (("v2", ("csim", "csynth", "cosim")), ("v2_ooc", ("csynth", "impl"))):
            for name in names:
                path = remote / variant / "records_v1" / f"{name}.finished.json"
                result = load(path)
                require(result["status"] == "已验证" and result["exit_code"] == 0, f"HLS stage failed: {path}")
                stages[f"{variant}/{name}"] = {"status": "已验证", "exit_code": 0,
                    "elapsed_s": result["elapsed_s"], "evidence": file_record(path)}
        summary = load(ROOT / "hls/v2/remote_summary.json")
        require(summary["source_sha256"] == expected["hls/candidates/interleaved_r04/v2/ffn_accel.cpp"],
                "HLS summary source changed")
        xml = ET.parse(remote / "v2/work/hls/syn/report/ffn_accel_csynth.xml").getroot()
        usage = {child.tag: int(child.text) for child in xml.find("AreaEstimates/Resources")}
        available = {child.tag: int(child.text) for child in xml.find("AreaEstimates/AvailableResources")}
        old_record = load(remote / "v2/records_v1/csynth.finished.json")["report_summary"]
        require(all(int(old_record[k]) == available[k] for k in available), "Runner erratum no longer matches raw record")
        require(usage == {"BRAM_18K": 142, "DSP": 333, "FF": 64719, "LUT": 58860, "URAM": 0},
                "Unexpected XML Resources values")
        report["hls_candidate"] = {"status": "已验证", "validation_scope": "Host, HLS full C, small RTL and standalone OOC only",
            "v1_target_timing": "失败", "stages": stages,
            "source_sha256": summary["source_sha256"], "csim": summary["csim"],
            "csynth": summary["csynth"], "operators": {"fadd": 16, "fmul": 16,
                "scope": "FC top-level instances shared across FC stages; nested GELU excluded"},
            "rtl_cosim_small": summary["rtl_cosim_small"],
            "ooc_implementation": summary["ooc_implementation"], "ip_archive": summary["ip_archive"],
            "full_rtl": "未执行", "new_full_system_bit_hwh_xsa": "未执行", "new_candidate_board_run": "未执行"}
        report["resource_erratum"] = {"status": "已验证", "evidence": "closure_notes.md",
            "raw_runner_summary_used_available_not_usage": True, "raw_records_unchanged": True,
            "xml_resources": usage, "xml_available_resources": available,
            "csynth_rpt_top_lut": 58866, "lut_xml_vs_text_difference": 6,
            "difference_cause": "未执行", "future_runner_fix": "未执行"}
        report["software_tests"] = {"current": junit(ROOT / "final_software_tests.xml"),
            "old_frozen_experiment_root": junit(ROOT / "ort/frozen27_recheck2.xml"),
            "skip_explanation": "Five old-runtime integration cases explicitly skipped in production tree; old frozen root separately passed all 27"}
        require(report["software_tests"]["current"]["passed"] == 92
                and report["software_tests"]["current"]["skipped"] == 5
                and report["software_tests"]["old_frozen_experiment_root"]["passed"] == 27,
                "Unexpected final test coverage")
        report["limitations"] = [
            "inputs.npy was not collected; its bytes were not rehashed locally",
            "No combining the ORT 5.3x controlled result with the HLS 3.25x static cycle ratio",
            "Standalone OOC lacks full external I/O delays and is not PS+PL system sign-off",
            "No new overlay download, PL start, register/clock change, camera, serial or vehicle action during closure",
            "No 15 Hz new-image inference or vehicle performance claim",
        ]
        report["status"] = "已验证"
    except Exception as error:
        report["status"] = "失败"
        report["first_error"] = f"{type(error).__name__}: {error}"
    report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    with OUTPUT.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"status": report["status"], "output": str(OUTPUT),
                      "first_error": report.get("first_error")}, ensure_ascii=False))
    return 0 if report["status"] == "已验证" else 1


if __name__ == "__main__":
    sys.exit(main())
