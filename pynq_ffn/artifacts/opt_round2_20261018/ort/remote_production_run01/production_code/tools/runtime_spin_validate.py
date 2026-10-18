"""Validate the production Runtime spin opt-in on CPU in isolated processes."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import traceback

import numpy as np
import onnxruntime as ort

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ps import runtime as runtime_module
from ps.preprocess import INPUT_SHAPE, preprocess_record, read_records
from ps.runtime import Runtime, OUTPUT_NAMES


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path, value):
    with Path(path).open("x", encoding="utf8") as f:
        json.dump(value, f, indent=2, allow_nan=False)
        f.write("\n")


def snapshot(path):
    info = {"python": platform.python_version(), "numpy": np.__version__, "onnxruntime": ort.__version__,
            "platform": platform.platform(), "logical_cpus": os.cpu_count(),
            "runtime_sha256": sha256(runtime_module.__file__)}
    try:
        info["affinity"] = sorted(os.sched_getaffinity(0))
    except AttributeError:
        info["affinity"] = None
    return info


def distribution(values):
    x = np.asarray(values, dtype=np.float64)
    if x.size == 0 or not np.isfinite(x).all():
        raise ValueError("No finite timing samples")
    return {"n": int(x.size), "mean": float(x.mean()), "p50": float(np.percentile(x, 50)),
            "p95": float(np.percentile(x, 95)), "p99": float(np.percentile(x, 99)), "max": float(x.max())}


def worker(job_path):
    job = json.loads(Path(job_path).read_text(encoding="utf8"))
    out = Path(job["directory"])
    result = {"status": "失败", "exit_code": 1, "mode": job["mode"], "threads": 4,
              "spinning": job["spinning"], "hardware_execution": False, "versions": snapshot(out),
              "start_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "actual_command": sys.argv}
    try:
        if sha256(runtime_module.__file__) != job["runtime_sha256"]:
            raise ValueError("Production Runtime SHA256 mismatch")
        if sha256(Path(job["bundle"]) / "manifest.json") != job["manifest_sha256"]:
            raise ValueError("Bundle manifest SHA256 mismatch")
        if sha256(job["inputs"]) != job["inputs_sha256"]:
            raise ValueError("Input snapshot SHA256 mismatch")
        inputs = np.load(job["inputs"], allow_pickle=False)
        if inputs.dtype != np.float32 or inputs.shape != (job["frames"],) + INPUT_SHAPE or not np.isfinite(inputs).all():
            raise ValueError("Input snapshot contract mismatch")
        runtime = Runtime(job["bundle"], mode=job["mode"], threads=4, spinning=job["spinning"])
        # Warmup is deliberately outside measured samples.
        for i in range(job["warmup"]):
            runtime.run(inputs[i % len(inputs)])
        samples, outputs = [], {key: [] for key in OUTPUT_NAMES}
        wall_start = time.perf_counter()
        cpu_start = time.process_time()
        for index, image in enumerate(inputs):
            start = time.perf_counter()
            value = runtime.run(image)
            samples.append({"index": index, "run_total_ms": (time.perf_counter() - start) * 1000,
                            "stages_ms": dict(runtime.last_timings_ms)})
            for key in outputs:
                outputs[key].append(value[key])
        wall_s, cpu_s = time.perf_counter() - wall_start, time.process_time() - cpu_start
        output = {key: np.stack(value) for key, value in outputs.items()}
        np.savez(out / "outputs.npz", **output)
        result.update({"end_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                      "n": len(samples), "warmup": job["warmup"], "wall_s": wall_s, "process_cpu_s": cpu_s,
                      "process_cpu_wall_ratio": cpu_s / wall_s,
                      "run_total_ms": distribution([s["run_total_ms"] for s in samples]),
                      "perstage_ms": {key: distribution([s["stages_ms"][key] for s in samples])
                                      for key in samples[0]["stages_ms"]},
                      "samples": samples, "outputs_sha256": sha256(out / "outputs.npz"),
                      "session_config": {"threads": 4, "spinning": job["spinning"], "inter_op_threads": 1,
                                         "execution_mode": "ORT_SEQUENTIAL", "graph_optimization": "ORT_ENABLE_EXTENDED"}})
        del runtime
        result["status"], result["exit_code"] = "已验证", 0
    except Exception as e:
        result["first_error"], result["traceback"] = str(e), traceback.format_exc()
        traceback.print_exc()
    write_json(out / "report.json", result)
    return result["exit_code"]


def launch(root, name, job):
    path = root / name
    path.mkdir()
    job["directory"] = str(path)
    write_json(path / "job.json", job)
    command = [sys.executable, str(Path(__file__).resolve()), "--worker", str(path / "job.json")]
    with (path / "stdout.log").open("x", encoding="utf8") as stdout, (path / "stderr.log").open("x", encoding="utf8") as stderr:
        process = subprocess.run(command, stdout=stdout, stderr=stderr, check=False)
    write_json(path / "process.json", {"command": command, "exit_code": process.returncode})
    if process.returncode != 0:
        raise RuntimeError("Worker failed: " + name + " exit=" + str(process.returncode))
    return path


def compare(actual_path, golden_path):
    with np.load(actual_path, allow_pickle=False) as actual, np.load(golden_path, allow_pickle=False) as golden:
        if set(actual.files) != set(golden.files) or set(actual.files) != set(OUTPUT_NAMES):
            raise ValueError("Output members differ")
        errors = {}
        for key in OUTPUT_NAMES:
            a, b = actual[key], golden[key]
            if a.shape != b.shape or a.dtype != b.dtype or a.dtype != np.float32 or not np.isfinite(a).all() or not np.isfinite(b).all():
                raise ValueError("Output shape/dtype/finiteness mismatch: " + key)
            errors[key] = float(np.max(np.abs(a - b)))
        integer_mismatches = int(np.count_nonzero(np.trunc(actual["speed_raw"]) != np.trunc(golden["speed_raw"])))
    return {"max_abs_error": errors, "atol": 1e-3, "rtol": 0,
            "speed_integer_mismatches": integer_mismatches,
            "pass": max(errors.values()) <= 1e-3 and integer_mismatches == 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--recording", type=Path, action="append")
    parser.add_argument("--inputs", type=Path)
    parser.add_argument("--frames", type=int, default=117)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        return worker(args.worker)
    if args.bundle is None or args.output is None or (bool(args.recording) == bool(args.inputs)):
        parser.error("provide --bundle --output and exactly one of --recording/--inputs")
    if args.frames < 1 or args.warmup < 0:
        parser.error("frames must be positive; warmup nonnegative")
    args.output.mkdir(parents=True, exist_ok=False)
    report = {"status": "失败", "exit_code": 1, "hardware_execution": False,
              "actual_command": sys.argv, "versions": snapshot(args.output),
              "bundle_manifest_sha256": sha256(args.bundle / "manifest.json"),
              "start_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        prepare_start = time.perf_counter()
        preprocessing_ms = []
        if args.recording:
            records = read_records(args.recording, limit=args.frames)
            if len(records) != args.frames:
                raise ValueError("Recording has fewer frames than requested")
            values = []
            for record in records:
                start = time.perf_counter()
                values.append(preprocess_record(record))
                preprocessing_ms.append((time.perf_counter() - start) * 1000)
            inputs = np.stack(values)
        else:
            inputs = np.load(args.inputs, allow_pickle=False)
            if inputs.shape == INPUT_SHAPE:
                inputs = inputs[None]
            inputs = inputs[:args.frames]
        if inputs.dtype != np.float32 or inputs.shape != (args.frames,) + INPUT_SHAPE or not np.isfinite(inputs).all():
            raise ValueError("Input snapshot contract mismatch")
        inputs_path = args.output / "inputs.npy"
        np.save(inputs_path, np.ascontiguousarray(inputs), allow_pickle=False)
        input_hash = sha256(inputs_path)
        write_json(args.output / "inputs.json", {"shape": list(inputs.shape), "dtype": "float32",
                                                   "sha256": input_hash, "preloaded_before_sessions": True,
                                                   "prepare_wall_ms": (time.perf_counter() - prepare_start) * 1000,
                                                   "decode_preprocess_ms": preprocessing_ms,
                                                   "source": [str(p.resolve()) for p in (args.recording or [args.inputs])]})
        common = {"bundle": str(args.bundle.resolve()), "inputs": str(inputs_path), "inputs_sha256": input_hash,
                  "runtime_sha256": report["versions"]["runtime_sha256"],
                  "manifest_sha256": report["bundle_manifest_sha256"],
                  "frames": args.frames, "warmup": args.warmup}
        # Separate processes ensure golden pools cannot perturb the candidate.
        golden = launch(args.output, "golden4", {**common, "mode": "golden", "spinning": None})
        candidate = launch(args.output, "split4_spinning_off", {**common, "mode": "split-ort", "spinning": False})
        validation = compare(candidate / "outputs.npz", golden / "outputs.npz")
        report.update({"candidate": str(candidate / "report.json"), "golden_reference": str(golden / "report.json"),
                       "comparison": validation,
                       "comparison_caveat": "Golden full graph vs split ORT output, same board/input/thread count/ORT settings. The candidate differs only in optional spinning config from the prior split4_off experiment; timing includes Runtime.run FFN diagnostic copies and excludes preprocessing/initialization.",
                       "end_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        if not validation["pass"]:
            raise ValueError("Numerical/integer equivalence failed")
        report["status"], report["exit_code"] = "已验证", 0
    except Exception as e:
        report["first_error"], report["traceback"] = str(e), traceback.format_exc()
        traceback.print_exc()
    write_json(args.output / "validation.json", report)
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
