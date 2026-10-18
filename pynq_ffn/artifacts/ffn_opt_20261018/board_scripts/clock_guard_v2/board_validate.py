#!/usr/bin/env python3
"""Offline-recording validation only. No camera, serial, vehicle, reset or recovery.

Default is STATIC ONLY: no project/PYNQ imports, overlay programming or MMIO.
See --help. Deployment manifest schema: ffn_board.deployment.v1, files maps
root-relative paths to {sha256, bytes}. Pin its hash from an external source.
Only --allow-pl permits PYNQ import/programming; run under exclusive board access.
A quarantined run intentionally NEVER exits, even on Ctrl-C/SIGTERM. An operator
must verify a safe PL reset before terminating it; killing it is NOT recovery.
Clock guard v2: actual fclk0 defaults to 100 MHz +/-0.5%, checked after overlay
programming BEFORE backend construction/allocation and again at end. HWH clock
values are metadata only. No clock/PLL writes or automatic repair are performed.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import gc
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import signal
import sys
import time
import xml.etree.ElementTree as ET

LAYERS = ("camera_0", "camera_1", "cross_0", "cross_1")
OUTPUTS = ("speed_norm", "speed_raw", "region_logits", "region_probs")
PHASES = ("static", "authorization", "inputs", "hardware", "references", "layers", "regression", "performance")
DEPENDENCIES = {"static": [], "authorization": ["static"], "inputs": ["authorization"],
                "hardware": ["inputs"], "references": ["inputs"],
                "layers": ["hardware", "references"], "regression": ["layers", "references"],
                "performance": ["hardware", "inputs"]}
# Strong ownership survives every exception and remains live in the quarantine loop.
LIVE = []


class ClockMismatchError(RuntimeError):
    pass


def hwh_clock_declarations(path, ip_name):
    """Metadata only; never a substitute for the actual Clocks getter."""
    return [{"port": p.get("NAME"), "signal": p.get("SIGNAME"),
             "declared_hz": p.get("CLKFREQUENCY"), "authority": "metadata_only"}
            for m in ET.parse(path).iter("MODULE") if m.get("INSTANCE") == ip_name
            for p in m.findall("./PORTS/PORT") if p.get("SIGIS") == "clk"]


def validate_clock(clocks, args, evidence, ip, checkpoint, initial=None):
    # Read the actual getter, not overlay.clock_dict, HWH or kernel clkcache.
    # This function contains no setters or clock/PLL register writes.
    try:
        actual = float(clocks.fclk0_mhz)
        read_error = None
    except Exception as error:
        actual = None
        read_error = f"{type(error).__name__}: {error}"
    finite = actual is not None and math.isfinite(actual) and actual > 0
    tolerance = args.expected_clock_mhz * args.clock_tolerance_percent / 100
    expected_ok = finite and abs(actual - args.expected_clock_mhz) <= tolerance
    initial_ok = initial is None or (finite and abs(actual - initial) <= tolerance)
    row = {"checkpoint": checkpoint, "date_utc": now(),
           "actual_clock_mhz": actual if finite else None,
           "actual_clock_raw": repr(actual), "getter_error": read_error,
           "expected_clock_mhz": args.expected_clock_mhz,
           "clock_tolerance_percent": args.clock_tolerance_percent,
           "initial_validated_clock_mhz": initial,
           "raw_state": {"ctrl": hex(int(ip.read(0))), "actual_fclk0_getter_repr": repr(actual)},
           "hardware_programmed": True, "compute_executed": bool(ip.starts),
           "kernel_starts_observed": ip.starts,
           "source": "actual pynq.Clocks.fclk0_mhz getter; no automatic clock writes",
           "status": "已验证" if expected_ok and initial_ok else "失败"}
    evidence.report.setdefault("clock_checks", []).append(row)
    evidence.report.update(hardware_programmed=True, compute_executed=bool(ip.starts),
                           hardware_execution=bool(ip.starts), kernel_starts_observed=ip.starts)
    if not expected_ok or not initial_ok:
        evidence.report["failure_mode"] = "failed_clock_mismatch"
        evidence.event("failed_clock_mismatch", row)
        raise ClockMismatchError(f"failed_clock_mismatch at {checkpoint}: actual={actual!r}, expected={args.expected_clock_mhz}")
    evidence.event("clock_validated", row)
    return actual


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_memory():
    try:
        return {k: v.strip() for line in Path("/proc/meminfo").read_text().splitlines()
                for k, _, v in [line.partition(":")]
                if k in ("MemAvailable", "CmaTotal", "CmaFree")}
    except OSError:
        return {}


def versions():
    packages = {}
    for name in ("numpy", "onnxruntime", "pynq", "opencv-python", "opencv-python-headless"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    return {"python": sys.version, "executable": sys.executable, "platform": platform.platform(),
            "machine": platform.machine(), "packages": packages, "memory": read_memory()}


class Evidence:
    def __init__(self, directory, args):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.journal = (self.directory / "events.jsonl").open("x", encoding="utf8")
        self.report = {"schema": "ffn_board.validation.v1", "date_utc": now(),
                       "status": "未执行", "phases": {p: {"status": "未执行"} for p in PHASES},
                       "dependencies": versions(), "arguments": vars(args),
                       "validator_script_sha256": sha(Path(__file__)),
                       "hardware_execution": False, "hardware_programmed": False, "compute_executed": False,
                       "not_executed": ["camera", "serial", "vehicle", "live_frame_freshness",
                                        "overlay_reset_or_timeout_recovery", "task_accuracy"],
                       "scope": "recorded inputs; throughput is NOT live inference/fresh-frame rate; "
                                "joystick labels are NOT task accuracy"}
        self.phase = "static"
        self.event("created", self.report)

    def event(self, kind, payload):
        self.journal.write(json.dumps({"date_utc": now(), "kind": kind, "data": payload},
                                     ensure_ascii=False, allow_nan=False, default=str) + "\n")
        self.journal.flush()
        os.fsync(self.journal.fileno())

    def start(self, phase):
        self.phase = phase
        self.event("phase_start", {"phase": phase, "dependencies": DEPENDENCIES[phase]})

    def finish(self, phase, data):
        value = {"status": "已验证", "date_utc": now(), "dependencies": DEPENDENCIES[phase], **data}
        self.report["phases"][phase] = value
        self.event(phase, value)

    def save(self):
        with (self.directory / "report.json").open("x", encoding="utf8") as stream:
            json.dump(self.report, stream, ensure_ascii=False, indent=2, allow_nan=False, default=str)
            stream.flush()
            os.fsync(stream.fileno())


def confined(root, relative):
    p = Path(relative)
    if p.is_absolute() or ".." in p.parts or "\\" in str(relative):
        raise ValueError("Manifest paths must be root-relative POSIX paths")
    result = (root / p).resolve()
    if root not in result.parents or not result.is_file():
        raise ValueError(f"Missing/unconfined deployment member: {relative}")
    return result


def hwh_registers(path, ip_name):
    tree = ET.parse(path)
    matches = [m for m in tree.iter("MODULE") if m.get("INSTANCE") == ip_name]
    if len(matches) != 1:
        raise ValueError("Expected exactly one requested IP in HWH")
    def properties(node):
        return {p.get("NAME").lower(): p.get("VALUE") for p in node.findall("PROPERTY")}
    registers = {}
    for reg in matches[0].findall("./ADDRESSBLOCKS/ADDRESSBLOCK/REGISTERS/REGISTER"):
        item = properties(reg)
        item["fields"] = {f.get("NAME"): properties(f) for f in reg.findall("./FIELDS/FIELD")}
        if reg.get("NAME") in registers:
            raise ValueError("Ambiguous HWH register")
        registers[reg.get("NAME")] = item
    # Static, no project or pynq imports. The existing backend revalidates post-import.
    def number(value):
        return int(str(value), 0)
    ctrl = registers.get("CTRL", registers.get("AP_CTRL", {}))
    if number(ctrl.get("address_offset", -1)) != 0 or number(ctrl.get("size", 0)) != 32:
        raise ValueError("HWH control mismatch")
    fields = {k.upper(): v for k, v in ctrl.get("fields", {}).items()}
    if "AP_CONTINUE" in fields:
        raise ValueError("ap_ctrl_chain unsupported")
    for name, offset in {"AP_START": 0, "AP_DONE": 1, "AP_IDLE": 2, "AP_READY": 3, "AUTO_RESTART": 7}.items():
        field = fields.get(name, {})
        if number(field.get("bit_offset", -1)) != offset or number(field.get("bit_width", 0)) != 1:
            raise ValueError(f"HWH control field mismatch: {name}")
    for name, offset in {"x": 0x10, "y": 0x1c, "w1": 0x28, "b1": 0x34, "w2": 0x40, "b2": 0x4c}.items():
        reg = registers.get(name, {})
        combined = number(reg.get("address_offset", -1)) == offset and number(reg.get("size", 0)) == 64
        if not combined:
            for suffix, delta in (("_1", 0), ("_2", 4)):
                reg = registers.get(name + suffix, {})
                if number(reg.get("address_offset", -1)) != offset + delta or number(reg.get("size", 0)) != 32:
                    raise ValueError(f"HWH pointer mismatch: {name}")
    reg = registers.get("num_tokens", {})
    if number(reg.get("address_offset", -1)) != 0x58 or number(reg.get("size", 0)) != 32:
        raise ValueError("HWH token register mismatch")
    return registers


def static_check(args):
    root = Path(args.root).resolve(strict=True)
    manifest_path = Path(args.deployment_manifest).resolve(strict=True)
    if root in manifest_path.parents:
        raise ValueError("Deployment manifest must be external to deployment root")
    if sha(manifest_path) != args.manifest_sha256:
        raise ValueError("External deployment manifest SHA256 mismatch")
    manifest = json.loads(manifest_path.read_text(encoding="utf8"))
    if manifest.get("schema") != "ffn_board.deployment.v1" or not isinstance(manifest.get("files"), dict):
        raise ValueError("Unexpected external manifest schema")
    members = manifest["files"]
    for p in (root / "ps").rglob("*"):
        if p.is_symlink():
            raise ValueError("ps/ must not contain symlinks or external import paths")
    if (root / "ps").is_symlink() or (root / "bundle").is_symlink():
        raise ValueError("Deployment ps/ and bundle/ must be owned local directories")
    required = {f"ps/{p.name}" for p in (root / "ps").glob("*.py")}
    required.update(f"bundle/{p.name}" for p in (root / "bundle").iterdir() if p.is_file())
    required.update((args.bit, str(Path(args.bit).with_suffix(".hwh")).replace("\\", "/")))
    if "ps/runtime.py" not in required or "bundle/manifest.json" not in required or not required <= set(members):
        raise ValueError("External manifest must cover all ps Python, bundle and selected overlay files")
    # Do not allow unmanifested subpackages to become imported executable code.
    executable = {p.relative_to(root).as_posix() for p in (root / "ps").rglob("*.py")}
    if any(p.suffix.lower() in (".pyc", ".pyo", ".so", ".pyd") for p in (root / "ps").rglob("*")):
        raise ValueError("Deploy source-only ps/: cached/native executable imports are forbidden")
    if not executable <= set(members):
        raise ValueError("Unmanifested Python subpackage")
    for relative, spec in members.items():
        p = confined(root, relative)
        if p.stat().st_size != spec["bytes"] or sha(p) != spec["sha256"]:
            raise ValueError(f"Deployment member hash/size mismatch: {relative}")
    bit = confined(root, args.bit)
    hwh = bit.with_suffix(".hwh")
    if sha(bit) != args.bit_sha256 or sha(hwh) != args.hwh_sha256:
        raise ValueError("Explicit bit/HWH hash mismatch")
    registers = hwh_registers(hwh, args.ip_name)
    # Verify inner bundle manifest before importing any deployed code.
    bundle = root / "bundle"
    bm = json.loads((bundle / "manifest.json").read_text(encoding="utf8"))
    if bm.get("schema") != "pynq_ffn.bundle.v1":
        raise ValueError("Unexpected model bundle")
    for relative, spec in bm["files"].items():
        p = confined(bundle.resolve(), relative)
        if sha(p) != spec["sha256"] or p.stat().st_size != spec["bytes"]:
            raise ValueError("Inner bundle verification failed")
    return root, bit, registers, {"manifest_sha256": sha(manifest_path), "files": members,
                                 "bit_sha256": sha(bit), "hwh_sha256": sha(hwh),
                                 "hwh_clock_declarations_metadata_only": hwh_clock_declarations(hwh, args.ip_name),
                                 "hwh_registers": registers, "bundle_source_sha256": bm["source_sha256"]}


def load_project(root):
    if "pynq" in sys.modules or any(k == "ps" or k.startswith("ps.") for k in sys.modules):
        raise RuntimeError("Use a fresh process: project/PYNQ already imported")
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root))
    import numpy as np
    import cv2
    from ps.runtime import Runtime
    from ps.preprocess import read_records, preprocess_record
    from ps.pynq_backend import PynqFFN, validate_registers
    cv2.setNumThreads(1)
    cv2.ocl.setUseOpenCL(False)
    return np, Runtime, read_records, preprocess_record, PynqFFN, validate_registers


class CountedIP:
    def __init__(self, ip):
        self.ip = ip
        self.reads = self.writes = self.starts = 0

    def read(self, offset):
        self.reads += 1
        return self.ip.read(offset)

    def write(self, offset, value):
        self.writes += 1
        if offset == 0 and value & 1:
            self.starts += 1
        return self.ip.write(offset, value)

    def counts(self):
        return (self.reads, self.writes)


class MeasuredBackend:
    def __init__(self, backend):
        self.backend = backend
        self.rows = []

    def forward(self, name, x, weights):
        value = self.backend.forward(name, x, weights)
        self.rows.append({"layer": name, **self.backend.last_timing})
        return value


@contextmanager
def runtime_owner(factory, bundle, mode, threads, backend=None):
    """Only one runtime owns ORT pools. All sessions initialized before yielding."""
    runtime = factory(bundle, mode=mode, threads=threads,
                      **({"backend": backend} if backend is not None else {}))
    try:
        yield runtime
    finally:
        runtime.sessions.clear()
        del runtime
        gc.collect()


def compare(np, actual, expected, atol):
    if actual.shape != expected.shape or not np.isfinite(actual).all() or not np.isfinite(expected).all():
        raise AssertionError("Non-finite output or shape mismatch")
    error = float(np.max(np.abs(actual - expected)))
    if not np.allclose(actual, expected, atol=atol, rtol=0):
        raise AssertionError(f"absolute error {error} exceeds {atol}")
    return error


def dump_npz(np, path, arrays):
    with Path(path).open("xb") as stream:
        np.savez(stream, **arrays)


def make_references(np, factory, bundle, datasets, preprocess, threads, evidence):
    base = evidence.directory / "references"
    base.mkdir(exist_ok=False)
    for mode in ("golden", "split-ort"):
        with runtime_owner(factory, bundle, mode, threads) as runtime:
            for ds, records in enumerate(datasets):
                for index, record in enumerate(records):
                    out = runtime.run(preprocess(record))
                    values = {f"out_{k}": v for k, v in out.items()}
                    if mode == "split-ort":
                        with np.load(base / f"golden_{ds}_{index}.npz", allow_pickle=False) as ref:
                            for name in OUTPUTS:
                                compare(np, out[name], ref[f"out_{name}"], 1e-3)
                        values.update({f"ffn_{k}": v for k, v in runtime.last_ffn_outputs.items()})
                        if ds == 0 and index == 0:
                            values.update({f"input_{k}": v for k, v in runtime.last_ffn_inputs.items()})
                        compare_integer(np, out["speed_raw"], ref_speed(np, base / f"golden_{ds}_{index}.npz"))
                    dump_npz(np, base / f"{mode}_{ds}_{index}.npz", values)
                    evidence.event("reference_frame", {"mode": mode, "dataset": ds, "index": index,
                                                       "stage_ms": runtime.last_timings_ms})
        # The with-target binding persists in Python: explicitly drop it before next mode.
        del runtime
        gc.collect()
    return base


def ref_speed(np, path):
    with np.load(path, allow_pickle=False) as ref:
        return ref["out_speed_raw"].copy()


def compare_integer(np, actual, expected):
    if not np.isfinite(actual).all() or not np.isfinite(expected).all():
        raise AssertionError("Non-finite integer speed source")
    # Python int is the reviewed C++ truncation convention, no packet is sent.
    if [int(v) for v in actual.flat] != [int(v) for v in expected.flat]:
        raise AssertionError("Integer speed mismatch against golden")


def layer_checks(np, backend, ip, bundle, references, evidence):
    with np.load(bundle / "weights.npz", allow_pickle=False) as archive:
        weights = {layer: {k: archive[f"{layer}.{k}"].copy() for k in ("w1", "b1", "w2", "b2")}
                   for layer in LAYERS}
    with np.load(references / "split-ort_0_0.npz", allow_pickle=False) as archive:
        cuts = {layer: (archive[f"input_{layer}"].reshape(-1, 128).copy(),
                        archive[f"ffn_{layer}"].reshape(-1, 128).copy()) for layer in LAYERS}
    rows = []
    # Camera cuts really contain 660 rows; cross cuts contain only four. Never tile
    # four rows and mislabel them as 659/660 real cross tokens.
    for repeat, order in enumerate((LAYERS, tuple(reversed(LAYERS)), LAYERS)):
        for layer in order:
            x, y = cuts[layer]
            tokens = (1, 4, 659, 660) if len(x) == 660 else (1, 4)
            for count in tokens:
                out = backend.forward(layer, x[:count], weights[layer])
                row = {"layer": layer, "tokens": count, "rotation": repeat,
                       "max_abs_error": compare(np, out, y[:count], 1e-4),
                       "timing_ms": dict(backend.last_timing)}
                rows.append(row)
                evidence.event("layer", row)
    gates = [("zero", "camera_0", np.zeros((0, 128), np.float32)),
             ("661", "camera_0", np.zeros((661, 128), np.float32)),
             ("float64", "camera_0", np.zeros((1, 128), np.float64)),
             ("nan", "camera_0", np.full((1, 128), np.nan, np.float32)),
             ("last_dimension", "camera_0", np.zeros((1, 127), np.float32)),
             ("unknown_layer", "unknown", np.zeros((1, 128), np.float32))]
    for label, layer, x in gates:
        before = ip.counts()
        try:
            backend.forward(layer, x, weights["camera_0"])
        except ValueError:
            pass
        else:
            raise AssertionError(f"Illegal Python input accepted: {label}")
        if before != ip.counts():
            raise AssertionError(f"Illegal Python input reached MMIO: {label}")
        evidence.event("python_gate", {"name": label, "no_mmio": True})
    # Existing backend checks cached-weight changes after an idle read. Record this
    # honestly (not as a no-MMIO Python input gate); require zero register WRITES.
    changed = dict(weights["camera_0"])
    changed["b2"] = changed["b2"] + np.float32(1)
    before = ip.counts()
    try:
        backend.forward("camera_0", cuts["camera_0"][0][:1], changed)
    except ValueError:
        pass
    else:
        raise AssertionError("Changed cached weights accepted")
    if ip.writes != before[1]:
        raise AssertionError("Changed cached weights wrote registers")
    return {"cases": len(rows), "rows": rows, "python_gates_no_mmio": len(gates),
            "weight_change_guard_no_writes": True,
            "cross_659_660": "未执行：真实cross切点仅4 tokens，不复制伪造"}


def run_frame(runtime, images, measured):
    if measured is not None:
        measured.rows.clear()
    started = time.perf_counter()
    outputs = runtime.run(images)
    run_ms = (time.perf_counter() - started) * 1000
    stages = dict(runtime.last_timings_ms)
    return outputs, {"run_total_ms": run_ms, "runtime_total_ms": stages["total"],
                     "stage_ms": stages,
                     "ps_remaining_stage_ms": sum(v for k, v in stages.items() if k.startswith("stage_")),
                     "ffn_components": list(measured.rows) if measured else []}


def regression(np, factory, bundle, datasets, preprocess, threads, measured, references, evidence):
    completed = []
    with runtime_owner(factory, bundle, "split-numpy", threads, measured) as runtime:
        for ds, records in enumerate(datasets):
            for index, record in enumerate(records):
                started = time.perf_counter()
                images = preprocess(record)
                preprocess_ms = (time.perf_counter() - started) * 1000
                out, timing = run_frame(runtime, images, measured)
                row = {"dataset": ds, "index": index, "segment": record.segment,
                       "frame_index": record.frame_index, "csv_line": record.csv_line,
                       "preprocess_ms": preprocess_ms, "timing": timing,
                       "outputs": {k: v.tolist() for k, v in out.items()},
                       "recorded_joystick_not_accuracy": [record.recorded_left, record.recorded_right]}
                with np.load(references / f"golden_{ds}_{index}.npz", allow_pickle=False) as ref:
                    row["output_errors_vs_golden"] = {k: compare(np, out[k], ref[f"out_{k}"], 1e-3) for k in OUTPUTS}
                    compare_integer(np, out["speed_raw"], ref["out_speed_raw"])
                with np.load(references / f"split-ort_{ds}_{index}.npz", allow_pickle=False) as ref:
                    row["cumulative_cut_errors"] = {k: compare(np, runtime.last_ffn_outputs[k], ref[f"ffn_{k}"], 1e-3) for k in LAYERS}
                row["integer_speed_exact_match"] = True
                evidence.event("regression_frame", row)
            completed.append(len(records))
    del runtime
    gc.collect()
    return {"frames_completed": completed, "output_atol": 1e-3, "cumulative_cut_atol": 1e-3,
            "rtol": 0, "integer_speed_exact_match": True}


def stats(np, values):
    array = np.asarray(values, dtype=np.float64)
    if not len(array) or not np.isfinite(array).all():
        raise ValueError("Invalid timing samples")
    return {"n": len(array), "mean": float(array.mean()), "p50": float(np.percentile(array, 50)),
            "p95": float(np.percentile(array, 95)), "p99": float(np.percentile(array, 99)), "max": float(array.max())}


def performance(np, factory, bundle, records, preprocess, thread_counts, warmup, measured, evidence):
    summaries = []
    for threads in thread_counts:
        for mode in ("golden", "split-ort", "split-pl"):
            pl = measured if mode == "split-pl" else None
            init = time.perf_counter()
            with runtime_owner(factory, bundle, "split-numpy" if pl else mode, threads, pl) as runtime:
                init_ms = (time.perf_counter() - init) * 1000
                # Identical warmup count and input sequence for every mode/threads.
                for index in range(warmup):
                    run_frame(runtime, preprocess(records[index % len(records)]), pl)
                run_times, offline_times, preprocess_times, stages_all = [], [], [], []
                completed_at = []
                prepare, wait, readback, remaining = [], [], [], []
                started = time.perf_counter()
                for index, record in enumerate(records):
                    before = time.perf_counter()
                    images = preprocess(record)
                    prep_ms = (time.perf_counter() - before) * 1000
                    _, timing = run_frame(runtime, images, pl)
                    completed_at.append(time.perf_counter())
                    offline_ms = (completed_at[-1] - before) * 1000
                    row = {"mode": mode, "threads": threads, "index": index,
                           "preprocess_disk_ms": prep_ms, "offline_e2e_ms": offline_ms, **timing}
                    evidence.event("performance_frame", row)
                    run_times.append(timing["run_total_ms"])
                    offline_times.append(offline_ms)
                    preprocess_times.append(prep_ms)
                    stages_all.append(timing["stage_ms"])
                    remaining.append(timing["ps_remaining_stage_ms"])
                    for key, target in (("prepare_ms", prepare), ("hardware_wait_ms", wait), ("readback_ms", readback)):
                        target.append(sum(v[key] for v in timing["ffn_components"]))
                duration = time.perf_counter() - started
                summary = {"mode": mode, "threads": threads, "warmup_n": warmup, "measured_n": len(records),
                           "session_initialization_ms_excluded": init_ms,
                           "run_total_ms": stats(np, run_times), "offline_e2e_ms": stats(np, offline_times),
                           "preprocess_disk_ms": stats(np, preprocess_times),
                           "ps_remaining_stage_ms": stats(np, remaining),
                           "stage_ms": {k: stats(np, [v[k] for v in stages_all]) for k in stages_all[0]},
                           "completion_interval_ms_including_evidence_io": stats(np, np.diff(completed_at) * 1000),
                           "run_duration_s_including_evidence_io": duration,
                           "sustained_offline_throughput_hz": len(records) / duration,
                           "compute_only_throughput_hz": len(records) * 1000 / sum(run_times),
                           "offline_e2e_scope": "disk decode + unchanged preprocessing + Runtime.run; excludes evidence fsync",
                           "run_total_scope": "Runtime.run including FFN instrumentation/cut copies; excludes preprocessing/startup",
                           "interval_scope": "sequential recorded inference; no live capture timestamps or realtime freshness"}
                if pl:
                    summary["ffn_prepare_ms"] = stats(np, prepare)
                    summary["ffn_wait_ms"] = stats(np, wait)
                    summary["ffn_readback_ms"] = stats(np, readback)
                summaries.append(summary)
                evidence.event("performance_summary", summary)
            del runtime
            gc.collect()
    return {"conditions": summaries, "fairness": "one live Runtime owner; same input order, warmup, n, ORT settings per threads; OpenCV threads=1"}


def quarantine(evidence, backend, error, sleeper=time.sleep):
    """Never close/free/exit. No automatic reset. Strong references are intentional."""
    LIVE.append(backend)
    if isinstance(getattr(backend, "ip", None), CountedIP):
        evidence.report.update(hardware_execution=bool(backend.ip.starts), compute_executed=bool(backend.ip.starts),
                               kernel_starts_observed=backend.ip.starts)
    for sig in (signal.SIGINT, signal.SIGTERM, *([signal.SIGHUP] if hasattr(signal, "SIGHUP") else [])):
        signal.signal(sig, signal.SIG_IGN)
    evidence.report.update(status="失败", failure_mode="failed_quarantined",
                           error_type=type(error).__name__, error=str(error),
                           operator_action="Keep process alive. Separately authorize and verify hardware-safe reset before termination.")
    evidence.report["phases"][evidence.phase] = {"status": "失败", "date_utc": now(), "quarantined": True}
    # Even a full/read-only evidence disk must not unwind an in-flight process.
    # Retry persistence while retaining ownership; never turn an IO failure into exit.
    while True:
        try:
            evidence.event("failed_quarantined", evidence.report)
            evidence.save()
            break
        except BaseException as persistence_error:
            try:
                print(f"FAILED_QUARANTINED: evidence write failed: {persistence_error}; retained, retrying", file=sys.stderr, flush=True)
                sleeper(60)
            except BaseException:
                pass
    try:
        print("FAILED_QUARANTINED: process retained; do NOT kill as recovery. Verify safe PL reset first.", flush=True)
    except BaseException:
        pass
    while True:
        try:
            sleeper(60)
        except BaseException:
            # Interrupting a Python wait is not a safe AXI-master cancellation.
            continue


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", required=True, help="NEW deployment root with ps/, bundle/, overlay/")
    p.add_argument("--deployment-manifest", required=True, help="EXTERNAL JSON: schema ffn_board.deployment.v1, files relative to root")
    p.add_argument("--manifest-sha256", required=True)
    p.add_argument("--bit", default="overlay/ffn_overlay_wrapper.bit", help="root-relative bit; adjacent same-stem HWH required")
    p.add_argument("--bit-sha256", required=True)
    p.add_argument("--hwh-sha256", required=True)
    p.add_argument("--output-dir", required=True, help="New directory only; never overwrite evidence")
    p.add_argument("--allow-pl", action="store_true", help="Explicit PYNQ import, one overlay download and repeated kernel execution authorization")
    p.add_argument("--stage", choices=("correctness", "performance", "all"), default="all")
    p.add_argument("--ip-name", default="ffn_accel_0")
    p.add_argument("--recording", action="append", default=[], help="Read-only recording directory or CSV; repeat for 173/117 datasets")
    p.add_argument("--expected-frames", action="append", type=int, default=[], help="Exact full CSV count for corresponding --recording; no silent truncation")
    p.add_argument("--threads", nargs="+", type=int, choices=(1, 4), default=[1, 4])
    p.add_argument("--warmup", type=int, default=3)
    p.add_argument("--timing-frames", type=int, help="N>=30, default min(117, dataset size)")
    p.add_argument("--timing-recording-index", type=int, default=-1, help="0-based; default last recording")
    p.add_argument("--expected-clock-mhz", type=float, default=100.0,
                   help="Actual fclk0 getter must match before backend construction or allocation; no clock changes")
    p.add_argument("--clock-tolerance-percent", type=float, default=0.5)
    p.add_argument("--timeout-s", type=float, default=60.0)
    a = p.parse_args(argv)
    for value in (a.manifest_sha256, a.bit_sha256, a.hwh_sha256):
        if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            p.error("SHA256 values must be 64 lowercase hex characters")
    if len(a.recording) != len(a.expected_frames) or any(v < 1 for v in a.expected_frames):
        p.error("Each recording requires its exact positive --expected-frames")
    if a.allow_pl and not a.recording:
        p.error("PL validation requires read-only recording inputs")
    if 4 not in a.threads or len(a.threads) != len(set(a.threads)):
        p.error("At least threads=4 is required; conditions must be unique")
    if a.warmup < 1 or not math.isfinite(a.timeout_s) or a.timeout_s <= 0:
        p.error("warmup and timeout must be positive and finite")
    if not math.isfinite(a.expected_clock_mhz) or a.expected_clock_mhz <= 0:
        p.error("expected-clock-mhz must be positive and finite")
    if not math.isfinite(a.clock_tolerance_percent) or not 0 <= a.clock_tolerance_percent <= 0.5:
        p.error("clock-tolerance-percent must be finite and between 0 and 0.5")
    if a.timing_frames is not None and a.timing_frames < 30:
        p.error("Timing requires N>=30; n=1 is not a performance result")
    return a


def main(argv=None):
    args = parse_args(argv)
    evidence = Evidence(args.output_dir, args)
    backend = overlay = ip = None
    try:
        root, bit, registers, static = static_check(args)
        evidence.finish("static", static)
        if not args.allow_pl:
            evidence.report.update(status="已验证", scope="Static verification ONLY; --allow-pl absent; no deployed-code/PYNQ imports or hardware access")
            evidence.save()
            return 0
        evidence.start("authorization")
        if platform.system() != "Linux" or platform.machine().lower() not in ("aarch64", "arm64"):
            raise PermissionError("PL mode requires target AArch64 Linux; CPU/mock tests do not use --allow-pl")
        if os.geteuid() != 0:
            raise PermissionError("PL mode requires explicit privileged launch; no sudo/password escalation in script")
        evidence.finish("authorization", {"allow_pl": True, "effective_uid": os.geteuid()})
        evidence.start("inputs")
        np, factory, read_records, preprocess, backend_cls, validator = load_project(root)
        validator(registers)
        datasets = [read_records([path]) for path in args.recording]
        for index, (records, expected) in enumerate(zip(datasets, args.expected_frames)):
            if len(records) != expected:
                raise ValueError(f"dataset {index}: actual {len(records)} != expected {expected}")
            files = {str(p): sha(p) for r in records for p in (r.left, r.front, r.right)}
            selected = Path(args.recording[index])
            csv_path = selected / "auto_data.csv" if selected.is_dir() else selected
            evidence.event("dataset", {"index": index, "frames": len(records), "csv_sha256": sha(csv_path), "images_sha256": files})
        timing_index = args.timing_recording_index
        if not -len(datasets) <= timing_index < len(datasets):
            raise ValueError("Timing recording index outside selected recordings")
        timing_records = datasets[timing_index]
        n = args.timing_frames if args.timing_frames is not None else min(117, len(timing_records))
        if args.stage in ("performance", "all") and (n < 30 or n > len(timing_records)):
            raise ValueError("Insufficient exact timing records (requires 30<=N<=available)")
        evidence.finish("inputs", {"frames_actual": list(map(len, datasets)), "timing_n": n,
                                   "recordings_read_only": True})
        evidence.start("hardware")
        # Sole PYNQ import / sole overlay programming site; all static/data gates passed.
        from pynq import Overlay, Clocks
        before = read_memory()
        overlay = Overlay(str(bit))
        evidence.report.update(overlay_programmed=True, hardware_programmed=True)
        LIVE.append(overlay)
        actual_regs = overlay.ip_dict[args.ip_name]["registers"]
        validator(actual_regs)
        ip = CountedIP(getattr(overlay, args.ip_name))
        validated_clock = validate_clock(Clocks, args, evidence, ip, "before_backend_and_first_start")
        backend = backend_cls(ip, actual_regs, timeout_s=args.timeout_s)
        LIVE.append(backend)
        control = int(ip.read(0))
        if not control & 4 or control & 0x81:
            raise RuntimeError("Actual control not idle or start/auto_restart set after load")
        measured = MeasuredBackend(backend)
        evidence.report["overlay_programmed"] = True
        evidence.finish("hardware", {"ctrl_raw": hex(control), "idle": True, "cma_before": before,
                                     "cma_after": read_memory(), "overlay_clock_dict": overlay.clock_dict,
                                     "actual_fclk0_mhz": validated_clock, "overlay_loads": 1,
                                     "postload_registers": actual_regs})
        if args.stage in ("correctness", "all"):
            evidence.start("references")
            refs = make_references(np, factory, root / "bundle", datasets, preprocess, 4, evidence)
            evidence.finish("references", {"threads": 4, "frames": list(map(len, datasets)),
                                           "modes": ["golden", "split-ort"], "reference_directory": refs})
            evidence.start("layers")
            evidence.finish("layers", layer_checks(np, backend, ip, root / "bundle", refs, evidence))
            evidence.start("regression")
            evidence.finish("regression", regression(np, factory, root / "bundle", datasets, preprocess, 4, measured, refs, evidence))
        if args.stage in ("performance", "all"):
            evidence.start("performance")
            evidence.finish("performance", performance(np, factory, root / "bundle", timing_records[:n], preprocess,
                                                       args.threads, args.warmup, measured, evidence))
        validate_clock(Clocks, args, evidence, ip, "endcheck", initial=validated_clock)
        # No private-driver state changes. close() itself refuses uncertain completion.
        control = int(ip.read(0))
        if not control & 4 or control & 0x81:
            quarantine(evidence, backend, RuntimeError("Final control is not safely idle"))
        backend.close()
        evidence.report.update(status="已验证", completed_utc=now(), cma_after=read_memory(),
                               kernel_starts_observed=ip.starts)
        evidence.report["hardware_execution"] = bool(ip.starts)
        evidence.report["compute_executed"] = bool(ip.starts)
        evidence.save()
        return 0
    except BaseException as error:
        if ip is not None:
            evidence.report.update(hardware_execution=bool(ip.starts), compute_executed=bool(ip.starts),
                                   kernel_starts_observed=ip.starts)
        if backend is not None:
            # close() is the existing driver's public quarantine guard. Never override
            # _inflight/_poisoned or free anything ourselves. Extra idle check covers
            # a foreign/unexpected busy master, even when our driver has no inflight.
            try:
                control = int(ip.read(0))
                if not control & 4 or control & 0x81:
                    quarantine(evidence, backend, error)
                backend.close()
            except BaseException:
                quarantine(evidence, backend, error)
        evidence.report.update(status="失败", error_type=type(error).__name__, error=str(error))
        evidence.report["phases"][evidence.phase] = {"status": "失败", "date_utc": now()}
        evidence.event("failure", evidence.report)
        evidence.save()
        print(f"FAILED: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
