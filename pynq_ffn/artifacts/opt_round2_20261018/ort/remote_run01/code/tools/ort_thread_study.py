"""Isolated CPU-only ORT thread-pool study; never imports PYNQ.

Each policy/reference/profile is a fresh subprocess. Runtime.__init__ and run
are reused without changing production globals or patching the ORT package.
Python >=3.10. No dependency installation, camera, serial or hardware access.
"""
from __future__ import annotations

import argparse
import collections
import gc
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import traceback

import cv2
import numpy as np
import onnxruntime as ort

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ps import runtime as production
from ps.preprocess import INPUT_SHAPE, preprocess_record, read_records

FROZEN_RUNTIME_SHA256 = "023327eadeaa935ec8392ec16095c6df2a1155368595afd37fe233b4cc6ed298"
HEAVY = {"stage_0", "stage_1", "camera_0", "camera_1", "golden"}
QUICK = ("golden4", "split1", "split4", "split4_off", "mixed4_1", "mixed4_1_off")
ALL = ("golden1", "golden2", "golden4", "golden_default", "split1", "split2",
       "split4", "split_default", "split4_off", "mixed4_1", "mixed4_1_off",
       "mixed2_1", "mixed2_1_off", "golden4_off", "golden2_off")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path, data):
    with Path(path).open("x", encoding="utf8") as stream:
        json.dump(data, stream, indent=2, allow_nan=False)
        stream.write("\n")


def policy(name):
    spin = not name.endswith("_off")
    base = name[:-4] if not spin else name
    if base.startswith("golden") or base.startswith("split"):
        mode = "golden" if base.startswith("golden") else "split-ort"
        suffix = base[6:] if mode == "golden" else base[5:]
        threads = 0 if suffix == "_default" else int(suffix)
        if threads not in (0, 1, 2, 4):
            raise ValueError("Unsupported thread count")
        heavy = small = threads
    elif base in ("mixed4_1", "mixed2_1"):
        mode, heavy, small = "split-ort", int(base[5]), 1
    else:
        raise ValueError("Unknown policy: " + name)
    return {"name": name, "mode": mode, "heavy_threads": heavy,
            "small_threads": small, "allow_spinning": spin,
            "inter_op_threads": 1, "execution_mode": "ORT_SEQUENTIAL",
            "graph_optimization": "ORT_ENABLE_EXTENDED", "provider": "CPUExecutionProvider"}


def reference_name(spec):
    count = spec["heavy_threads"]
    return "golden" + ("_default" if count == 0 else str(count)) + ("" if spec["allow_spinning"] else "_off")


class SessionFactory:
    """Explicit per-session policy for the experimental adapter only."""
    def __init__(self, spec, profile_dir=None):
        self.spec = spec
        self.profile_dir = Path(profile_dir) if profile_dir is not None else None
        self.created = []

    def InferenceSession(self, path, providers=None):
        if providers != ["CPUExecutionProvider"]:
            raise ValueError("Only CPUExecutionProvider is permitted")
        stem = "golden" if Path(path).name == "model.onnx" else Path(path).stem
        count = self.spec["heavy_threads"] if stem in HEAVY else self.spec["small_threads"]
        options = ort.SessionOptions()
        options.intra_op_num_threads = count
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_EXTENDED
        options.log_severity_level = 3
        # Explicit both ways: default-spin is documented as enabled, not inferred.
        for key in ("session.intra_op.allow_spinning", "session.inter_op.allow_spinning"):
            options.add_session_config_entry(key, "1" if self.spec["allow_spinning"] else "0")
        if self.profile_dir is not None:
            options.enable_profiling = True
            options.profile_file_prefix = str(self.profile_dir / stem)
        session = ort.InferenceSession(path, sess_options=options, providers=providers)
        if session.get_providers() != ["CPUExecutionProvider"]:
            raise ValueError("Unexpected provider/fallback")
        self.created.append({"name": stem, "intra_op_threads": count,
                             "inter_op_threads": 1, "allow_spinning": self.spec["allow_spinning"]})
        return session


class PolicyRuntime(production.Runtime):
    def __init__(self, bundle, spec, profile_dir=None):
        runtime_file = Path(production.__file__).resolve()
        if sha256(runtime_file) != FROZEN_RUNTIME_SHA256:
            raise ValueError("Production Runtime changed since experiment freeze")
        start = time.perf_counter()
        # Run the exact production integrity/weights/ABI checks with no extra
        # worker pools (threads=1), then release every temporary session.
        super().__init__(bundle, mode=spec["mode"], threads=1)
        self.sessions.clear()
        gc.collect()
        validated = time.perf_counter()
        factory = SessionFactory(spec, profile_dir)
        if self.mode == "golden":
            self.sessions["golden"] = factory.InferenceSession(str(self.bundle / "model.onnx"), providers=["CPUExecutionProvider"])
        else:
            for stage in self.abi["schedule"]:
                current = factory.InferenceSession(str(self.bundle / stage["file"]), providers=["CPUExecutionProvider"])
                if [v.name for v in current.get_inputs()] != stage["inputs"] or [v.name for v in current.get_outputs()] != stage["outputs"]:
                    raise ValueError("Stage contract mismatch during experimental rebuild")
                self.sessions[stage["name"]] = current
        self.experimental_initialization_ms = {
            "production_validation_and_release": (validated - start) * 1000,
            "policy_rebuild": (time.perf_counter() - validated) * 1000}
        self.thread_policy = factory.created


def distribution(values):
    a = np.asarray(values, dtype=np.float64)
    if a.size == 0 or not np.isfinite(a).all():
        raise ValueError("No finite timing samples")
    return {"n": int(a.size), "mean": float(a.mean()), "p50": float(np.percentile(a, 50)),
            "p95": float(np.percentile(a, 95)), "p99": float(np.percentile(a, 99)),
            "max": float(a.max()), "min": float(a.min())}


def process_snapshot():
    result = {"process_cpu_s": time.process_time(), "task_threads": None,
              "process_ctx": None, "task_ctx": None}
    task_root = Path("/proc/self/task")
    if task_root.is_dir():
        paths = list(task_root.iterdir())
        result["task_threads"] = len(paths)
        contexts = {}
        for path in paths:
            try:
                fields = {}
                for line in (path / "status").read_text().splitlines():
                    if line.startswith(("voluntary_ctxt_switches:", "nonvoluntary_ctxt_switches:")):
                        key, value = line.split(":", 1)
                        fields[key] = int(value)
                contexts[path.name] = fields
            except FileNotFoundError:
                continue
        result["task_ctx"] = contexts
    try:
        import resource
        usage = resource.getrusage(resource.RUSAGE_SELF)
        result["process_ctx"] = {"voluntary": usage.ru_nvcsw, "involuntary": usage.ru_nivcsw}
    except ImportError:
        pass
    return result


def context_delta(before, after):
    if before["process_ctx"] is None or after["process_ctx"] is None:
        return None
    return {k: after["process_ctx"][k] - v for k, v in before["process_ctx"].items()}


def compare_outputs(actual, reference, atol=1e-3):
    if set(actual) != set(production.OUTPUT_NAMES) or set(reference) != set(actual):
        raise ValueError("Output members differ")
    errors = {}
    for name in production.OUTPUT_NAMES:
        a, b = actual[name], reference[name]
        if a.shape != b.shape or a.dtype != np.float32 or b.dtype != np.float32:
            raise ValueError("Output shape/dtype mismatch: " + name)
        if not np.isfinite(a).all() or not np.isfinite(b).all():
            raise ValueError("Non-finite output: " + name)
        errors[name] = float(np.max(np.abs(a - b)))
    mismatches = int(np.count_nonzero(np.trunc(actual["speed_raw"]) != np.trunc(reference["speed_raw"])))
    return {"max_abs_error": errors, "speed_integer_mismatches": mismatches,
            "atol": atol, "rtol": 0, "pass": max(errors.values()) <= atol and mismatches == 0}


def parse_profiles(paths):
    result = {}
    for name, path in paths.items():
        events = json.loads(Path(path).read_text(encoding="utf8"))
        kernel, fence, schedule = [], [], []
        operators = collections.defaultdict(list)
        for event in events:
            event_name = event.get("name", "")
            if "kernel_time" in event_name:
                kernel.append(float(event.get("dur", 0)))
                operators[event.get("args", {}).get("op_name", "unknown")].append(float(event.get("dur", 0)))
            if "fence" in event_name.lower():
                fence.append(float(event.get("dur", 0)))
            stats = event.get("args", {}).get("thread_scheduling_stats")
            if stats:
                schedule.append({"name": event_name, "stats": stats})
        result[name] = {"path": str(path), "sha256": sha256(path), "event_count": len(events),
                        "kernel_events": len(kernel), "kernel_total_us": sum(kernel),
                        "fence_events": len(fence), "fence_total_us": sum(fence),
                        "operators_us": {k: {"events": len(v), "total": sum(v)} for k, v in operators.items()},
                        "thread_scheduling_stats": schedule,
                        "limitation": "Profile includes warmup; events do not alone prove pool contention. Missing/empty scheduling stats are not evidence of no contention."}
    return result


def versions():
    return {"python": platform.python_version(), "numpy": np.__version__,
            "onnxruntime": ort.__version__, "opencv": cv2.__version__, "platform": platform.platform(),
            "machine": platform.machine(), "logical_cpus": os.cpu_count(),
            "affinity": sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None,
            "thread_environment": {key: os.environ.get(key) for key in
                                   ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "ORT_NUM_THREADS")}}


def worker(job_path):
    job = json.loads(Path(job_path).read_text(encoding="utf8"))
    out = Path(job["directory"])
    report = {"status": "失败", "actual_command": sys.argv, "pid": os.getpid(),
              "hardware_execution": False, "versions": versions(), "policy": policy(job["condition"]),
              "runtime_sha256": sha256(production.__file__), "profiling": job["profile"],
              "start_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "exit_code": 1}
    try:
        if sha256(job["inputs"]) != job["inputs_sha256"]:
            raise ValueError("Input snapshot hash mismatch")
        if sha256(Path(job["bundle"]) / "manifest.json") != job["manifest_sha256"]:
            raise ValueError("Bundle manifest changed")
        load_start = time.perf_counter()
        inputs = np.load(job["inputs"], allow_pickle=False)
        if inputs.dtype != np.float32 or inputs.shape[1:] != INPUT_SHAPE or not np.isfinite(inputs).all():
            raise ValueError("Invalid preloaded inputs")
        # Materialize all inputs before initialization and timing; no decode/preprocess in run_total.
        inputs = np.ascontiguousarray(inputs)
        report["input_load_ms"] = (time.perf_counter() - load_start) * 1000
        report["input_sha256"] = job["inputs_sha256"]
        report["manifest_sha256"] = job["manifest_sha256"]
        report["snapshot_before_init"] = process_snapshot()
        if job["profile"]:
            (out / "profiles").mkdir()
        init_start = time.perf_counter()
        runtime = PolicyRuntime(job["bundle"], report["policy"], out / "profiles" if job["profile"] else None)
        report["initialization_ms"] = (time.perf_counter() - init_start) * 1000
        report["session_policies"] = runtime.thread_policy
        report["experimental_initialization_ms"] = runtime.experimental_initialization_ms
        for i in range(job["warmup"]):
            runtime.run(inputs[i % len(inputs)])
        samples, outputs = [], {key: [] for key in production.OUTPUT_NAMES}
        before = process_snapshot()
        wall_start = time.perf_counter()
        for i, x in enumerate(inputs):
            cpu_start, start = time.process_time(), time.perf_counter()
            values = runtime.run(x)
            elapsed, cpu = (time.perf_counter() - start) * 1000, (time.process_time() - cpu_start) * 1000
            samples.append({"index": i, "run_total_ms": elapsed, "process_cpu_ms": cpu,
                            "cpu_wall_ratio": cpu / elapsed, "stages_ms": dict(runtime.last_timings_ms)})
            for key in outputs:
                outputs[key].append(values[key])
        wall_s = time.perf_counter() - wall_start
        after = process_snapshot()
        output_arrays = {key: np.stack(values) for key, values in outputs.items()}
        np.savez(out / "outputs.npz", **output_arrays)
        report.update({"n": len(inputs), "warmup": job["warmup"], "snapshot_before_measurement": before,
                       "snapshot_after_measurement": after, "process_context_switch_delta": context_delta(before, after),
                       "measurement_loop_wall_s": wall_s,
                       "measurement_loop_cpu_s": after["process_cpu_s"] - before["process_cpu_s"],
                       "measurement_loop_cpu_wall_ratio": (after["process_cpu_s"] - before["process_cpu_s"]) / wall_s,
                       "run_total_ms": distribution([s["run_total_ms"] for s in samples]),
                       "process_cpu_ms": distribution([s["process_cpu_ms"] for s in samples]),
                       "perstage_ms": {key: distribution([s["stages_ms"][key] for s in samples]) for key in samples[0]["stages_ms"]},
                       "timing_scope": "run_total wraps original Runtime.run incl input/output validation and FFN cut copies; excludes init/decode/preprocess/comparison/profile parsing. Perstage timers use original Runtime; stages do not sum to run_total. Loop counters include Python sample/output collection.",
                       "samples": samples, "output_sha256": sha256(out / "outputs.npz")})
        if job.get("reference"):
            if sha256(job["reference"]) != job["reference_sha256"]:
                raise ValueError("Reference output hash mismatch")
            with np.load(job["reference"], allow_pickle=False) as archive:
                comparison = compare_outputs(output_arrays, dict(archive))
            report["comparison"] = comparison
            report["reference"] = job["reference"]
            report["reference_sha256"] = job["reference_sha256"]
            if not comparison["pass"]:
                raise ValueError("Numerical/integer equivalence failed")
        if job["profile"]:
            paths = {name: session.end_profiling() for name, session in runtime.sessions.items()}
            report["profile_summary"] = parse_profiles(paths)
        # ORT pools are ultimately released by exiting this fresh worker process.
        del runtime
        report["status"], report["exit_code"] = "已验证", 0
    except Exception as error:
        report["first_error"] = str(error)
        report["traceback"] = traceback.format_exc()
        traceback.print_exc()
    report["end_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    write_json(out / "report.json", report)
    return report["exit_code"]


def source_hashes():
    return {str(p.relative_to(ROOT)): sha256(p) for p in
            (Path(__file__), Path(production.__file__), ROOT / "ps/preprocess.py", ROOT / "ps/backends.py")}


def prepare_inputs(args, out):
    before = time.perf_counter()
    provenance = {"source": [], "preprocessing_ms": [], "decode_preprocess_in_run_total": False}
    if args.recording:
        records = read_records(args.recording, limit=args.frames)
        if len(records) != args.frames:
            raise ValueError("Recording has fewer frames than requested")
        values = []
        for record in records:
            start = time.perf_counter()
            values.append(preprocess_record(record))
            provenance["preprocessing_ms"].append((time.perf_counter() - start) * 1000)
            provenance["source"].append({"segment": record.segment, "frame_index": record.frame_index,
                                          "csv_line": record.csv_line,
                                          "images": [{"path": str(p), "sha256": sha256(p)} for p in (record.left, record.front, record.right)]})
        arrays = np.stack(values)
        provenance["csv"] = [{"path": str((p / "auto_data.csv" if p.is_dir() else p).resolve()),
                               "sha256": sha256(p / "auto_data.csv" if p.is_dir() else p)} for p in args.recording]
        provenance["preprocessing_summary_ms"] = distribution(provenance["preprocessing_ms"])
    else:
        arrays = np.load(args.inputs, allow_pickle=False)
        if arrays.shape == INPUT_SHAPE:
            arrays = arrays[None, ...]
        if len(arrays) < args.frames:
            raise ValueError("Input snapshot has fewer frames than requested; use --frames explicitly for smoke tests")
        arrays = arrays[:args.frames]
        provenance["source"] = [{"path": str(args.inputs.resolve()), "sha256": sha256(args.inputs)}]
        provenance["preprocessing_summary_ms"] = None
    if arrays.dtype != np.float32 or arrays.shape != (args.frames,) + INPUT_SHAPE or not np.isfinite(arrays).all():
        raise ValueError("Input snapshot violates reviewed input contract")
    np.save(out / "inputs.npy", np.ascontiguousarray(arrays), allow_pickle=False)
    provenance.update({"n": len(arrays), "shape": list(arrays.shape), "dtype": str(arrays.dtype),
                       "snapshot_sha256": sha256(out / "inputs.npy"),
                       "prepare_wall_ms": (time.perf_counter() - before) * 1000})
    write_json(out / "inputs.json", provenance)
    return provenance


def launch_job(out, name, condition, args, snapshot, reference=None, profile=False):
    directory = out / name
    directory.mkdir()
    job = {"condition": condition, "bundle": str(args.bundle.resolve()),
           "directory": str(directory), "inputs": str(out / "inputs.npy"),
           "inputs_sha256": snapshot["snapshot_sha256"],
           "manifest_sha256": snapshot["manifest_sha256"],
           "warmup": args.warmup, "profile": profile,
           "reference": str(reference) if reference else None,
           "reference_sha256": sha256(reference) if reference else None}
    write_json(directory / "job.json", job)
    command = [sys.executable, str(Path(__file__).resolve()), "--worker", str(directory / "job.json")]
    start = time.perf_counter()
    with (directory / "stdout.log").open("x", encoding="utf8") as stdout, (directory / "stderr.log").open("x", encoding="utf8") as stderr:
        child = subprocess.run(command, stdout=stdout, stderr=stderr, check=False)
    write_json(directory / "process.json", {"command": command, "exit_code": child.returncode,
                                             "wall_s": time.perf_counter() - start})
    if child.returncode != 0 or not (directory / "report.json").is_file():
        raise RuntimeError("Worker failed: " + name + "; exit=" + str(child.returncode) + "; see " + str(directory / "stderr.log"))
    print(name + " complete", flush=True)
    return directory


def analyze(out):
    rows = []
    for path in sorted(Path(out).glob("perf_*/report.json")):
        data = json.loads(path.read_text(encoding="utf8"))
        if data["profiling"]:
            raise ValueError("Profiling must not enter performance analysis")
        rows.append({"run": path.parent.name, "policy": data["policy"]["name"],
                     "status": data["status"], "run_total_ms": data["run_total_ms"],
                     "cpu_wall_ratio": data["measurement_loop_cpu_wall_ratio"],
                     "threads": data["snapshot_after_measurement"]["task_threads"],
                     "context_switches": data["process_context_switch_delta"],
                     "comparison": data.get("comparison"), "evidence": str(path)})
    if not rows:
        raise ValueError("No formal performance reports")
    return {"rows": rows, "limitation": "CPU-only thread/spin intervention. No causal attribution to graph fusion; profiling is separate. Compare both orders and numerical gates before adopting any policy."}


def study(args):
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    report = {"status": "失败", "exit_code": 1, "actual_command": sys.argv,
              "hardware_execution": False, "versions": versions(), "source_hashes": source_hashes(),
              "date_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "runs": []}
    try:
        if sha256(production.__file__) != FROZEN_RUNTIME_SHA256:
            raise ValueError("Production Runtime changed since freeze")
        manifest = json.loads((args.bundle / "manifest.json").read_text(encoding="utf8"))
        report["bundle_manifest"] = manifest
        report["manifest_sha256"] = sha256(args.bundle / "manifest.json")
        snapshot = prepare_inputs(args, out)
        snapshot["manifest_sha256"] = report["manifest_sha256"]
        selected = list(QUICK if args.mode == "quick" else ALL) if args.mode != "one" else [args.condition]
        for condition in selected:
            policy(condition)
        report["orders"] = [selected if i % 2 == 0 else list(reversed(selected)) for i in range(args.rounds)]
        # Build references in isolated processes, never keep reference pools alive in perf workers.
        refs = {}
        for name in dict.fromkeys(reference_name(policy(c)) for c in selected):
            directory = launch_job(out, "reference_" + name, name, args, snapshot)
            refs[name] = directory / "outputs.npz"
        # Also cross-check same-condition reference outputs against the first golden policy.
        with np.load(next(iter(refs.values())), allow_pickle=False) as archive:
            anchor = dict(archive)
        checks = {}
        for name, path in refs.items():
            with np.load(path, allow_pickle=False) as archive:
                checks[name] = compare_outputs(dict(archive), anchor)
            if not checks[name]["pass"]:
                raise ValueError("Golden reference policies disagree: " + name)
        report["reference_crosschecks"] = checks
        for round_index, order in enumerate(report["orders"], 1):
            for ordinal, condition in enumerate(order, 1):
                name = "perf_r%d_%02d_%s" % (round_index, ordinal, condition)
                directory = launch_job(out, name, condition, args, snapshot, refs[reference_name(policy(condition))])
                report["runs"].append(str(directory))
        # Optional profiling is an additional pass, never merged into formal timing.
        for ordinal, condition in enumerate(args.profile_condition, 1):
            refname = reference_name(policy(condition))
            if refname not in refs:
                raise ValueError("Profile condition lacks a matching formal reference")
            launch_job(out, "profile_%02d_%s" % (ordinal, condition), condition, args, snapshot, refs[refname], True)
        report["analysis"] = analyze(out)
        if report["source_hashes"] != source_hashes():
            raise ValueError("Source changed during study")
        report["status"], report["exit_code"] = "已验证", 0
    except Exception as error:
        report["first_error"] = str(error)
        report["traceback"] = traceback.format_exc()
        traceback.print_exc()
    report["end_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    write_json(out / "study.json", report)
    return report["exit_code"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("quick", "all", "one", "analysis"), default="quick")
    parser.add_argument("--bundle", type=Path)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--recording", action="append", type=Path)
    source.add_argument("--inputs", type=Path)
    parser.add_argument("--output", type=Path, help="New immutable run directory; never reuse")
    parser.add_argument("--study-dir", type=Path, help="Read-only analysis input")
    parser.add_argument("--condition", choices=ALL, default="split4_off")
    parser.add_argument("--profile-condition", action="append", choices=ALL, default=[])
    parser.add_argument("--frames", type=int, default=117)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--rounds", type=int, choices=(1, 2), default=1,
                        help="Default quick first pass only; 2 reverses the second pass")
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        return worker(args.worker)
    if args.mode == "analysis":
        if args.study_dir is None:
            parser.error("analysis requires --study-dir")
        print(json.dumps(analyze(args.study_dir), indent=2, allow_nan=False))
        return 0
    if args.output is None or args.bundle is None or not (args.recording or args.inputs):
        parser.error("study requires --bundle --output and --recording or --inputs")
    if args.frames < 1 or args.warmup < 0:
        parser.error("frames must be positive and warmup nonnegative")
    return study(args)


if __name__ == "__main__":
    sys.exit(main())
