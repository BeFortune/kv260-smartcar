"""Full CPU-equivalence validation of the migrated PS payload.

Runs every frame of every recording that ships inside the source payload ZIP
through the golden / split-ort / split-numpy CPU runtimes and checks, per frame:

  * all four outputs of split-ort and split-numpy agree with golden within
    max-abs error <= --atol (rtol 0),
  * the truncated integer part of speed_raw is identical to golden,
  * the four FFN cut points agree between the software (numpy) FFN and the
    split-ort pipeline output, and the numpy FFN on the exact same input the
    ORT pipeline consumed, within --atol.

JPEGs are decoded straight from the ZIP with cv2.imdecode; nothing is written
to disk and no archive member is executed or unpickled. This is a host CPU
software check on Windows: it is NOT a KV260 measurement, NOT PL/hardware
execution, and it does NOT measure accuracy or vehicle control.

Read-only over the source ZIP. Refuses to overwrite an existing report.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import platform
import posixpath
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ps.backends import NumpyFFN
from ps.preprocess import preprocess_arrays
from ps.runtime import MODEL_SHA256, OUTPUT_NAMES, Runtime

SOURCE_MODEL_MEMBER = "model/model_current.onnx"
PAYLOAD_MANIFEST_MEMBER = "payload_manifest.json"


def _stream_member(zf, member):
    with zf.open(member) as stream:
        return stream.read()


def _decode(member, raw):
    array = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    if array is None:
        raise ValueError(f"Unreadable JPEG: {member}")
    return array


def confined_member(csv_member, raw):
    """Resolve a CSV image path against its CSV parent, inside the archive.

    The normalized member must remain inside the CSV's own recording directory;
    absolute paths, drive letters and any ``..`` escape are rejected.
    """
    parent = posixpath.dirname(csv_member)
    cleaned = raw.strip().replace("\\", "/")
    if not cleaned or cleaned.startswith("/") or (len(cleaned) > 1 and cleaned[1] == ":"):
        raise ValueError(f"Absolute or empty archive path: {raw!r}")
    joined = posixpath.normpath(posixpath.join(parent, cleaned))
    if joined in ("", ".", "..") or joined.startswith("../") or joined.startswith("/") or ".." in joined.split("/"):
        raise ValueError(f"Archive path escapes the archive root: {raw!r}")
    if parent and joined != parent and not joined.startswith(parent + "/"):
        raise ValueError(f"Archive path escapes its recording directory: {raw!r}")
    return joined


def collect_frames(zf, names):
    """Parse every auto_data.csv inside the archive into ordered frame records."""
    csv_members = sorted(name for name in names if name.lower().endswith("auto_data.csv"))
    if not csv_members:
        raise ValueError("No auto_data.csv found in archive")
    frames = []
    for member in csv_members:
        segment = posixpath.dirname(member).split("/")[-1]
        text = zf.read(member).decode("utf-8-sig")
        frame_index = 0
        for line_number, row in enumerate(csv.reader(io.StringIO(text)), 1):
            if not row or all(not value.strip() for value in row):
                continue
            if len(row) not in (5, 6):
                raise ValueError(f"{member}:{line_number}: expected front,left,right,left_speed,right_speed[,region]")
            left_speed, right_speed = float(row[3]), float(row[4])
            if not (np.isfinite(left_speed) and np.isfinite(right_speed)):
                raise ValueError(f"{member}:{line_number}: non-finite recorded speed")
            members = [confined_member(member, row[i]) for i in range(3)]
            for target in members:
                if target not in names:
                    raise ValueError(f"{member}:{line_number}: missing archive member {target}")
            frame_index += 1
            frames.append({
                "segment": segment,
                "frame_index": frame_index,
                "csv_member": member,
                "csv_line": line_number,
                "front": members[0],
                "left": members[1],
                "right": members[2],
                "recorded_left": left_speed,
                "recorded_right": right_speed,
            })
    if not frames:
        raise ValueError("No recording frames in archive")
    return frames


def _worst_tracker():
    return {}


def _bump(tracker, mode, key, error, frame):
    current = tracker.get((mode, key))
    if current is None or error > current[0]:
        tracker[(mode, key)] = (float(error), frame["segment"], frame["frame_index"])


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def validate(zip_path, bundle, threads=4, atol=1e-3, progress=None):
    if not np.isfinite(atol) or atol <= 0:
        raise ValueError("atol must be finite and positive")
    zip_path = Path(zip_path).resolve()
    import zipfile

    cv2.setNumThreads(1)
    cv2.ocl.setUseOpenCL(False)

    result = {
        "schema": "pynq_ffn.payload_validation.v1",
        "status": "pass",
        "hardware_execution": False,
        "host": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "windows_host": sys.platform.startswith("win"),
            "kv260": False,
            "pl_fpga": False,
        },
        "environment": {
            "numpy": np.__version__,
            "onnxruntime": ort.__version__,
            "opencv": cv2.__version__,
        },
        "threads": threads,
        "atol": atol,
        "rtol": 0,
        "timing_scope": ("Host CPU software replay only. Not KV260 measurements. "
                          "Includes no board init, capture, serial transport, PL execution or vehicle response."),
        "not_covered": [
            "KV260 / ARM board execution and PL (FPGA) datapath",
            "accuracy against ground-truth or held-out labels",
            "vehicle control, actuator or safety response",
            "device, serial and network IO",
        ],
        "source": {
            "zip": str(zip_path),
            "zip_bytes": zip_path.stat().st_size,
        },
        "model_sha": {"golden_model_sha256": MODEL_SHA256},
        "totals": {},
        "segments": {},
    }

    with zipfile.ZipFile(zip_path) as zf:
        archive_names = zf.namelist()
        names = set(archive_names)
        if len(names) != len(archive_names):
            raise ValueError("Duplicate archive members are not allowed")
        if not {PAYLOAD_MANIFEST_MEMBER, SOURCE_MODEL_MEMBER} <= names:
            raise ValueError("Expected the original payload manifest and reviewed model member")
        if PAYLOAD_MANIFEST_MEMBER in names:
            manifest = json.loads(zf.read(PAYLOAD_MANIFEST_MEMBER))
            declared = manifest.get("recordings", [])
            result["source"]["payload_manifest_version"] = manifest.get("version")
            result["source"]["declared_recordings"] = declared
            result["source"]["declared_total_frames"] = sum(int(item.get("frames", 0)) for item in declared)
            result["model_sha"]["payload_manifest_model_sha256"] = manifest.get("model_sha256")
        if SOURCE_MODEL_MEMBER in names:
            source_sha = _sha256_bytes(zf.read(SOURCE_MODEL_MEMBER))
            result["model_sha"]["source_zip_model_sha256"] = source_sha
            result["model_sha"]["source_zip_model_member"] = SOURCE_MODEL_MEMBER

        frames = collect_frames(zf, names)
        result["source"]["observed_total_frames"] = len(frames)

        observed = {}
        for frame in frames:
            observed.setdefault(frame["segment"], 0)
            observed[frame["segment"]] += 1
        result["source"]["observed_recordings"] = observed

        declared_total = result["source"].get("declared_total_frames")
        declared = {item.get("segment"): int(item.get("frames", 0)) for item in result["source"].get("declared_recordings", [])}
        result["source"]["declared_matches_observed"] = bool(declared_total == len(frames) and declared == observed)

        bundle_model = Path(bundle).resolve() / "model.onnx"
        result["model_sha"]["bundle_model_onnx_sha256"] = _sha256_bytes(bundle_model.read_bytes())
        result["model_sha"]["bundle_matches_golden"] = (
            result["model_sha"]["bundle_model_onnx_sha256"] == MODEL_SHA256
        )
        if "source_zip_model_sha256" in result["model_sha"]:
            result["model_sha"]["source_matches_golden"] = result["model_sha"]["source_zip_model_sha256"] == MODEL_SHA256

        if not result["source"]["declared_matches_observed"]:
            raise ValueError("Payload recording counts differ from its manifest")
        if result["model_sha"].get("payload_manifest_model_sha256") != MODEL_SHA256:
            raise ValueError("Payload manifest names an unreviewed model")
        if not result["model_sha"]["bundle_matches_golden"] or not result["model_sha"].get("source_matches_golden"):
            raise ValueError("Payload or bundle model differs from the reviewed golden model")

        runtimes = {mode: Runtime(bundle, mode=mode, threads=threads)
                    for mode in ("golden", "split-ort", "split-numpy")}
        numpy_ffn = NumpyFFN()
        ffn_stages = [stage["name"] for stage in runtimes["split-ort"].abi["schedule"] if stage["kind"] == "ffn"]
        if len(ffn_stages) != 4:
            raise ValueError(f"Expected 4 FFN cut points, found {len(ffn_stages)}")

        max_error = {mode: {name: 0.0 for name in OUTPUT_NAMES} for mode in ("split-ort", "split-numpy")}
        worst = _worst_tracker()
        ffn_pipeline_max = {stage: 0.0 for stage in ffn_stages}
        ffn_same_input_max = {stage: 0.0 for stage in ffn_stages}
        ffn_worst = {}
        speed_int_mismatch = {mode: 0 for mode in ("split-ort", "split-numpy")}
        segment_status = {segment: {"frames": count, "status": "pass"}
                          for segment, count in sorted(observed.items())}

        total = len(frames)
        start = time.perf_counter()
        for done, frame in enumerate(frames, 1):
            raw_left = _stream_member(zf, frame["left"])
            raw_front = _stream_member(zf, frame["front"])
            raw_right = _stream_member(zf, frame["right"])
            images = preprocess_arrays(_decode(frame["left"], raw_left),
                                       _decode(frame["front"], raw_front),
                                       _decode(frame["right"], raw_right))
            outputs = {mode: runtime.run(images) for mode, runtime in runtimes.items()}
            golden = outputs["golden"]

            for mode in ("split-ort", "split-numpy"):
                for name in OUTPUT_NAMES:
                    error = float(np.max(np.abs(outputs[mode][name] - golden[name])))
                    if not np.isfinite(error):
                        error = float("inf")
                    max_error[mode][name] = max(max_error[mode][name], error)
                    _bump(worst, mode, name, error, frame)
                    if error > atol:
                        result["status"] = "failed"
                        segment_status[frame["segment"]]["status"] = "failed"
                mismatches = int(np.count_nonzero(np.trunc(outputs[mode]["speed_raw"]) != np.trunc(golden["speed_raw"])))
                speed_int_mismatch[mode] += mismatches
                if mismatches:
                    result["status"] = "failed"
                    segment_status[frame["segment"]]["status"] = "failed"

            for stage in ffn_stages:
                pipeline = float(np.max(np.abs(runtimes["split-numpy"].last_ffn_outputs[stage]
                                              - runtimes["split-ort"].last_ffn_outputs[stage])))
                same_input = float(np.max(np.abs(numpy_ffn.forward(stage,
                                                                   runtimes["split-ort"].last_ffn_inputs[stage],
                                                                   runtimes["split-ort"].weights[stage])
                                                - runtimes["split-ort"].last_ffn_outputs[stage])))
                for label, error in (("pipeline", pipeline), ("same_input", same_input)):
                    if not np.isfinite(error):
                        error = float("inf")
                    bucket = ffn_pipeline_max if label == "pipeline" else ffn_same_input_max
                    bucket[stage] = max(bucket[stage], error)
                    key = (label, stage)
                    if key not in ffn_worst or error > ffn_worst[key][0]:
                        ffn_worst[key] = (error, frame["segment"], frame["frame_index"])
                    if error > atol:
                        result["status"] = "failed"
                        segment_status[frame["segment"]]["status"] = "failed"

            if progress and (done % 10 == 0 or done % 25 == 0 or done == total):
                mark = "25" if done % 25 == 0 else "10"
                progress(f"progress[{mark}] {done}/{total} frames seg={frame['segment']} "
                         f"frame={frame['frame_index']} status={result['status']}")

        result["totals"] = {
            "frames_processed": total,
            "speed_integer_mismatches": speed_int_mismatch,
            "max_abs_error": {mode: {name: max_error[mode][name] for name in OUTPUT_NAMES}
                              for mode in ("split-ort", "split-numpy")},
            "worst_frame": {f"{mode}:{name}": {"error": worst[(mode, name)][0],
                                                "segment": worst[(mode, name)][1],
                                                "frame_index": worst[(mode, name)][2]}
                            for mode in ("split-ort", "split-numpy") for name in OUTPUT_NAMES},
            "ffn_cutpoints": {
                "stages": ffn_stages,
                "pipeline_numpy_vs_ort_max_abs": ffn_pipeline_max,
                "same_input_numpy_vs_ort_max_abs": ffn_same_input_max,
                "worst_frame": {f"{label}:{stage}": {"error": value[0], "segment": value[1], "frame_index": value[2]}
                                for (label, stage), value in ffn_worst.items()},
                "pass": bool(all(v <= atol for v in ffn_pipeline_max.values())
                             and all(v <= atol for v in ffn_same_input_max.values())),
            },
            "elapsed_s": time.perf_counter() - start,
        }
        result["segments"] = segment_status

    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Full-payload CPU-equivalence validation of a recording ZIP. Read-only; no device IO.")
    parser.add_argument("--zip", type=Path, required=True, dest="zip_path")
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--threads", type=int, choices=(1, 2, 3, 4), default=4)
    parser.add_argument("--atol", type=float, default=1e-3)
    args = parser.parse_args(argv)
    if args.output.exists():
        raise FileExistsError(f"Refusing to overwrite existing report: {args.output}")
    report = validate(args.zip_path, args.bundle, args.threads, args.atol,
                      progress=lambda message: print(message, flush=True))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf8") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(json.dumps({
        "status": report["status"],
        "frames_processed": report["totals"]["frames_processed"],
        "segments": report["segments"],
        "elapsed_s": report["totals"]["elapsed_s"],
        "output": str(args.output),
    }, indent=2))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
