from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import time

import cv2
import numpy as np

from .preprocess import read_records, preprocess_record, legacy_packet_preview
from .runtime import Runtime


def replay(recordings, bundle, output_dir, backend="split-ort", threads=1, limit=None):
    output_dir = Path(output_dir)
    if output_dir.exists():
        raise FileExistsError("Refusing an existing output directory")
    cv2.setNumThreads(1)
    cv2.ocl.setUseOpenCL(False)
    records = read_records(recordings, limit)
    runtime = Runtime(bundle, mode=backend, threads=threads)
    output_dir.mkdir(parents=True, exist_ok=False)
    fields = ["segment", "frame_index", "csv_line", "front", "left", "right", "left_speed", "right_speed",
              "left_speed_int", "right_speed_int", "recorded_left_speed", "recorded_right_speed", "delta_left", "delta_right",
              "preprocess_ms", "inference_ms", "legacy_packet_hex", "speed_norm_json", "region_logits_json", "region_probs_json", "stage_timings_ms_json"]
    summary = {"status": "running", "frames_expected": len(records), "frames_completed": 0, "backend": backend,
               "threads": threads, "hardware_execution": False, "timing_scope": "Host CPU software replay, not board performance; excludes startup, capture, serial and vehicle response"}
    start = time.perf_counter()
    try:
        with (output_dir / "predictions.csv").open("x", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            for record in records:
                before = time.perf_counter()
                images = preprocess_record(record)
                preprocess_ms = (time.perf_counter() - before) * 1000
                outputs = runtime.run(images)
                left, right = map(float, outputs["speed_raw"][0])
                writer.writerow({"segment": record.segment, "frame_index": record.frame_index, "csv_line": record.csv_line,
                                 "front": record.front.relative_to(record.directory).as_posix(), "left": record.left.relative_to(record.directory).as_posix(),
                                 "right": record.right.relative_to(record.directory).as_posix(), "left_speed": left, "right_speed": right,
                                 "left_speed_int": int(left), "right_speed_int": int(right), "recorded_left_speed": record.recorded_left,
                                 "recorded_right_speed": record.recorded_right, "delta_left": left-record.recorded_left, "delta_right": right-record.recorded_right,
                                 "preprocess_ms": preprocess_ms, "inference_ms": runtime.last_timings_ms["total"],
                                 "legacy_packet_hex": legacy_packet_preview(left, right), "speed_norm_json": json.dumps(outputs["speed_norm"].tolist()),
                                 "region_logits_json": json.dumps(outputs["region_logits"].tolist()), "region_probs_json": json.dumps(outputs["region_probs"].tolist()),
                                 "stage_timings_ms_json": json.dumps(runtime.last_timings_ms)})
                stream.flush()
                summary["frames_completed"] += 1
        summary["status"] = "complete"
    except Exception as error:
        summary.update(status="failed", error_type=type(error).__name__)
        raise
    finally:
        summary["elapsed_s"] = time.perf_counter() - start
        with (output_dir / "summary.json").open("x", encoding="utf8") as stream:
            json.dump(summary, stream, indent=2)
    return summary


def main():
    parser = argparse.ArgumentParser(description="Offline three-camera CPU replay only; no device IO.")
    parser.add_argument("--recording", action="append", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--backend", choices=("golden", "split-ort", "split-numpy"), default="split-ort")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--threads", type=int, choices=(1, 2, 3, 4), default=1)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    print(json.dumps(replay(args.recording, args.bundle, args.output_dir, args.backend, args.threads, args.limit)))


if __name__ == "__main__":
    main()
