#!/usr/bin/env python3
"""Recorded-input r04v2 acceptance. Default static-only; NEVER programs or resets PL.
Only an explicitly authorized AArch64/root worker imports PYNQ. PL must already be
programmed after system acceptance. A quarantined worker NEVER exits or frees DMA
buffers; parent NEVER kills workers. No camera/serial/vehicle/clock writes.
"""
from __future__ import annotations

import argparse
import gc
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import time

sys.dont_write_bytecode = True
import safety_gates as g

RUNTIME_SHA = "49ace8fbf61a29e2e54efc174b45477cd08fc5fe469d13547df17326b9745576"
MODES = ("golden4", "split_ort4", "split_pl4")


def write_json(path, value):
    with Path(path).open("x", encoding="utf8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False, default=str)
        f.flush()
        os.fsync(f.fileno())


def acceptance(args):
    path = Path(args.system_acceptance).resolve(strict=True)
    if g.sha(path) != args.acceptance_sha256:
        raise ValueError("System acceptance SHA mismatch")
    value = json.loads(path.read_text(encoding="utf8"))
    if (value.get("schema") != "r04v2.system_acceptance.v1" or value.get("status") != "已验证"
            or value.get("bit_sha256") != args.bit_sha256 or value.get("hwh_sha256") != args.hwh_sha256):
        raise ValueError("System acceptance not verified for these exact bit/HWH")
    files = value.get("evidence_files", {})
    if not files:
        raise ValueError("System acceptance requires original report evidence")
    for name, digest in files.items():
        if g.sha(g.confined(path.parent, name)) != digest:
            raise ValueError("System report hash mismatch: " + name)
    return {"sha256": g.sha(path), "confirmation": value,
            "authority": "Externally reviewed system-acceptance attestation; file hashes are NOT proof of Vivado success or PL readback"}


def static(args):
    root, bit, registers, report = g.static_check(args)
    if g.sha(root / "ps/runtime.py") != RUNTIME_SHA:
        raise ValueError("Production Runtime SHA changed")
    report["system_acceptance"] = acceptance(args)
    report["runtime_sha256"] = RUNTIME_SHA
    report["harness_sha256"] = g.sha(__file__)
    report["safety_gates_sha256"] = g.sha(Path(__file__).with_name("safety_gates.py"))
    return root, bit, registers, report


def clock_snapshot(clocks, ip, args, evidence, checkpoint, initial=None):
    # These are reads of the same installed PYNQ clock register map captured by v3.
    value = g.read_clock_snapshot(clocks, ip)
    value["cpu_raw"] = {"ACPU_CTRL": int(clocks._instance._crf_registers.ACPU_CTRL[:])}
    value["cpu_mhz"] = float(clocks.cpu_mhz)
    evidence.event("clock_register_snapshot", {"checkpoint": checkpoint, **value})
    g.require_idle(value["ctrl"])
    actual = g.validate_clock(clocks, args, evidence, ip, checkpoint,
                              initial=None if initial is None else initial["fclk0_mhz"])
    value["fclk0_mhz"] = actual
    if initial is not None:
        for key in ("pl_raw", "pll_raw", "cpu_raw"):
            if value[key] != initial[key]:
                raise g.ClockMismatchError("Clock/PLL/CPU registers changed: " + key)
    return value


def require_target():
    if platform.system() != "Linux" or platform.machine().lower() not in ("aarch64", "arm64"):
        raise PermissionError("Authorized board workers require AArch64 Linux")
    if os.geteuid() != 0:
        raise PermissionError("Explicit privileged launch required; no automatic escalation")


def load_inputs(root, args, evidence):
    np, factory, read_records, preprocess, backend_cls, validator = g.load_project(root)
    records = read_records([args.recording])
    if len(records) != 117:
        raise ValueError(f"Formal recording must contain EXACTLY 117 frames, got {len(records)}")
    csv = Path(args.recording)
    csv = csv / "auto_data.csv" if csv.is_dir() else csv
    fingerprints = {str(p): g.sha(p) for r in records for p in (r.left, r.front, r.right)}
    data = {"frames": 117, "csv_sha256": g.sha(csv), "images_sha256": fingerprints}
    data["digest"] = g.snapshot_digest(data)
    if args.input_digest is not None and data["digest"] != args.input_digest:
        raise ValueError("Dataset changed between isolated workers")
    evidence.report["inputs"] = data
    evidence.event("inputs", data)
    return np, factory, records, preprocess, backend_cls, validator


def reference_path(base, mode, index):
    return Path(base) / mode / "references" / f"frame_{index:03d}.npz"


def validate_reference_index(args):
    path = Path(args.reference_index)
    if g.sha(path) != args.reference_index_sha256:
        raise ValueError("Reference index hash mismatch")
    index = json.loads(path.read_text(encoding="utf8"))
    for rel, digest in index.items():
        if g.sha(g.confined(path.parent.resolve(), rel)) != digest:
            raise ValueError("Prior process reference changed: " + rel)


def check_outputs(np, out, runtime, args, index):
    errors = {}
    with np.load(reference_path(args.run_root, "golden4", index), allow_pickle=False) as ref:
        for name in g.OUTPUTS:
            errors[name] = g.compare(np, out[name], ref["out_" + name], 1e-3)
        g.compare_integer(np, out["speed_raw"], ref["out_speed_raw"])
    cuts = {}
    if args.mode == "split_pl4":
        with np.load(reference_path(args.run_root, "split_ort4", index), allow_pickle=False) as ref:
            cuts = {name: g.compare(np, runtime.last_ffn_outputs[name], ref["ffn_" + name], 1e-3)
                    for name in g.LAYERS}
    return {"output_errors_atol_1e_3_rtol_0": errors, "integer_speed_equal": True,
            "cumulative_cut_errors_atol_1e_3_rtol_0": cuts}


def layer_checks(np, backend, ip, weights, args, evidence):
    rows = []
    # One real token first, before any 660-token invocation.
    with np.load(reference_path(args.run_root, "split_ort4", 0), allow_pickle=False) as ref:
        x = ref["input_camera_0"].reshape(-1, 128)[:1].copy()
        expected = ref["ffn_camera_0"].reshape(-1, 128)[:1]
        out = backend.forward("camera_0", x, weights["camera_0"])
        evidence.event("one_token_smoke", {"max_abs_error": g.compare(np, out, expected, 1e-4),
                                           "kernel_starts": ip.starts, "timing_ms": backend.last_timing})
    for index, order in zip((0, 58, 116), (g.LAYERS, tuple(reversed(g.LAYERS)), g.LAYERS)):
        with np.load(reference_path(args.run_root, "split_ort4", index), allow_pickle=False) as ref:
            for name in order:
                x, expected = ref["input_" + name].reshape(-1, 128), ref["ffn_" + name].reshape(-1, 128)
                for count in ((1, 4, 659, 660) if len(x) == 660 else (1, 4)):
                    out = backend.forward(name, x[:count].copy(), weights[name])
                    row = {"record_index": index, "layer": name, "tokens": count,
                           "max_abs_error": g.compare(np, out, expected[:count], 1e-4),
                           "timing_ms": dict(backend.last_timing), "kernel_starts": ip.starts}
                    rows.append(row)
                    evidence.event("single_layer", row)
    for label, name, x in (("zero", "camera_0", np.zeros((0,128),np.float32)),
                           ("661", "camera_0", np.zeros((661,128),np.float32)),
                           ("float64", "camera_0", np.zeros((1,128),np.float64)),
                           ("nan", "camera_0", np.full((1,128),np.nan,np.float32)),
                           ("last_dimension", "camera_0", np.zeros((1,127),np.float32)),
                           ("unknown_layer", "unknown", np.zeros((1,128),np.float32))):
        before = ip.counts()
        try:
            backend.forward(name, x, weights["camera_0"])
        except ValueError:
            pass
        else:
            raise AssertionError("Invalid input accepted: " + label)
        if before != ip.counts():
            raise AssertionError("Invalid input reached MMIO: " + label)
        evidence.event("invalid_input_no_mmio", {"case": label})
    return {"smoke_cases": 1, "layer_cases": len(rows), "distinct_record_inputs": 3,
            "atol": 1e-4, "rtol": 0, "python_gates": 6,
            "cross_659_660": "未执行：真实cross只有4 tokens；不tile冒充真实输入"}


def safe_cleanup(evidence, backend, ip, runtime, error=None):
    # Check uncertainty BEFORE close() can free; even a later IDLE does not absolve a timeout.
    if backend is not None:
        try:
            if getattr(backend, "_poisoned", False) or getattr(backend, "_inflight", False):
                raise RuntimeError("Backend completion uncertain")
            control = int(ip.read(0))
            g.require_idle(control)
        except BaseException as state_error:
            g.LIVE.append(runtime)
            g.quarantine(evidence, backend, error or state_error)
            raise AssertionError("Quarantine must never return")
        evidence.event("cleanup_idle", {"ctrl": control, "kernel_starts": ip.starts})
        try:
            backend.close()
        except BaseException as close_error:
            g.LIVE.append(runtime)
            g.quarantine(evidence, backend, error or close_error)
            raise AssertionError("Quarantine must never return")
        evidence.event("backend_closed_after_idle", {"kernel_starts": ip.starts})
    if runtime is not None:
        runtime.sessions.clear()
        evidence.event("ort_sessions_cleared", {})
        gc.collect()


def benchmark(np, runtime, records, preprocess, measured, args, evidence):
    ref_dir = evidence.directory / "references"
    ref_dir.mkdir(exist_ok=False)
    for i in range(3):
        g.run_frame(runtime, preprocess(records[i]), measured)
    samples, completions = [], []
    wall = time.perf_counter()
    cpu = time.process_time()
    for index, record in enumerate(records):
        started = time.perf_counter()
        images = preprocess(record)
        prep_ms = (time.perf_counter() - started) * 1000
        out, timing = g.run_frame(runtime, images, measured)
        completed = time.perf_counter()
        completions.append(completed)
        row = {"index": index, "frame_index": record.frame_index, "csv_line": record.csv_line,
               "decode_preprocess_ms": prep_ms, "offline_e2e_ms": (completed-started)*1000,
               "completion_offset_s": completed-wall, **timing}
        if args.mode != "golden4":
            row.update(check_outputs(np, out, runtime, args, index))
        arrays = {"out_" + k: value for k, value in out.items()}
        if args.mode == "split_ort4":
            arrays.update({"ffn_" + k: value for k, value in runtime.last_ffn_outputs.items()})
            if index in (0, 58, 116):
                arrays.update({"input_" + k: value for k, value in runtime.last_ffn_inputs.items()})
        g.dump_npz(np, ref_dir / f"frame_{index:03d}.npz", arrays)
        evidence.event("measured_frame", row)
        samples.append(row)
    duration, cpu_s = time.perf_counter()-wall, time.process_time()-cpu
    summary = {"mode": args.mode, "threads": 4, "spinning": None if args.mode == "golden4" else False,
               "warmup": 3, "frames": 117, "test_duration_s": duration, "process_cpu_s": cpu_s,
               "sustained_offline_outputs_hz": 117/duration,
               "run_total_only_hz": 117000/sum(r["run_total_ms"] for r in samples),
               "completion_interval_ms": g.stats(np, np.diff(completions)*1000),
               "raw_samples": samples,
               "timing_scope": "Runtime.run unchanged incl diagnostic copies; offline_e2e=disk decode+preprocess+run. Wall/interval include numerical checks, NPZ and evidence fsync. No live camera/frame-age claim."}
    for key in ("run_total_ms", "runtime_total_ms", "offline_e2e_ms", "decode_preprocess_ms", "ps_remaining_stage_ms"):
        summary[key] = g.stats(np, [r[key] for r in samples])
    summary["stage_ms"] = {k: g.stats(np, [r["stage_ms"][k] for r in samples]) for k in samples[0]["stage_ms"]}
    if measured:
        for key in ("prepare_ms", "hardware_wait_ms", "readback_ms", "total_ms"):
            summary["ffn_"+key] = g.stats(np, [sum(v[key] for v in r["ffn_components"]) for r in samples])
    return summary


def protect_lifetime_signals():
    # SIGINT remains catchable: active forward() quarantines on KeyboardInterrupt.
    # SIGTERM/SIGHUP must not bypass Python ownership checks and reclaim DMA memory.
    for sig in (signal.SIGTERM, *([signal.SIGHUP] if hasattr(signal, "SIGHUP") else [])):
        signal.signal(sig, signal.SIG_IGN)


def worker(args):
    if args.mode == "split_pl4":
        protect_lifetime_signals()
    evidence = g.Evidence(args.output_dir, args)
    evidence.report["explicit_environment"] = {key: os.environ.get(key) for key in
                                               ("XILINX_XRT", "PATH", "LD_LIBRARY_PATH", "PYTHONUNBUFFERED")}
    evidence.event("explicit_environment", evidence.report["explicit_environment"])
    backend = ip = runtime = None
    try:
        root, _, registers, checks = static(args)
        evidence.finish("static", checks)
        require_target()
        evidence.start("inputs")
        np, factory, records, preprocess, backend_cls, validator = load_inputs(root, args, evidence)
        validator(registers)
        if args.mode != "golden4":
            validate_reference_index(args)
        evidence.finish("inputs", {"frames": 117, "warmup": 3})
        measured = None
        initial = None
        if args.mode == "split_pl4":
            evidence.start("hardware")
            # Sole hardware import, only after file/authorization/input/reference gates.
            from pynq import Overlay, PL, Clocks
            overlay = g.reuse_overlay(Overlay, PL, args, evidence)
            g.LIVE.append(overlay)
            actual_regs = overlay.ip_dict[args.ip_name]["registers"]
            validator(actual_regs)
            ip = g.CountedIP(getattr(overlay, args.ip_name))
            initial = clock_snapshot(Clocks, ip, args, evidence, "before_backend_and_first_start")
            backend = backend_cls(ip, actual_regs, timeout_s=args.timeout_s)
            g.LIVE.append(backend)
            measured = g.MeasuredBackend(backend)
            evidence.finish("hardware", {"clock_snapshot": initial, "programmed_by_harness": False})
        mode = {"golden4": "golden", "split_ort4": "split-ort", "split_pl4": "split-numpy"}[args.mode]
        init = time.perf_counter()
        runtime = factory(root / "bundle", mode=mode, threads=4,
                          **({"backend": measured} if measured else {}),
                          spinning=None if args.mode == "golden4" else False)
        init_ms = (time.perf_counter()-init)*1000
        if backend:
            g.LIVE.append(runtime)
            evidence.start("layers")
            evidence.finish("layers", layer_checks(np, backend, ip, runtime.weights, args, evidence))
            clock_snapshot(Clocks, ip, args, evidence, "after_layers_before_formal", initial)
        evidence.start("performance")
        result = benchmark(np, runtime, records, preprocess, measured, args, evidence)
        result["initialization_ms_excluded"] = init_ms
        evidence.finish("performance", result)
        if backend:
            expected = 37 + (117+3)*4
            if ip.starts != expected:
                raise AssertionError(f"Expected {expected} real kernel start writes, got {ip.starts}")
            evidence.report["final_clock_snapshot"] = clock_snapshot(Clocks, ip, args, evidence, "endcheck", initial)
            evidence.report.update(kernel_starts_observed=ip.starts, compute_executed=bool(ip.starts),
                                   hardware_execution=bool(ip.starts), expected_kernel_starts=expected)
        safe_cleanup(evidence, backend, ip, runtime)
        evidence.report.update(status="已验证", mode=args.mode, finished_utc=g.now())
        evidence.save()
        return 0
    except BaseException as error:
        if ip is not None:
            evidence.report.update(kernel_starts_observed=ip.starts, hardware_execution=bool(ip.starts), compute_executed=bool(ip.starts))
        safe_cleanup(evidence, backend, ip, runtime, error)
        evidence.report.update(status="失败", error_type=type(error).__name__, error=str(error))
        evidence.event("failure", evidence.report)
        evidence.save()
        print(f"FAILED {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        return 1


def wait_without_cancellation(process, evidence):
    # No timeout, context-manager __exit__, terminate, kill, or exception-triggered exit.
    # If interrupted, keep waiting; the PL worker may be retaining AXI-owned buffers.
    while True:
        try:
            return process.wait()
        except BaseException as error:
            try:
                evidence.event("parent_interrupt_not_cancellation", {"pid": process.pid, "error": repr(error)})
            except BaseException:
                pass


def launch(args, mode, evidence, input_digest, index):
    values = dict(vars(args), mode=mode, output_dir=str(evidence.directory / mode),
                  run_root=str(evidence.directory.resolve()), input_digest=input_digest,
                  reference_index=str(index) if index else None,
                  reference_index_sha256=g.sha(index) if index else None)
    job = evidence.directory / (mode+"_job.json")
    write_json(job, values)
    command = [sys.executable, "-B", str(Path(__file__).resolve()), "--worker-job", str(job)]
    with (evidence.directory/(mode+"_stdout.log")).open("x", encoding="utf8") as out, (evidence.directory/(mode+"_stderr.log")).open("x", encoding="utf8") as err:
        process = subprocess.Popen(command, stdout=out, stderr=err)
        try:
            evidence.event("worker_started", {"mode": mode, "pid": process.pid, "command": command})
        except BaseException:
            # Even a full evidence disk after spawn cannot cancel an AXI owner.
            wait_without_cancellation(process, evidence)
            raise
        code = wait_without_cancellation(process, evidence)
    evidence.event("worker_exited", {"mode": mode, "exit_code": code})
    if code != 0:
        raise RuntimeError(f"{mode} failed with exit={code}; no next worker")
    report = json.loads((Path(values["output_dir"])/"report.json").read_text(encoding="utf8"))
    if report["status"] != "已验证":
        raise RuntimeError("Worker report not verified")
    return report


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("root", "deployment-manifest", "manifest-sha256", "bit-sha256", "hwh-sha256",
                 "system-acceptance", "acceptance-sha256", "output-dir"):
        p.add_argument("--"+name, required=True)
    p.add_argument("--bit", default="overlay/ffn_overlay_wrapper.bit")
    p.add_argument("--recording", help="Read-only CSV or directory containing EXACTLY117 frames")
    p.add_argument("--allow-pl", action="store_true")
    p.add_argument("--reuse-programmed-overlay", action="store_true")
    p.add_argument("--ip-name", default="ffn_accel_0")
    p.add_argument("--timeout-s", type=float, default=60.)
    a = p.parse_args(argv)
    for name in ("manifest_sha256", "bit_sha256", "hwh_sha256", "acceptance_sha256"):
        value = getattr(a, name)
        if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            p.error("SHA256 must be lowercase hex64")
    if not 0 < a.timeout_s < float("inf"):
        p.error("timeout must be positive finite")
    if a.allow_pl and (not a.reuse_programmed_overlay or not a.recording):
        p.error("PL authorization requires --reuse-programmed-overlay and --recording")
    a.expected_clock_mhz, a.clock_tolerance_percent = 100., .5
    a.mode, a.run_root, a.input_digest = None, None, None
    a.reference_index, a.reference_index_sha256 = None, None
    return a


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--worker-job"]:
        if len(argv) != 2:
            raise ValueError("Invalid worker arguments")
        args = argparse.Namespace(**json.loads(Path(argv[1]).read_text(encoding="utf8")))
        if not args.allow_pl or not args.reuse_programmed_overlay or args.mode not in MODES:
            raise PermissionError("Worker requires explicit authorization")
        return worker(args)
    args = parse_args(argv)
    evidence = g.Evidence(args.output_dir, args)
    try:
        _, _, _, checks = static(args)
        evidence.finish("static", checks)
        if args.allow_pl:
            require_target()
            protect_lifetime_signals()
            reports = {}
            digest = None
            index = None
            for mode in MODES:
                report = launch(args, mode, evidence, digest, index)
                reports[mode] = {"report_sha256": g.sha(evidence.directory/mode/"report.json"),
                                 "performance": report["phases"]["performance"]}
                digest = report["inputs"]["digest"]
                if mode != "split_pl4":
                    index = evidence.directory/(mode+"_reference_index.json")
                    members = {p.relative_to(evidence.directory).as_posix(): g.sha(p)
                               for prior in reports for p in (evidence.directory/prior/"references").glob("*.npz")}
                    write_json(index, members)
            evidence.report["isolated_modes"] = reports
            evidence.report["hardware_execution"] = True
        evidence.report.update(status="已验证", scope="isolated recorded benchmark" if args.allow_pl else "static ONLY; no project/PYNQ import")
        evidence.save()
        return 0
    except BaseException as error:
        evidence.report.update(status="失败", error_type=type(error).__name__, error=str(error))
        evidence.event("failure", evidence.report)
        evidence.save()
        print(f"FAILED {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
