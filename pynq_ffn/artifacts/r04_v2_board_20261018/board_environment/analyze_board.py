"""Review completed real-board reports locally, retaining timing scope and raw evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--program", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    programmed = load(args.program / "report.json")
    require(programmed["status"] == "已验证" and programmed["hardware_programmed"], "Programming not verified")
    require(programmed["kernel_starts"] == 0, "Programming stage unexpectedly computed")
    parent = load(args.run / "report.json")
    require(parent["status"] == "已验证", "Parent run did not complete")
    results = {}
    reports = {}
    common_inputs = None
    clock_reference = programmed["after_snapshot"]
    for mode in ("golden4", "split_ort4", "split_pl4"):
        path = args.run / mode / "report.json"
        report = load(path)
        require(report["status"] == "已验证", "Mode failed: " + mode)
        require(not report.get("failure_mode"), "Mode recorded failure state")
        fingerprint = report["inputs"]["digest"]
        common_inputs = common_inputs or fingerprint
        require(fingerprint == common_inputs, "Inputs differ across modes")
        perf = report["phases"]["performance"]
        require(perf["status"] == "已验证" and perf["frames"] == 117 and perf["warmup"] == 3
                and perf["threads"] == 4, "Performance conditions differ")
        require(perf["spinning"] is (None if mode == "golden4" else False), "Spinning policy differs")
        samples = perf["raw_samples"]
        require(len(samples) == 117, "Missing timing samples")
        for metric in ("run_total_ms", "offline_e2e_ms", "ps_remaining_stage_ms", "decode_preprocess_ms"):
            values = np.asarray([row[metric] for row in samples], dtype=np.float64)
            require(np.isfinite(values).all() and (values >= 0).all(), "Invalid timing sample")
            statistics = {"mean": values.mean(), "p50": np.percentile(values, 50),
                          "p95": np.percentile(values, 95), "p99": np.percentile(values, 99), "max": values.max()}
            for key, value in statistics.items():
                require(abs(float(value) - perf[metric][key]) < 1e-8, "Timing statistics differ")
        results[mode] = {key: perf[key] for key in ("run_total_ms", "offline_e2e_ms", "ps_remaining_stage_ms",
                        "decode_preprocess_ms", "completion_interval_ms", "sustained_offline_outputs_hz",
                        "run_total_only_hz", "test_duration_s", "initialization_ms_excluded", "stage_ms")}
        results[mode].update(report_sha256=sha(path), threads=4, spinning=perf["spinning"])
        if mode == "split_pl4":
            require(report["kernel_starts_observed"] == report["expected_kernel_starts"] == 517,
                    "Real kernel START coverage differs")
            final_clock = report["final_clock_snapshot"]
            for key in ("pl_raw", "pll_raw", "cpu_raw"):
                require(final_clock[key] == clock_reference[key], "Clock changed after programming")
            require(abs(final_clock["fclk0_mhz"] - 100) <= 0.5, "Wrong board clock")
            results[mode].update({key: perf[key] for key in ("ffn_prepare_ms", "ffn_hardware_wait_ms",
                                                           "ffn_readback_ms", "ffn_total_ms")})
            results[mode].update(kernel_starts_observed=517, final_clock_snapshot=final_clock,
                                 layer_validation=report["phases"]["layers"])
        reports[mode] = report
    errors = {mode: {name: 0.0 for name in ("speed_norm", "speed_raw", "region_logits", "region_probs")}
              for mode in ("split_ort4", "split_pl4")}
    cut_errors = {name: 0.0 for name in ("camera_0", "camera_1", "cross_0", "cross_1")}
    for index in range(117):
        files = {mode: np.load(args.run / mode / "references" / f"frame_{index:03d}.npz", allow_pickle=False)
                 for mode in reports}
        try:
            for mode in errors:
                for name in errors[mode]:
                    actual, reference = files[mode]["out_" + name], files["golden4"]["out_" + name]
                    require(actual.shape == reference.shape and actual.dtype == reference.dtype,
                            "Output shape/dtype differs")
                    require(np.isfinite(actual).all() and np.isfinite(reference).all(), "Nonfinite output")
                    value = float(np.max(np.abs(actual.astype(np.float64) - reference.astype(np.float64))))
                    require(value <= 1e-3, "Four-output numerical gate failed")
                    errors[mode][name] = max(errors[mode][name], value)
                require(np.array_equal(files[mode]["out_speed_raw"].astype(np.int64),
                                       files["golden4"]["out_speed_raw"].astype(np.int64)), "Integer speeds differ")
            for name in cut_errors:
                if "ffn_" + name in files["split_pl4"].files:
                    a, b = files["split_pl4"]["ffn_" + name], files["split_ort4"]["ffn_" + name]
                    value = float(np.max(np.abs(a.astype(np.float64) - b.astype(np.float64))))
                    require(value <= 1e-3 and np.isfinite(a).all(), "Cumulative cut numerical gate failed")
                else:
                    value = float(reports["split_pl4"]["phases"]["performance"]["raw_samples"][index]
                                  ["cumulative_cut_errors_atol_1e_3_rtol_0"][name])
                    require(np.isfinite(value) and value <= 1e-3, "Recorded cumulative cut gate failed")
                cut_errors[name] = max(cut_errors[name], value)
        finally:
            for archive in files.values():
                archive.close()
    result = {"status": "已验证", "scope": "Local independent review of real KV260 recorded-input run; no hardware action",
              "input_digest": common_inputs, "modes": results,
              "four_output_max_abs_error": errors, "speed_integer_mismatches": {mode: 0 for mode in errors},
              "cumulative_cut_max_abs_error": cut_errors,
              "cumulative_cut_scope": "Recorded per-frame comparisons unless PL cut arrays were archived",
              "pl_vs_golden_p50_ratio": results["split_pl4"]["run_total_ms"]["p50"] / results["golden4"]["run_total_ms"]["p50"],
              "pl_vs_split_ort_p50_ratio": results["split_pl4"]["run_total_ms"]["p50"] / results["split_ort4"]["run_total_ms"]["p50"],
              "limitations": ["Recorded inputs, not real-time camera/frame freshness or vehicle", "Wall throughput/intervals include validation and evidence I/O", "No 15Hz claim or arithmetic combination of separate experiments"]}
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"status": result["status"], "output": str(args.output),
                      "pl_vs_golden_p50_ratio": result["pl_vs_golden_p50_ratio"],
                      "four_output_max_abs_error": errors}, ensure_ascii=False))


if __name__ == "__main__":
    main()
