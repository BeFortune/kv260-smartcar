"""Offline saved-result or explicit synthetic-fixture decision replay. No devices."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from ps.control_v2 import ControlPolicy, DriverSnapshot, PolicyConfig, SignSnapshot
from ps.control_v2.audit import SourceAudit, TickAudit, write_audit
from ps.control_v2.sender import Command
from ps.roadsign import CLASS_NAMES, Detection


def dump(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _result(raw, kind, generation):
    if raw is None:
        return None
    common = (generation, raw["frame_seq"], raw["grabbed"], raw["completed"],
              raw.get("healthy", True), raw.get("error"))
    if kind == "driver":
        return DriverSnapshot(*common, raw.get("left"), raw.get("right"))
    detections = tuple(Detection(**item) for item in raw.get("detections", []))
    return SignSnapshot(*common, detections)


def load_saved(path):
    """Only decisions on saved rows; no implied ticks between sparse records."""
    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            row = json.loads(line)
            if "now" in row:
                yield row
                continue
            now = row["timing"]["replay_decision_t_mono"]
            seq = row["frame_id"]
            road = row.get("roadsign")
            yield {"run": row["run"], "sample_id": row["sample_id"], "now": now,
                   "driver": {"frame_seq": seq, "grabbed": min(row["grabbed"]),
                              "completed": now, "left": row["driver"][0], "right": row["driver"][1]},
                   "signs": {"frame_seq": seq,
                             "grabbed": road["grabbed_t_mono"] if road else min(row["grabbed"]),
                             "completed": road["replay_completed_t_mono"] if road else now,
                             "healthy": road is not None,
                             "error": None if road else "saved_detector_result_missing",
                             "detections": road["detections"] if road else []}}


def fixture_rows(config):
    """Synthetic accepted detections, not inference, explicitly diagnostic values."""
    dt = min(config.confirmation_max_gap_s, config.max_driver_age_s, config.max_sign_age_s) / 4
    n = config.confirm_frames
    scenarios = [
        ("red_green_pedestrian", [(6,), (), (5,), (5, 9)] + [(5,)] * max(n, config.pedestrian_clear_frames)),
        ("limit_on_off_on", [(3,)] * n + [(4,)] * n + [(3,)] * n),
        ("crosswalk_rearm", [(2,)] * (n + 2) + [()] * config.crosswalk_clear_frames + [(2,)] * n),
        ("junction_left", [(0,)] * (n + 1) + [()]),
        ("junction_conflict", [(0, 1), ()]),
    ]
    for scenario, observations in scenarios:
        now = 10.
        for seq, ids in enumerate(observations, 1):
            if scenario == "crosswalk_rearm" and seq == n + 3:
                now += config.crosswalk_hold_s + dt
            result = {"frame_seq": seq, "grabbed": now, "completed": now}
            row = {"run": scenario, "sample_id": f"{scenario}/{seq}", "now": now,
                   "driver": {**result, "left": 200., "right": 100.},
                   "signs": {**result, "detections": [asdict(Detection(i, CLASS_NAMES[i], .9, 100., 100.)) for i in ids]}}
            yield row
            if seq == 1:
                yield {**row, "now": now + dt / 2, "sample_id": row["sample_id"] + "_reused"}
            now += dt


def replay_rows(rows, config):
    current_run, generation, policy = None, 0, None
    seen_runs, seen = set(), {}
    audit, samples = [], []
    for row in rows:
        run = row["run"]
        if run != current_run:
            if run in seen_runs:
                raise ValueError("runs must be contiguous; interleaved generations are refused")
            generation += 1
            policy = ControlPolicy(config, generation=generation)
            current_run = run
            seen_runs.add(run)
            seen = {}
        driver = _result(row.get("driver"), "driver", generation)
        signs = _result(row.get("signs"), "signs", generation)
        now = row["now"]
        event = row.get("complete_route")
        if event is not None:
            policy.complete_route(generation, event)
        decision = policy.step(now, driver, signs, emergency_stop=row.get("emergency_stop", False),
                               external_fault=row.get("external_fault"))
        sources = []
        for name, value in (("driver", driver), ("signs", signs)):
            if value is None:
                sources.append(SourceAudit(None, None, None, None, None, False, None, None))
                continue
            key = (value.generation, value.frame_seq)
            sources.append(SourceAudit(value.generation, value.frame_seq, value.grabbed,
                value.completed, now - value.grabbed, seen.get(name) == key, value.healthy, value.error))
            seen[name] = key
        command = Command(generation, len(audit) + 1, now, now + config.command_ttl_s,
                          decision.left, decision.right, decision.stop, decision.reason)
        audit.append(TickAudit(len(audit) + 1, now, generation,
            (None, None) if driver is None else (driver.left, driver.right),
            (decision.left, decision.right), sources[0], sources[1], decision,
            command.frame.hex(), "not_sent_decision_replay", None, ()))
        samples.append({"tick_seq": len(audit), "run": run, "sample_id": row.get("sample_id")})
    if not audit:
        raise ValueError("no replay records")
    return audit, samples


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="all timing/count/factor values explicit; no vehicle defaults")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--input", type=Path, help="saved roadsign_replay JSONL or normalized snapshot JSONL")
    group.add_argument("--fixture", action="store_true", help="synthetic accepted detections; not real YOLO")
    parser.add_argument("--out-dir", type=Path, required=True, help="must not already exist")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        config = PolicyConfig(**json.loads(args.config.read_text(encoding="utf-8")))
        # Refuse overwrite before any output; all writes use exclusive create.
        args.out_dir.mkdir(parents=True, exist_ok=False)
    except (ValueError, OSError, TypeError) as error:
        print(f"control_v2_replay: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    try:
        rows = fixture_rows(config) if args.fixture else load_saved(args.input)
        audit, samples = replay_rows(rows, config)
        write_audit(args.out_dir / "audit.jsonl", audit)
        dump(args.out_dir / "samples.json", samples)
        faults = sum(item.decision.state.fault is not None for item in audit)
        summary = {"schema": "control_v2_offline_replay_v1", "status": "失败" if faults else "已验证",
                   "date": datetime.now(timezone.utc).isoformat(), "fixture": args.fixture,
                   "scope": "synthetic accepted detections, not real YOLO; decision-only, no writes" if args.fixture else
                            "saved model results, decision-only; not continuous video or closed-loop",
                   "config": asdict(config), "config_sha256": digest(args.config),
                   "input_sha256": None if args.fixture else digest(args.input),
                   "policy_sha256": digest(PROJECT / "ps/control_v2/policy.py"),
                   "ticks": len(audit), "fault_ticks": faults,
                   "reasons": dict(Counter(item.decision.reason for item in audit)),
                   "timing_note": "saved driver completed is reconstructed as recorded replay decision time; "
                                  "driver grabbed=min triplet, signs use saved grabbed/completed. No inserted ticks/frames; "
                                  "large gaps cannot demonstrate intermediate stale handling or multi-frame confirmation.",
                   "limitations": ["diagnostic config only, not vehicle safety values", "no physical sends or STM32 ACK",
                                   "fixture is not detection accuracy", "no PL/camera/serial/vehicle/15Hz verification",
                                   "post-run exclusive disk writer, not validated production asynchronous logging"]}
        dump(args.out_dir / "summary.json", summary)
        print(json.dumps({"status": summary["status"], "ticks": len(audit), "out_dir": str(args.out_dir)}, ensure_ascii=False))
        return 1 if faults else 0
    except Exception as error:
        dump(args.out_dir / "failure.json", {"status": "失败", "type": type(error).__name__, "first_error": str(error)})
        print(f"control_v2_replay: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
