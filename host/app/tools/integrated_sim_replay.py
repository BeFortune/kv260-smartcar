"""Integrated offline simulation: integer-reference PL + PS + real YOLO + control v2.

No PYNQ, HLS, camera, serial, motor, or vehicle access. The integer reference is a
PL numerical model, not an HLS/RTL run or a physical PL execution.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
import time
import traceback

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from ps.control_v2 import ControlPolicy, DriverSnapshot, PolicyConfig, SignSnapshot  # noqa: E402
from ps.packet import encode_side_speed  # noqa: E402
from ps.pl_infer import reference_infer  # noqa: E402
from ps.roadsign import RoadSignONNX  # noqa: E402
from tools.field_replay import load_pinned_images  # noqa: E402
from tools.roadsign_replay import ReplayClock, metadata_for, summary  # noqa: E402


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(8 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def identity(path):
    path = Path(path)
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256_file(path)}


def dump(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def process_sample(sample, images, grabbed, metadata, generation, driver, detector, policy, replay_clock):
    replay_clock.set_frames(grabbed, t_mono=metadata.get("t_mono"))
    driver_left, driver_right = driver(*images)
    driver_completed = replay_clock()
    driver_snapshot = DriverSnapshot(
        generation, sample["frame_id"], min(grabbed), driver_completed, True, None,
        driver_left, driver_right,
    )
    sign_frame = detector.detect(images[1], grabbed=grabbed[1])
    sign_snapshot = SignSnapshot(
        generation, sample["frame_id"], sign_frame.grabbed, sign_frame.completed,
        True, None, sign_frame.result.detections,
    )
    now = replay_clock()
    decision = policy.step(now, driver_snapshot, sign_snapshot)
    packet = encode_side_speed(decision.left, decision.right)
    return {
        "sample_id": sample["sample_id"],
        "run": sample["run"],
        "frame_id": sample["frame_id"],
        "generation": generation,
        "grabbed": list(grabbed),
        "recorded_old": None if "recorded_old" not in sample else [
            sample["recorded_old"]["left"], sample["recorded_old"]["right"],
        ],
        "integer_reference_driver": {
            "left": driver_left,
            "right": driver_right,
            "completed": driver_completed,
            "stage_ms": dict(driver.last_ms),
            "kernel_starts": driver.kernel.starts,
        },
        "roadsign": {
            "model_sha256": sign_frame.model_sha256,
            "latency_ms": sign_frame.latency_ms,
            "grabbed": sign_frame.grabbed,
            "completed": sign_frame.completed,
            "params": sign_frame.result.params,
            "detections": [
                {
                    "class_id": item.class_id,
                    "class_name": item.class_name,
                    "confidence": item.confidence,
                    "width": item.width,
                    "height": item.height,
                }
                for item in sign_frame.result.detections
            ],
        },
        "decision": {
            "left": decision.left,
            "right": decision.right,
            "stop": decision.stop,
            "reason": decision.reason,
            "fault": decision.state.fault,
        },
        "packet_hex": packet.hex(),
        "timing": {
            "recorded_capture_t_mono": metadata["t_mono"],
            "replay_decision_t_mono": now,
            "capture_age_ms": [(metadata["t_mono"] - stamp) * 1000 for stamp in grabbed],
            "offline_chain_elapsed_ms": (now - metadata["t_mono"]) * 1000,
        },
    }


def run(args):
    contract_path = args.contract.resolve()
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    samples = list(contract["samples"])
    if args.limit is not None:
        if args.limit < 1:
            raise ValueError("--limit must be positive")
        samples = samples[:args.limit]
    if not samples:
        raise ValueError("no samples")
    sample_ids = [sample["sample_id"] for sample in samples]
    if len(set(sample_ids)) != len(sample_ids):
        raise ValueError("duplicate sample_id in input contract")
    scope_path = args.policy_scope.resolve()
    scope = json.loads(scope_path.read_text(encoding="utf-8"))
    config = PolicyConfig(**scope["fixed_diagnostic_policy_config"])
    driver = reference_infer(args.ref, args.params, args.partb, threads=args.driver_threads)
    replay_clock = ReplayClock()
    detector = RoadSignONNX(
        args.roadsign_model,
        expected_sha256=args.roadsign_sha256,
        threads=args.roadsign_threads,
        clock=replay_clock,
    )
    rows = []
    current_run, generation, policy = None, 0, None
    with (args.out_dir / "predictions.jsonl").open("x", encoding="utf-8", newline="\n") as stream:
        for index, sample in enumerate(samples):
            if sample["run"] != current_run:
                generation += 1
                current_run = sample["run"]
                policy = ControlPolicy(config, generation=generation)
            images = load_pinned_images(sample)
            metadata, grabbed = metadata_for(sample)
            row = process_sample(
                sample, images, grabbed, metadata, generation, driver, detector, policy, replay_clock,
            )
            rows.append(row)
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
            stream.flush()
            if (index + 1) % 25 == 0 or index + 1 == len(samples):
                print(f"integrated_sim {index + 1}/{len(samples)}", flush=True)

    reasons = Counter(row["decision"]["reason"] for row in rows)
    faults = sum(row["decision"]["fault"] is not None for row in rows)
    red_rows = [
        row for row in rows
        if any(item["class_id"] == 6 for item in row["roadsign"]["detections"])
    ]
    packets_valid = all(len(bytes.fromhex(row["packet_hex"])) == 21 for row in rows)
    completed = len(rows) == len(samples)
    status = "已验证" if completed and not faults and packets_valid else "失败"
    detector_times = [row["roadsign"]["latency_ms"] for row in rows]
    driver_totals = [row["integer_reference_driver"]["stage_ms"]["total"] for row in rows]
    stage_times = {
        name: summary([row["integer_reference_driver"]["stage_ms"][name] for row in rows])
        for name in ("pre", "kernel", "dequant", "partb")
    }
    result = {
        "schema": "integrated_integer_reference_yolo_pl_ps_v1",
        "status": status,
        "date_utc": datetime.now(timezone.utc).isoformat(),
        "scope": (
            "Actual recorded-image replay through integer-reference PL, PS partB, real YOLO ONNX, "
            "control v2 and packet encoding; no HLS/RTL, physical PL, camera, serial or vehicle"
        ),
        "samples": len(rows),
        "requested_samples": len(samples),
        "unprocessed_samples": len(samples) - len(rows),
        "unique_sample_ids": len(set(sample_ids)),
        "runs": len(set(sample["run"] for sample in samples)),
        "kernel_starts": driver.kernel.starts,
        "decision_reasons": dict(reasons),
        "stop_count": sum(row["decision"]["stop"] for row in rows),
        "fault_ticks": faults,
        "red_observations": len(red_rows),
        "red_stop_decisions": sum(row["decision"]["stop"] for row in red_rows),
        "packets_valid_21_bytes": packets_valid,
        "timing_ms": {
            "integer_reference_driver_total": summary(driver_totals),
            "yolo_detector": summary(detector_times),
            "driver_stages": stage_times,
            "note": "Windows offline wall times only; not KV260, HLS, RTL, physical PL or end-to-end board latency",
        },
        "inputs": {
            "contract": identity(contract_path),
            "policy_scope": identity(scope_path),
            "ref": identity(args.ref),
            "params": identity(args.params),
            "partb": identity(args.partb),
            "roadsign_model": identity(args.roadsign_model),
        },
        "source": {
            "integrated_sim_replay.py": identity(PROJECT / "tools/integrated_sim_replay.py"),
            "pl_infer.py": identity(PROJECT / "ps/pl_infer.py"),
            "roadsign.py": identity(PROJECT / "ps/roadsign.py"),
            "policy.py": identity(PROJECT / "ps/control_v2/policy.py"),
            "snapshots.py": identity(PROJECT / "ps/control_v2/snapshots.py"),
        },
        "config_diagnostic_only": scope["fixed_diagnostic_policy_config"],
        "pynq_imported": any(name == "pynq" or name.startswith("pynq.") for name in sys.modules),
        "not_executed": [
            "HLS or RTL integrated replay",
            "physical PL",
            "real camera",
            "physical serial or STM32 ACK",
            "motor or vehicle",
            "15 Hz or fixed-track acceptance",
        ],
    }
    dump(args.out_dir / "summary.json", result)
    print(json.dumps({
        "status": status,
        "samples": len(rows),
        "fault_ticks": faults,
        "red_observations": len(red_rows),
        "red_stop_decisions": result["red_stop_decisions"],
        "out_dir": str(args.out_dir),
    }, ensure_ascii=False), flush=True)
    return 0 if status == "已验证" else 1


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--policy-scope", type=Path, required=True)
    parser.add_argument("--ref", type=Path, required=True)
    parser.add_argument("--params", type=Path, required=True)
    parser.add_argument("--partb", type=Path, required=True)
    parser.add_argument("--roadsign-model", type=Path, required=True)
    parser.add_argument("--roadsign-sha256", required=True)
    parser.add_argument("--driver-threads", type=int, default=4)
    parser.add_argument("--roadsign-threads", type=int, default=1)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--out-dir", type=Path, required=True)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if len(args.roadsign_sha256) != 64 or any(c not in "0123456789abcdef" for c in args.roadsign_sha256):
        raise SystemExit("--roadsign-sha256 must be 64 lowercase hexadecimal characters")
    if args.driver_threads < 1 or args.roadsign_threads < 1:
        raise SystemExit("thread counts must be positive")
    args.roadsign_model = args.roadsign_model.resolve()
    args.ref = args.ref.resolve()
    args.params = args.params.resolve()
    args.partb = args.partb.resolve()
    args.out_dir = args.out_dir.resolve()
    args.out_dir.mkdir(parents=True, exist_ok=False)
    dump(args.out_dir / "command.json", {
        "argv": sys.argv if argv is None else argv,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "scope": "offline integer-reference integrated simulation only; no devices",
    })
    try:
        return run(args)
    except BaseException as error:
        dump(args.out_dir / "failure.json", {
            "status": "失败",
            "type": type(error).__name__,
            "first_error": str(error),
        })
        with (args.out_dir / "failure_traceback.txt").open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(traceback.format_exc())
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
