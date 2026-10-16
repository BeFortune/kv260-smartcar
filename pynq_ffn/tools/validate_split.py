from pathlib import Path
import argparse
import json
import platform
import sys

import numpy as np
import onnxruntime as ort

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ps.runtime import Runtime
from ps.backends import NumpyFFN
from ps.preprocess import preprocess_record, read_records


def validate(bundle, first_inputs=(), recordings=(), random_cases=2, threads=1, atol=1e-3):
    if random_cases < 0 or not np.isfinite(atol) or atol <= 0:
        raise ValueError("Invalid validation configuration")
    rng = np.random.default_rng(20261014)
    cases = [(f"random_{i}", rng.uniform(-.5, .5, (1, 3, 3, 66, 200)).astype(np.float32)) for i in range(random_cases)]
    for path in first_inputs:
        cases.append(("first_input:" + str(path), np.load(path, allow_pickle=False)))
    if recordings:
        for record in read_records(recordings, limit=3):
            cases.append((f"recording:{record.segment}:{record.frame_index}", preprocess_record(record)))
    if not cases:
        raise ValueError("No validation inputs")
    runtimes = {mode: Runtime(bundle, mode=mode, threads=threads) for mode in ("golden", "split-ort", "split-numpy")}
    result = {"status": "pass", "hardware_execution": False, "timing_scope": "Host CPU only; not KV260 measurements; includes no initialization or warmup",
              "python": platform.python_version(), "machine": platform.machine(), "numpy": np.__version__, "onnxruntime": ort.__version__,
              "threads": threads, "atol": atol, "rtol": 0, "cases": []}
    for name, x in cases:
        outputs = {mode: runtime.run(x) for mode, runtime in runtimes.items()}
        case = {"name": name, "comparisons": {}, "timings_ms": {mode: dict(r.last_timings_ms) for mode, r in runtimes.items()}}
        for mode in ("split-ort", "split-numpy"):
            errors = {key: float(np.max(np.abs(outputs[mode][key]-outputs["golden"][key]))) for key in outputs["golden"]}
            integer_mismatches = int(np.count_nonzero(np.trunc(outputs[mode]["speed_raw"]) != np.trunc(outputs["golden"]["speed_raw"])))
            floats_pass = all(np.isfinite(error) and error <= atol for error in errors.values())
            passed = floats_pass and integer_mismatches == 0
            case["comparisons"][mode] = {"max_abs_error": errors, "floats_pass": floats_pass,
                                            "pass": passed, "speed_integer_mismatches": integer_mismatches}
            if not passed:
                result["status"] = "failed"
        case["ffn_pipeline_numpy_vs_ort_max_abs"] = {key: float(np.max(np.abs(runtimes["split-numpy"].last_ffn_outputs[key]-runtimes["split-ort"].last_ffn_outputs[key])))
                                                      for key in runtimes["split-ort"].last_ffn_outputs}
        case["ffn_pipeline_pass"] = all(np.isfinite(error) and error <= atol
                                         for error in case["ffn_pipeline_numpy_vs_ort_max_abs"].values())
        if not case["ffn_pipeline_pass"]:
            result["status"] = "failed"
        case["ffn_same_input_numpy_vs_ort_max_abs"] = {}
        for key, value in runtimes["split-ort"].last_ffn_inputs.items():
            measured = NumpyFFN().forward(key, value, runtimes["split-ort"].weights[key])
            error = float(np.max(np.abs(measured-runtimes["split-ort"].last_ffn_outputs[key])))
            case["ffn_same_input_numpy_vs_ort_max_abs"][key] = error
            if not np.isfinite(error) or error > atol:
                result["status"] = "failed"
        result["cases"].append(case)
    return result


def main():
    parser = argparse.ArgumentParser(description="Validate CPU golden/split modes; does not test board hardware.")
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--first-input", action="append", type=Path, default=[])
    parser.add_argument("--recording", action="append", type=Path, default=[])
    parser.add_argument("--random-cases", type=int, default=2)
    parser.add_argument("--threads", type=int, choices=(1, 2, 3, 4), default=1)
    parser.add_argument("--atol", type=float, default=1e-3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Refusing an existing validation report")
    report = validate(args.bundle, args.first_input, args.recording, args.random_cases, args.threads, args.atol)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf8") as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
