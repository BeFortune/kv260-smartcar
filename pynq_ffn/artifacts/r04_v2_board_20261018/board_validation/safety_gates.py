"""Safety gates copied unchanged from retest100; no download or clock setters.
Source SHA is recorded in provenance.json. Not a hardware validation result.
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

LIVE = []

class ClockMismatchError(RuntimeError):
    pass

def snapshot_digest(snapshot):
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode("utf8")).hexdigest()

def require_idle(control):
    if not control & 4 or control & 0x81:
        raise RuntimeError("Control must be idle with ap_start and auto_restart clear")

def read_clock_snapshot(clocks, ip):
    # Installed pynq/ps.py: PL0..3 in CRL, IOPLL/RPLL in CRL, A/D/VPLL in CRF.
    instance = clocks._instance
    crl, crf = instance._crl_registers, instance._crf_registers
    return {"ctrl": int(ip.read(0)),
            "pl_raw": [int(reg[:]) for reg in instance.PL_CLK_CTRLS],
            "pll_raw": {name: int(getattr(bank, name)[:]) for bank, names in
                        ((crl, ("IOPLL_CTRL", "RPLL_CTRL")),
                         (crf, ("APLL_CTRL", "DPLL_CTRL", "VPLL_CTRL"))) for name in names},
            "fclk0_mhz": float(clocks.fclk0_mhz),
            "source_mhz": float(instance.get_pll_mhz(crl.IOPLL_CTRL))}

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
           "hardware_programmed": False, "reused_programmed_overlay": True, "compute_executed": bool(ip.starts),
           "kernel_starts_observed": ip.starts,
           "source": "actual pynq.Clocks.fclk0_mhz getter; no automatic clock writes",
           "status": "已验证" if expected_ok and initial_ok else "失败"}
    evidence.report.setdefault("clock_checks", []).append(row)
    evidence.report.update(hardware_programmed=False, compute_executed=bool(ip.starts),
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

def verify_loaded_bitfile(bitfile_name, expected_sha256):
    """Hash the runtime-reported host file, NOT FPGA configuration readback."""
    if not isinstance(bitfile_name, (str, os.PathLike)) or not str(bitfile_name):
        raise RuntimeError("Runtime has no loaded bitfile path")
    path = Path(bitfile_name).resolve(strict=True)
    if not path.is_file():
        raise RuntimeError("Runtime loaded bitfile path is not a readable regular file")
    actual_sha256 = sha(path)
    if actual_sha256 != expected_sha256:
        raise RuntimeError("Runtime loaded bitfile file SHA256 mismatch")
    return {"runtime_bitfile_name": str(bitfile_name), "resolved_path": str(path),
            "file_sha256": actual_sha256,
            "authority": "runtime-reported host file SHA256 only; NOT FPGA configuration readback/equivalence"}

def reuse_overlay(overlay_cls, pl, args, evidence):
    from pynq.pl_server import global_state
    # Capture the JSON BEFORE PL getters (which can recompute its SHA1) and BEFORE
    # Overlay.__init__ overwrites device.bitfile_name even with download=False.
    state_path = Path(global_state.STATE_DIR) / "global_pl_state.json"
    raw_bytes = state_path.read_bytes()
    state = json.loads(raw_bytes)
    row = {"global_state_path": str(state_path), "global_state_raw": raw_bytes.decode("utf8"),
           "global_state_file_sha256": hashlib.sha256(raw_bytes).hexdigest(),
           "global_state": state,
           "authority": "runtime bookkeeping and host files only; NOT FPGA configuration readback"}
    evidence.report["reused_overlay_identity"] = row
    evidence.event("runtime_identity_before_overlay", row)
    bitfile_name, timestamp = pl.bitfile_name, pl.timestamp
    row.update(pl_bitfile_name=bitfile_name, pl_timestamp=timestamp)
    evidence.event("pl_identity_before_overlay", row)
    identity = verify_loaded_bitfile(bitfile_name, args.bit_sha256)
    recorded = verify_loaded_bitfile(state.get("bitfile_name"), args.bit_sha256)
    if identity["resolved_path"] != recorded["resolved_path"] or not timestamp or timestamp != state.get("timestamp"):
        raise RuntimeError("Preconstruction PL/global-state identity or timestamp mismatch")
    # Persisted SHA1 is bookkeeping, checked against this host file, not PL content.
    loaded = Path(identity["resolved_path"])
    file_sha1 = hashlib.sha1(loaded.read_bytes()).hexdigest()
    if file_sha1 != state.get("bitfile_hash"):
        raise RuntimeError("Persisted runtime SHA1 does not match loaded host bitfile")
    hwh = loaded.with_suffix(".hwh")
    if sha(hwh) != args.hwh_sha256:
        raise RuntimeError("Loaded-path HWH SHA256 mismatch")
    row.update(identity=identity, loaded_hwh_sha256=sha(hwh), loaded_file_sha1=file_sha1,
               verification="host file SHA256 agrees with externally pinned deployment manifest; no PL readback")
    evidence.event("runtime_identity_verified_before_overlay", row)
    # Sole constructor. Never download or gen_cache; retain the existing loaded path.
    overlay = overlay_cls(str(loaded), download=False)
    evidence.report.update(overlay_programmed=False, hardware_programmed=False,
                           reused_programmed_overlay=True, overlay_loads=0)
    return overlay

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

def compare_integer(np, actual, expected):
    if not np.isfinite(actual).all() or not np.isfinite(expected).all():
        raise AssertionError("Non-finite integer speed source")
    # Python int is the reviewed C++ truncation convention, no packet is sent.
    if [int(v) for v in actual.flat] != [int(v) for v in expected.flat]:
        raise AssertionError("Integer speed mismatch against golden")

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

def stats(np, values):
    array = np.asarray(values, dtype=np.float64)
    if not len(array) or not np.isfinite(array).all():
        raise ValueError("Invalid timing samples")
    return {"n": len(array), "mean": float(array.mean()), "p50": float(np.percentile(array, 50)),
            "p95": float(np.percentile(array, 95)), "p99": float(np.percentile(array, 99)), "max": float(array.max())}

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
