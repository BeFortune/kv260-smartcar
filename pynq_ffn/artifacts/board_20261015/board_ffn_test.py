#!/usr/bin/env python3
"""KV260 on-board FFN PL bring-up: contract check, per-layer ORT comparison, hybrid replay.

Stages (each is a separate process, overlay reloads then):
  contract  - load overlay, verify HWH registers via ps.pynq_backend, check idle/clock
  layers    - real-weights 1/4/full-token comparisons for all four layers + guards
  hybrid    - N frames through split-numpy+PL, compare vs split-ort and recorded truth
  timing    - warmup then N frames via PL-hybrid and CPU split-ort, collect per-stage ms

Usage: python3 board_ffn_test.py --stage <name> [--frames N]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path("/home/ubuntu/ffn_migrate")
sys.path.insert(0, str(ROOT))

BIT_SHA256 = "8e22a7ffbc308c7741c697cb06d6c3d9c6faa4d886d23cf236014e5c9cb47409"
HWH_SHA256 = "bc305bab1cf1201bd3182aab2872694fa3d31e7ba54334fbc8e5ed2507fdcf70"
IP_NAME = "ffn_accel_0"
LAYERS = ("cross_0", "cross_1", "camera_0", "camera_1")

RESULTS = ROOT / "results"
RESULTS.mkdir(parents=True, exist_ok=True)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cma_kb() -> dict:
    info = {}
    for line in open("/proc/meminfo"):
        if line.startswith(("CmaTotal:", "CmaFree:")):
            key, val = line.split(":")
            info[key.strip()] = int(val.strip().split()[0])
    return info


def record(name: str, payload: dict):
    path = RESULTS / name
    if path.exists():
        raise FileExistsError(f"results file must be new: {path}")
    with open(path, "x", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    print(f"[saved] {path}")


def load_overlay():
    from pynq import Overlay
    bit = ROOT / "overlay/ffn_overlay_wrapper.bit"
    hwh = bit.with_suffix(".hwh")
    bit_sha, hwh_sha = sha256_file(bit), sha256_file(hwh)
    checks = {"bit_sha256": bit_sha, "hwh_sha256": hwh_sha,
              "bit_sha_ok": bit_sha == BIT_SHA256, "hwh_sha_ok": hwh_sha == HWH_SHA256}
    if not checks["bit_sha_ok"] or not checks["hwh_sha_ok"]:
        raise ValueError("overlay bit/hwh hashes do not match the release manifest; refusing to load")
    started = time.perf_counter()
    overlay = Overlay(str(bit))
    seconds = time.perf_counter() - started
    if IP_NAME not in overlay.ip_dict:
        raise KeyError(f"IP {IP_NAME} not found; actual: {list(overlay.ip_dict)}")
    ip = getattr(overlay, IP_NAME)
    registers = overlay.ip_dict[IP_NAME]["registers"]
    from ps.pynq_backend import validate_registers
    validate_registers(registers)
    status = int(ip.read(0x00))
    info = {
        "checks": checks,
        "load_seconds": seconds,
        "ip_names": list(overlay.ip_dict),
        "phys_addr": overlay.ip_dict[IP_NAME].get("phys_addr"),
        "ctrl_raw_after_load": f"{status:#010x}",
        "idle_bit_set": bool(status & 0x4),
        "start_bit_clear": not (status & 0x1),
        "done_bit_clear": not (status & 0x2),
        "cma": cma_kb(),
    }
    try:
        info["clocks"] = {str(k): str(v) for k, v in overlay.clock_dict.items()}
    except Exception as error:  # best effort only
        info["clocks_error"] = repr(error)
    print(json.dumps({k: v for k, v in info.items() if k != "checks"}, ensure_ascii=False))
    return overlay, ip, registers, info


def stage_contract():
    overlay, ip, registers, info = load_overlay()
    info["register_map_summary"] = {name: str(value) for name, value in ip.register_map.__dict__.items()
                                    if not name.startswith("_")}
    record("pl_contract_20261015.json", {"stage": "contract", **info})
    print("contract OK")


def stage_layers():
    evidence: dict = {"stage": "layers", "tests": [], "gates": {}}
    overlay, ip, registers, load_info = load_overlay()
    evidence["load"] = load_info

    from ps.pynq_backend import PynqFFN
    from ps.runtime import Runtime
    from ps.preprocess import read_records, preprocess_record

    backend = PynqFFN(ip, registers, timeout_s=60.0)
    reference = Runtime(ROOT / "bundle", mode="split-ort", threads=4)
    records = read_records([ROOT / "data/1778852165/auto_data.csv"], limit=1)
    print(f"records loaded: {len(records)}")

    images = preprocess_record(records[0])
    reference.run(images)
    assert reference.last_ffn_inputs and reference.last_ffn_outputs, "no FFN tensors captured"

    def check(layer, x, ref_slice):
        y = backend.forward(layer, x, reference.weights[layer])
        err = float(np.abs(y - ref_slice).max())
        ok = bool(ref_slice.shape == y.shape and err <= 1e-4)
        item = {"layer": layer, "tokens": x.size // 128,
                "max_abs_err": err, "within_tol": ok,
                "timing_ms": dict(backend.last_timing),
                "y_first8": [float(v) for v in y.reshape(-1)[:8]],
                "ref_first8": [float(v) for v in ref_slice.reshape(-1)[:8]]}
        evidence["tests"].append(item)
        print(item["layer"], item["tokens"], ("err %.3e" % err) if ok else f"FAIL err={err}",
              {k: round(v, 2) for k, v in item["timing_ms"].items()})
        if not ok:
            raise AssertionError(f"PL vs ORT mismatch beyond 1e-4 at {layer}/{item['tokens']}")

    x_c0 = np.ascontiguousarray(reference.last_ffn_inputs["cross_0"]).reshape(4, 128)
    y_c0 = np.ascontiguousarray(reference.last_ffn_outputs["cross_0"]).reshape(4, 128)
    check("cross_0", x_c0[:1], y_c0[:1])
    check("cross_0", x_c0, y_c0)
    x_cam0 = np.ascontiguousarray(reference.last_ffn_inputs["camera_0"]).reshape(660, 128)
    y_cam0 = np.ascontiguousarray(reference.last_ffn_outputs["camera_0"]).reshape(660, 128)
    check("camera_0", x_cam0[:1], y_cam0[:1])
    check("camera_0", x_cam0, y_cam0)
    check("camera_1", np.ascontiguousarray(reference.last_ffn_inputs["camera_1"]),
          reference.last_ffn_outputs["camera_1"])
    check("cross_1", np.ascontiguousarray(reference.last_ffn_inputs["cross_1"]),
          reference.last_ffn_outputs["cross_1"])
    check("cross_0", x_c0[:1], y_c0[:1])  # revisit with cached weights

    # weight-change guard: same cached layer with different weights must be refused
    tampered = dict(reference.weights["cross_1"])
    tampered["b2"] = tampered["b2"] + 1.0
    try:
        backend.forward("cross_1", np.ascontiguousarray(reference.last_ffn_inputs["cross_1"]), tampered)
    except ValueError as error:
        evidence["weight_change_guard"] = {"refused": True, "message": str(error)}
        print("weight-change guard OK")
    else:
        evidence["weight_change_guard"] = {"refused": False}
        raise AssertionError("tampered weights were accepted")

    # gates: illegal counts and dtypes must be refused before any MMIO
    for label, tokens in (("zero", 0), ("661", 661)):
        try:
            backend.forward("cross_1", np.zeros((tokens, 128), dtype=np.float32), reference.weights["cross_1"])
        except (ValueError, FloatingPointError) as error:
            evidence["gates"][label] = {"refused": True, "error": type(error).__name__}
            print("gate", label, "refused OK")
        else:
            evidence["gates"][label] = {"refused": False}
            raise AssertionError(f"gate {label} accepted")
    try:
        backend.forward("cross_1", np.zeros((1, 128)), reference.weights["cross_1"])
    except ValueError as error:
        evidence["gates"]["float64"] = {"refused": True, "error": type(error).__name__}
        print("gate float64 refused OK")
    else:
        evidence["gates"]["float64"] = {"refused": False}
        raise AssertionError("float64 input accepted")

    x_buff, y_buff = backend._x, backend._y
    evidence["buffers"] = {"x_device_address": int(x_buff.device_address), "y_device_address": int(y_buff.device_address)}
    evidence["cma_after_layers"] = cma_kb()
    backend_close_guard(evidence, backend)
    record("pl_layers_20261015.json", evidence)
    print("layers OK")


def backend_close_guard(evidence, backend):
    try:
        backend.close()
        evidence["backend_close"] = "ok"
        print("backend close OK")
    except Exception as error:
        evidence["backend_close"] = f"refused: {error}"
        print("backend close REFUSED:", error)
        raise


def stage_hybrid(frames):
    evidence = {"stage": "hybrid", "frames_expected": frames}
    overlay, ip, registers, load_info = load_overlay()
    evidence["load"] = load_info

    from ps.pynq_backend import PynqFFN
    from ps.runtime import Runtime
    from ps.preprocess import read_records, preprocess_record

    backend = PynqFFN(ip, registers, timeout_s=60.0)
    ort_cpu = Runtime(ROOT / "bundle", mode="split-ort", threads=4)
    records = read_records([ROOT / "data/1778852165/auto_data.csv"], limit=frames)

    cpu_out = {}
    cpu_ffn = {}
    for rec in records:
        images = preprocess_record(rec)
        cpu_out[rec.frame_index] = ort_cpu.run(images)
        cpu_ffn[rec.frame_index] = {layer: np.copy(v) for layer, v in ort_cpu.last_ffn_outputs.items()}

    hybrid = Runtime(ROOT / "bundle", mode="split-numpy", threads=1, backend=backend)
    frames_rows = []
    for i, rec in enumerate(records):
        images = preprocess_record(rec)
        out = hybrid.run(images)
        ref = cpu_out[rec.frame_index]
        row = {"frame_index": rec.frame_index,
               "csv_line": rec.csv_line,
               "recorded_left": rec.recorded_left, "recorded_right": rec.recorded_right,
               "pred_left": float(out["speed_raw"][0, 0]), "pred_right": float(out["speed_raw"][0, 1]),
               "cpu_left": float(ref["speed_raw"][0, 0]), "cpu_right": float(ref["speed_raw"][0, 1]),
               "int_left": int(out["speed_raw"][0, 0]), "int_right": int(out["speed_raw"][0, 1]),
               "int_cpu_left": int(ref["speed_raw"][0, 0]), "int_cpu_right": int(ref["speed_raw"][0, 1]),
               "int_match": int(out["speed_raw"][0, 0]) == int(ref["speed_raw"][0, 0]) and
                           int(out["speed_raw"][0, 1]) == int(ref["speed_raw"][0, 1]),
               "pred_vs_recorded_left": float(out["speed_raw"][0, 0]) - rec.recorded_left,
               "pred_vs_recorded_right": float(out["speed_raw"][0, 1]) - rec.recorded_right,
               "per_layer_errors": {},
               "stage_ms": dict(hybrid.last_timings_ms)}
        for layer in LAYERS:
            err = float(np.abs(hybrid.last_ffn_outputs[layer] - cpu_ffn[rec.frame_index][layer]).max())
            row["per_layer_errors"][layer] = err
            if err > 1e-3:
                raise AssertionError(f"frame {rec.frame_index} layer {layer} error {err} beyond 1e-3")
        diff_left = abs(row["int_left"] - row["int_cpu_left"])
        diff_right = abs(row["int_right"] - row["int_cpu_right"])
        if diff_left or diff_right:
            raise AssertionError(f"frame {rec.frame_index} integer speeds diverge: {diff_left},{diff_right}")
        frames_rows.append(row)
        print("frame", rec.frame_index,
              "PL", row["pred_left"], row["pred_right"],
              "int(t)", "%d,%d" % (row["int_left"], row["int_right"]),
              "recorded", rec.recorded_left, rec.recorded_right,
              "total_ms", round(row["stage_ms"]["total"], 2), flush=True)
    evidence["frames"] = frames_rows
    deltas_l = [abs(r["pred_vs_recorded_left"]) for r in frames_rows]
    deltas_r = [abs(r["pred_vs_recorded_right"]) for r in frames_rows]
    layer_max = {layer: max(r["per_layer_errors"][layer] for r in frames_rows) for layer in LAYERS}
    evidence["summary"] = {"frames_completed": len(frames_rows),
                           "all_int_match": all(r["int_match"] for r in frames_rows),
                           "layer_max_err_vs_cpu_ort": layer_max,
                           "pred_vs_recorded_max_abs": {"left": max(deltas_l), "right": max(deltas_r)},
                           "pred_vs_recorded_mean_abs": {"left": sum(deltas_l)/len(deltas_l),
                                                         "right": sum(deltas_r)/len(deltas_r)}}
    print("summary:", json.dumps(evidence["summary"]))
    evidence["cma_after"] = cma_kb()
    backend_close_guard(evidence, backend)
    record(f"pl_hybrid_{frames}f_20261015.json", evidence)
    print("hybrid OK")


def stage_timing(frames):
    evidence = {"stage": "timing", "warmups": 1, "hybrid": [], "cpu": []}
    overlay, ip, registers, load_info = load_overlay()

    from ps.pynq_backend import PynqFFN
    from ps.runtime import Runtime
    from ps.preprocess import read_records, preprocess_record

    backend = PynqFFN(ip, registers, timeout_s=60.0)
    records = read_records([ROOT / "data/1778852165/auto_data.csv"], limit=frames)
    images_list = [preprocess_record(rec) for rec in records]

    hybrid = Runtime(ROOT / "bundle", mode="split-numpy", threads=1, backend=backend)
    hybrid.run(images_list[0])  # warmup: weight upload, session init
    for images in images_list:
        out = hybrid.run(images)
        evidence["hybrid"].append({"total_ms": hybrid.last_timings_ms["total"],
                                   "stage_ms": dict(hybrid.last_timings_ms)})
        print("hybrid", round(hybrid.last_timings_ms["total"], 2), "ms")

    cpu = Runtime(ROOT / "bundle", mode="split-ort", threads=4)
    cpu.run(images_list[0])
    for images in images_list:
        cpu.run(images)
        evidence["cpu"].append({"total_ms": cpu.last_timings_ms["total"],
                                "stage_ms": dict(cpu.last_timings_ms)})
        print("cpu   ", round(cpu.last_timings_ms["total"], 2), "ms")

    golden = Runtime(ROOT / "bundle", mode="golden", threads=4)
    golden.run(images_list[0])
    evidence["golden_warm_ms"] = golden.last_timings_ms
    print("golden warm", round(golden.last_timings_ms["total"], 2), "ms")

    backend_close_guard(evidence, backend)
    record("pl_timing_20261015.json", evidence)
    print("timing OK")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("contract", "layers", "hybrid", "timing"), required=True)
    parser.add_argument("--frames", type=int, default=8)
    args = parser.parse_args()
    if args.stage == "contract":
        stage_contract()
    elif args.stage == "layers":
        stage_layers()
    elif args.stage == "hybrid":
        stage_hybrid(args.frames)
    else:
        stage_timing(args.frames)


if __name__ == "__main__":
    main()
