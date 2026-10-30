"""Offline identity replay of field capture samples on five fixed CPU paths.

No camera, serial, motor, PYNQ or PL access.  The only model work is local CPU
ORT plus the pinned exact integer reference conv0-2.  Prepare freezes the sample
list, model/source hashes and every selected original PNG before any ORT session
is created.  Run refuses changed inputs and gates old-NUC predictions against
the recorded raw CPU speeds before evaluating any candidate.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import time
import traceback

import cv2
import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
WORKSPACE = PROJECT.parent
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))
from ps.preprocess import preprocess_arrays  # noqa: E402

ARTIFACT_ROOT = PROJECT / "artifacts/field_capture_analysis_20261026"
OLD = WORKSPACE / "ros_craic_car/src/base_control/resource/model_current.onnx"
FP32 = PROJECT / "artifacts/route_b_quant_20261020/cut2_01_board/s16_j1c2_trained_full.onnx"
PARTB_ORIG = PROJECT / "artifacts/route_b_quant_20261020/cut2_01_board/s16_j1c2_cut2int8_ps.onnx"
B_FULL = PROJECT / "artifacts/photometric_finetune_20261025/candidates02/b/full.onnx"
B_PARTB = PROJECT / "artifacts/photometric_finetune_20261025/candidates02/b/partb.onnx"
REF = PROJECT / "hls/conv_front_int8/ref_int8.py"
PARAMS = PROJECT / "hls/conv_front_int8/params.json"
TEST_FILE = PROJECT / "tests/test_field_replay.py"

OLD_SHA = "ad58d1fc9168da94b9ce076ba8cc9754c371ead87974da8bc9cc601ad1650d5b"
FP32_SHA = "86db4428c9c1e20ae600fbfd8ec6aa36ce7fbfb786115518db672d5930f44bfb"
PARTB_ORIG_SHA = "61bcec8f01ffca61ecc611f12bdf61a177f8ee0e9dc0febd256e02b8132cecdb"
B_FULL_SHA = "7bd72cb5e56b1940ec7a632ae3c078deacb5146ccd274bfb6f35d42cfd69e1a5"
B_PARTB_SHA = "542c6939ab45826bfdbd8d17c722c88bee292f314debf923bcee1842fa7684de"
REF_SHA = "02feef12ea147f5b772a726ebd3c90d5f1e66bd33ff9fcefa50c3618a040edb4"
PARAMS_SHA = "889703bc0ea80da1a1f49f647c11714b249ee1eaea3149894f35dbbf4eb86a3f"
PRIMARY_RUN = "run_20261026_142207_115d6e87"

MODEL_SPECS = (
    {"name": "old_nuc", "kind": "ort", "path": OLD, "sha256": OLD_SHA, "role": "recorded old CPU model"},
    {"name": "new_fp32", "kind": "ort", "path": FP32, "sha256": FP32_SHA, "role": "original new FP32"},
    {"name": "new_hybrid", "kind": "hybrid", "path": PARTB_ORIG, "sha256": PARTB_ORIG_SHA,
     "role": "exact integer conv0-2 + original part B"},
    {"name": "b_full", "kind": "ort", "path": B_FULL, "sha256": B_FULL_SHA, "role": "candidate B full FP32"},
    {"name": "b_hybrid", "kind": "hybrid", "path": B_PARTB, "sha256": B_PARTB_SHA,
     "role": "same exact integer feature + candidate B part B"},
)
MODEL_NAMES = tuple(spec["name"] for spec in MODEL_SPECS)
ROLES = ("left", "front", "right")
SIDES = ("left", "right")
GATE_TOLERANCE = .001
OUT_SHAPE = (3, 64, 9, 25)
PREPROCESS = {"crop_yx": [[150, 479], [0, 640]], "resize_wh": [200, 66],
              "interpolation": "INTER_LINEAR", "color": "BGR to YUV",
              "scale": "float32 /255 -0.5 via cv2.addWeighted", "layout": "N,camera,C,H,W",
              "shape": [1, 3, 3, 66, 200], "camera_order": list(ROLES)}


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(8 << 20), b""):
            h.update(block)
    return h.hexdigest()


def dump(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")


def write_text(path, text):
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(text)


def d_value(values):
    values = np.asarray(values, dtype=np.float64)
    return (values[..., 0] - values[..., 1]) / 2.0


def base_value(values):
    return np.asarray(values, dtype=np.float64).mean(axis=-1)


def summary(values):
    values = np.asarray(values, dtype=np.float64).reshape(-1)
    if not len(values):
        return None
    if not np.isfinite(values).all():
        raise ValueError("nonfinite metric input")
    return {"n": int(len(values)), "p50": float(np.quantile(values, .5)),
            "p95": float(np.quantile(values, .95)), "max": float(values.max()),
            "mean": float(values.mean())}


def ratio(numerator, denominator):
    return {"numerator": int(numerator), "denominator": int(denominator),
            "rate": float(numerator / denominator) if denominator else None}


def response_metrics(old_prediction, candidate_prediction, mask):
    """Candidate response on a mask fixed solely by the old model."""
    mask = np.asarray(mask, dtype=bool)
    n = int(mask.sum())
    if n == 0:
        return {"frames": 0, "denominator": 0, "abs_amplitude_ratio": None,
                "half_amplitude": ratio(0, 0), "nearstraight": ratio(0, 0),
                "strong_reverse": ratio(0, 0), "abs_difference_d": None,
                "abs_difference_base": None, "side_abs_difference": None}
    d0 = d_value(old_prediction)[mask]
    dc = d_value(candidate_prediction)[mask]
    b0 = base_value(old_prediction)[mask]
    bc = base_value(candidate_prediction)[mask]
    denom = np.abs(d0)
    nonzero = denom > 0
    ratio_values = np.abs(dc[nonzero]) / denom[nonzero]
    old = np.asarray(old_prediction, dtype=np.float64)[mask]
    candidate = np.asarray(candidate_prediction, dtype=np.float64)[mask]
    return {
        "frames": n,
        "denominator": n,
        "abs_amplitude_ratio": summary(ratio_values),
        "abs_amplitude_ratio_nonzero_old_denominator": int(nonzero.sum()),
        "half_amplitude": ratio((np.abs(dc) <= .5 * denom).sum(), n),
        "nearstraight": ratio((np.abs(dc) <= 5).sum(), n),
        "strong_reverse": ratio(((d0 * dc < 0) & (np.abs(dc) > 5)).sum(), n),
        "abs_difference_d": summary(np.abs(dc - d0)),
        "abs_difference_base": summary(np.abs(bc - b0)),
        "side_abs_difference": {side: summary(np.abs(candidate[:, i] - old[:, i]))
                                for i, side in enumerate(SIDES)},
    }


def identity_distribution(prediction):
    prediction = np.asarray(prediction, dtype=np.float64)
    if not np.isfinite(prediction).all():
        raise ValueError("nonfinite identity prediction")
    return {"frames": int(len(prediction)), "abs_d": summary(np.abs(d_value(prediction))),
            "abs_base": summary(np.abs(base_value(prediction))),
            "signed_d": summary(d_value(prediction)), "signed_base": summary(base_value(prediction))}


def quantization_delta(first, second, mask):
    mask = np.asarray(mask, dtype=bool)
    n = int(mask.sum())
    if n == 0:
        return {"frames": 0, "abs_difference_d": None, "abs_difference_base": None,
                "side_abs_difference": None}
    a = np.asarray(first, dtype=np.float64)[mask]
    b = np.asarray(second, dtype=np.float64)[mask]
    return {"frames": n,
            "abs_difference_d": summary(np.abs(d_value(a) - d_value(b))),
            "abs_difference_base": summary(np.abs(base_value(a) - base_value(b))),
            "side_abs_difference": {side: summary(np.abs(a[:, i] - b[:, i]))
                                    for i, side in enumerate(SIDES)}}


def baseline_gate(expected, actual, tolerance=GATE_TOLERANCE):
    expected = np.asarray(expected, dtype=np.float64)
    actual = np.asarray(actual, dtype=np.float64)
    if expected.shape != actual.shape or expected.ndim != 2 or expected.shape[1] != 2:
        raise ValueError("baseline gate expects [sample,2]")
    if not np.isfinite(expected).all() or not np.isfinite(actual).all():
        raise ValueError("baseline gate has nonfinite recorded or recomputed speed")
    error = np.abs(expected - actual)
    max_abs = float(error.max()) if error.size else 0.0
    rows = [{"sample_index": i, "recorded": expected[i].tolist(), "recomputed": actual[i].tolist(),
             "abs_error": error[i].tolist(), "max_abs": float(error[i].max()), "finite": True}
            for i in range(len(expected))]
    return {"status": "已验证" if max_abs <= tolerance else "失败", "frames": int(len(expected)),
            "threshold": float(tolerance), "max_abs": max_abs,
            "by_side_max_abs": {side: float(error[:, i].max()) if error.size else 0.0
                                for i, side in enumerate(SIDES)},
            "rows": rows, "recorded_is_raw": True,
            "raw_evidence": "metadata.metadata.speed_left/right written by FrameLog before packet encoding"}


def read_triplet(frame_dir, image_paths=None):
    """Read left/front/right in fixed order and return file and pixel hashes."""
    frame_dir = Path(frame_dir).resolve()
    if image_paths is None:
        image_paths = {role: frame_dir / f"{role}.png" for role in ROLES}
    if set(image_paths) != set(ROLES):
        raise ValueError("image mapping must be exactly left/front/right")
    images, pins = [], {}
    for role in ROLES:
        path = Path(image_paths[role])
        if not path.is_absolute():
            path = frame_dir / path
        path = path.resolve()
        if frame_dir not in path.parents and path.parent != frame_dir:
            raise ValueError("image path escapes its frame directory: " + str(path))
        if not path.is_file():
            raise FileNotFoundError("missing original image: " + str(path))
        data = path.read_bytes()
        image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None or image.shape != (480, 640, 3) or image.dtype != np.uint8:
            raise ValueError("image decode/shape/type failure: " + str(path))
        pin = {"role": role, "path": str(path), "relative": str(path.relative_to(frame_dir)),
               "bytes": len(data), "file_sha256": sha256_bytes(data),
               "pixel_sha256": sha256_bytes(image.tobytes()),
               "shape": [480, 640, 3], "dtype": "uint8"}
        images.append(image)
        pins[role] = pin
    return tuple(images), pins


def read_run_manifest(run_dir):
    path = Path(run_dir) / "snapshots/manifest.json"
    if not path.is_file():
        raise RuntimeError("missing snapshots/manifest.json; cannot verify recorded old raw: " + str(run_dir))
    value = json.loads(path.read_bytes().decode("utf-8"))
    context = value.get("context") or {}
    result = {"path": str(path), "sha256": sha256_file(path), "backend": context.get("backend"),
              "model_sha256": context.get("model_sha256") or {},
              "camera_order": value.get("camera_order")}
    if result["backend"] != "cpu":
        raise RuntimeError("recorded run backend is not cpu; metadata is not known raw CPU output; stop and ask")
    if (result["model_sha256"] or {}).get("original_cpu") != OLD_SHA:
        raise RuntimeError("recorded run model hash is not the pinned old NUC model; stop and ask")
    return result


def read_run_index(run_dir):
    run_dir = Path(run_dir).resolve()
    index = run_dir / "snapshots/index.jsonl"
    good, errors, zero_bytes, index_sha = set(), [], 0, None
    if index.is_file():
        raw = index.read_bytes()
        zero_bytes = raw.count(b"\x00")
        index_sha = sha256_bytes(raw)
        for line_number, line in enumerate(raw.splitlines(), 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line.decode("utf-8"))
                frame_id = value.get("frame_id") if isinstance(value, dict) else None
                path = value.get("path") if isinstance(value, dict) else None
                if not isinstance(frame_id, int) or frame_id < 1 or not isinstance(path, str):
                    raise ValueError("index record lacks integer frame_id/path")
                if path != f"frame_{frame_id:06d}":
                    raise ValueError("index path does not match frame_id")
                good.add(frame_id)
            except (ValueError, UnicodeError) as error:
                if len(errors) < 16:
                    errors.append({"line": line_number, "bytes": len(line),
                                   "leading_hex": line[:24].hex(), "error": type(error).__name__ + ": " + str(error)[:160]})
    snapshots = run_dir / "snapshots"
    directories = sorted(int(p.name[6:]) for p in snapshots.glob("frame_*")
                         if p.is_dir() and not p.name.endswith(".partial") and p.name[6:].isdigit())
    recovered = sorted(set(directories) - good)
    events = run_dir / "events.jsonl"
    close_kinds = []
    if events.is_file():
        for line in events.read_bytes().splitlines():
            try:
                value = json.loads(line.decode("utf-8"))
                if isinstance(value, dict) and value.get("kind") in ("frame_capture_closed", "stopped", "exit", "close_error"):
                    close_kinds.append(value.get("kind"))
            except (ValueError, UnicodeError):
                pass
    return {"path": str(run_dir), "index_path": str(index), "index_exists": index.is_file(),
            "index_sha256": index_sha, "index_zero_bytes": int(zero_bytes),
            "index_parse_errors": errors, "snapshot_directories": len(directories),
            "snapshot_frame_ids": directories,
            "good_frame_ids": sorted(good), "recovered_frame_ids": recovered,
            "close_kinds": close_kinds,
            "has_frame_capture_closed": "frame_capture_closed" in close_kinds,
            "missing_close_log": "frame_capture_closed" not in close_kinds}


def read_frames_csv(run_dir):
    path = Path(run_dir) / "frames.csv"
    if not path.is_file():
        return {}, None
    speeds = {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            try:
                frame_id = int(row["frame_id"])
                left, right = float(row["speed_left"]), float(row["speed_right"])
            except (KeyError, TypeError, ValueError):
                continue
            if np.isfinite([left, right]).all():
                speeds[frame_id] = (left, right)
    return speeds, sha256_file(path)


def read_recorded_old(frame_dir, frame_id, image_pins, frames_speeds):
    path = Path(frame_dir) / "metadata.json"
    if not path.is_file():
        raise RuntimeError("missing metadata.json; cannot prove recorded old is raw; stop and ask")
    metadata = json.loads(path.read_bytes().decode("utf-8"))
    row = metadata.get("metadata")
    if not isinstance(row, dict):
        raise RuntimeError("metadata.json has no wrapped 'metadata' object; stop and ask")
    if row.get("frame_id") != frame_id:
        raise ValueError("metadata frame_id mismatch")
    for key in ("speed_left", "speed_right"):
        if key not in row:
            raise RuntimeError("metadata is not raw speed_left/right; stop and ask")
    left, right = float(row["speed_left"]), float(row["speed_right"])
    if not np.isfinite([left, right]).all():
        raise ValueError("recorded old speed is nonfinite")
    if frame_id in frames_speeds:
        csv_left, csv_right = frames_speeds[frame_id]
        if abs(csv_left - left) > 1e-12 or abs(csv_right - right) > 1e-12:
            raise ValueError("metadata/frames.csv recorded old mismatch")
    hashes = metadata.get("sha256") or {}
    prefix = f"frame_{frame_id:06d}"
    for role in ROLES:
        key = f"{prefix}/{role}.png"
        if key in hashes and hashes[key] != image_pins[role]["file_sha256"]:
            raise ValueError("metadata image hash mismatch: " + key)
    pixels = metadata.get("original_pixels") or {}
    for role in ROLES:
        value = pixels.get(role) or {}
        if value.get("sha256") and value["sha256"] != image_pins[role]["pixel_sha256"]:
            raise ValueError("metadata pixel hash mismatch: " + role)
    return {"left": left, "right": right, "source": f"{prefix}/metadata.json#metadata.speed_left/right",
            "raw": True}, sha256_file(path)


def resolve_input_path(path, artifact_root):
    path = Path(path)
    if path.is_absolute():
        return path.resolve()
    candidate = (Path.cwd() / path).resolve()
    if candidate.exists():
        return candidate
    return (Path(artifact_root) / path).resolve()


def resolve_run_dir(sample, runs_root, artifact_root):
    run = sample.get("run")
    if not isinstance(run, str) or not run or Path(run).name != run or "\\" in run:
        raise ValueError("sample run must be a plain run directory name")
    if sample.get("run_dir"):
        path = Path(sample["run_dir"])
        if not path.is_absolute():
            path = Path(artifact_root) / path
    else:
        path = Path(runs_root) / run
    path = path.resolve()
    roots = (Path(runs_root).resolve(), Path(artifact_root).resolve())
    if not any(path == root or root in path.parents for root in roots):
        raise ValueError("run_dir escapes the configured artifact/runs roots")
    if not path.is_dir():
        raise FileNotFoundError("missing run directory: " + str(path))
    return path


def quality_flag(record, quality, names):
    for name in names:
        if name in quality:
            return quality[name]
        if name in record:
            return record[name]
    return None


def load_samples(path):
    value = json.loads(Path(path).read_bytes().decode("utf-8"))
    if isinstance(value, list):
        samples, meta = value, {}
    elif isinstance(value, dict):
        samples = value.get("samples", value.get("usable_samples", value.get("records")))
        meta = value
    else:
        raise ValueError("usable_samples.json must be an object or list")
    if isinstance(samples, dict):
        samples = list(samples.values())
    if not isinstance(samples, list) or not samples:
        raise ValueError("usable_samples.json has no samples")
    primary_run = meta.get("primary_run") or PRIMARY_RUN
    return samples, meta, primary_run


def prepare(samples_path, out, artifact_root=ARTIFACT_ROOT, runs_root=None):
    artifact_root = Path(artifact_root).resolve()
    runs_root = (resolve_input_path(runs_root, artifact_root) if runs_root
                 else artifact_root / "source_runs")
    samples_path = resolve_input_path(samples_path, artifact_root)
    out = Path(out).resolve()
    if out.parent != artifact_root:
        raise ValueError("output must be a fresh subdirectory of the artifact root")
    if not out.name.startswith("replay"):
        raise ValueError("output directory name must start with replay")
    out.mkdir(parents=True, exist_ok=False)
    dump(out / "prepare_claim.json", {"status": "prepared", "created_at": datetime.now(timezone.utc).isoformat(),
                                      "resume_supported": False, "samples": str(samples_path)})
    cv2.setNumThreads(1)
    samples, metadata, primary_run = load_samples(samples_path)
    run_cache, run_stats = {}, {}
    frames_cache = {}
    run_items = list(samples) + [item for item in (metadata.get("excluded") or []) if isinstance(item, dict)]
    for sample in run_items:
        if not isinstance(sample, dict):
            continue
        run = sample.get("run")
        if isinstance(run, str) and run not in run_cache:
            run_dir = resolve_run_dir(sample, runs_root, artifact_root)
            run_cache[run] = run_dir
            run_stats[run] = read_run_index(run_dir)
            run_stats[run]["manifest"] = read_run_manifest(run_dir)
            frames_cache[run] = read_frames_csv(run_dir)
            run_stats[run]["frames_csv_sha256"] = frames_cache[run][1]
    included, excluded = [], []
    declared_excluded_snapshot = 0
    declared_excluded_other = 0
    for item in metadata.get("excluded") or []:
        if not isinstance(item, dict):
            declared_excluded_other += 1
            continue
        run = item.get("run")
        frame_id = item.get("frame_id")
        if isinstance(frame_id, str) and frame_id.startswith("frame_"):
            frame_id = int(frame_id[6:]) if frame_id[6:].isdigit() else None
        if isinstance(frame_id, str) and frame_id.isdigit():
            frame_id = int(frame_id)
        snapshot_ids = set(run_stats.get(run, {}).get("snapshot_frame_ids", []))
        if not isinstance(frame_id, int) or frame_id not in snapshot_ids:
            declared_excluded_other += 1
            continue
        declared_excluded_snapshot += 1
        reasons = item.get("reasons") or item.get("exclusion_reasons") or item.get("reason") or ["declared excluded"]
        if isinstance(reasons, str):
            reasons = [reasons]
        excluded.append({"sample_id": item.get("sample_id") or f"{run}/frame_{frame_id:06d}",
                         "run": run, "frame_id": frame_id, "reasons": reasons})
    seen_samples = set()
    for index, sample in enumerate(samples):
        if not isinstance(sample, dict):
            raise ValueError("each sample must be an object")
        run = sample.get("run")
        frame_id = sample.get("frame_id")
        if isinstance(frame_id, str) and frame_id.startswith("frame_"):
            frame_id = int(frame_id[6:]) if frame_id[6:].isdigit() else None
        if isinstance(frame_id, str) and frame_id.isdigit():
            frame_id = int(frame_id)
        if not isinstance(frame_id, int) or frame_id < 1:
            raise ValueError("sample frame_id must be a positive integer")
        sample_id = sample.get("sample_id") or f"{run}/frame_{frame_id:06d}"
        if sample_id in seen_samples or (run, frame_id) in seen_samples:
            raise ValueError("duplicate sample: " + str(sample_id))
        seen_samples.add(sample_id)
        seen_samples.add((run, frame_id))
        usable = sample.get("usable", sample.get("included", True))
        quality = sample.get("quality") or {}
        reasons = sample.get("exclusion_reasons") or sample.get("reasons") or []
        if isinstance(reasons, str):
            reasons = [reasons]
        if sample.get("exclude_reason"):
            reasons = list(reasons) + [sample["exclude_reason"]]
        if not usable:
            excluded.append({"sample_id": sample_id, "run": run, "frame_id": frame_id,
                             "reasons": reasons or ["declared unusable"]})
            continue
        full = quality_flag(sample, quality, ("full", "complete", "three_images"))
        if full is None:
            excluded.append({"sample_id": sample_id, "run": run, "frame_id": frame_id,
                             "reasons": ["quality.full missing; not guessed, so not included"]})
            continue
        if not isinstance(full, bool):
            raise ValueError("quality.full must be boolean for " + str(sample_id))
        if not full:
            excluded.append({"sample_id": sample_id, "run": run, "frame_id": frame_id,
                             "reasons": ["quality.full=false: incomplete sample"]})
            continue
        try:
            run_dir = run_cache[run]
            frame_dir = run_dir / "snapshots" / f"frame_{frame_id:06d}"
            images, image_pins = read_triplet(frame_dir, sample.get("images"))
            frames_speeds = frames_cache[run][0]
            recorded, metadata_sha = read_recorded_old(frame_dir, frame_id, image_pins, frames_speeds)
            if sample.get("recorded_old"):
                given = sample["recorded_old"]
                if given.get("raw") is not True:
                    raise RuntimeError("sample recorded_old is not marked raw; stop and ask")
                if abs(float(given["left"]) - recorded["left"]) > 1e-12 or abs(float(given["right"]) - recorded["right"]) > 1e-12:
                    raise ValueError("sample recorded_old disagrees with metadata raw speed")
            fresh = quality_flag(sample, quality, ("fresh", "all_fresh"))
            if fresh is not None and not isinstance(fresh, bool):
                raise ValueError("quality.fresh must be boolean for " + str(sample_id))
            declared_primary = quality_flag(sample, quality, ("primary",))
            if declared_primary is not None and not isinstance(declared_primary, bool):
                raise ValueError("quality.primary must be boolean for " + str(sample_id))
            stats = run_stats[run]
            index_valid = frame_id in set(stats["good_frame_ids"])
            recovered = frame_id in set(stats["recovered_frame_ids"])
            reported_valid = quality_flag(sample, quality, ("index_valid", "index_ok"))
            reported_recovered = quality_flag(sample, quality, ("recovered", "recovered_from_dir"))
            index_conflict = ((reported_valid is not None and bool(reported_valid) != index_valid)
                              or (reported_recovered is not None and bool(reported_recovered) != recovered))
            included.append({
                "sample_id": sample_id, "sample_index": len(included), "run": run,
                "frame_id": frame_id, "run_dir": str(run_dir), "frame_dir": str(frame_dir),
                "images": image_pins, "recorded_old": recorded, "metadata_sha256": metadata_sha,
                "quality": {"usable": True, "full": bool(full) if full is not None else False,
                            "full_missing": full is None,
                            "fresh": None if fresh is None else bool(fresh),
                            "fresh_missing": fresh is None,
                            "primary": bool(declared_primary) if declared_primary is not None else run == primary_run,
                            "index_valid": index_valid, "recovered": recovered},
                "index_flag_conflict": index_conflict,
                "quality_reported": quality,
            })
        except (OSError, ValueError, KeyError, RuntimeError) as error:
            excluded.append({"sample_id": sample_id, "run": run, "frame_id": frame_id,
                             "reasons": [type(error).__name__ + ": " + str(error)]})
    if not included:
        dump(out / "prepare_excluded.json", {"status": "失败", "excluded": excluded,
                                             "reason": "no usable complete samples after quality/image validation"})
        first = excluded[0]["reasons"][0] if excluded and excluded[0].get("reasons") else "unknown"
        raise ValueError("no usable complete samples after quality/image validation (%d excluded; first: %s)" % (len(excluded), first))
    model_pins = []
    for spec in MODEL_SPECS:
        path = Path(spec["path"])
        if not path.is_file():
            raise FileNotFoundError("missing fixed model: " + str(path))
        actual = sha256_file(path)
        if actual != spec["sha256"]:
            raise ValueError(f"fixed model SHA256 mismatch: {path} is {actual}")
        model_pins.append({"name": spec["name"], "kind": spec["kind"], "path": str(path),
                           "sha256": actual, "bytes": path.stat().st_size, "role": spec["role"]})
    for path, expected in ((REF, REF_SHA), (PARAMS, PARAMS_SHA)):
        actual = sha256_file(path)
        if actual != expected:
            raise ValueError(f"fixed source SHA256 mismatch: {path} is {actual}")
    counts = {}
    for run in sorted(run_cache):
        included_count = sum(1 for s in included if s["run"] == run)
        excluded_count = sum(1 for s in excluded if s["run"] == run)
        not_listed = run_stats[run]["snapshot_directories"] - included_count - excluded_count
        if not_listed < 0:
            raise ValueError("quality sample/excluded lists overlap for " + run)
        counts[run] = {"snapshot_directories": run_stats[run]["snapshot_directories"],
                       "listed_samples": included_count + excluded_count,
                       "included": included_count,
                       "excluded": excluded_count,
                       "not_listed": not_listed,
                       "index_valid": sum(1 for s in included if s["run"] == run and s["quality"]["index_valid"]),
                       "recovered": sum(1 for s in included if s["run"] == run and s["quality"]["recovered"]),
                       "missing_close_log": run_stats[run]["missing_close_log"],
                       "index_zero_bytes": run_stats[run]["index_zero_bytes"],
                       "index_parse_errors": run_stats[run]["index_parse_errors"]}
    contract = {
        "schema": "field_capture_replay_contract_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "artifact_root": str(artifact_root), "runs_root": str(runs_root),
        "samples_path": str(samples_path), "samples_sha256": sha256_file(samples_path),
        "primary_run": primary_run, "preprocess": PREPROCESS,
        "quality_rules": metadata.get("quality_rules"),
        "quality_sample_count": metadata.get("sample_count"),
        "quality_usable_count": metadata.get("usable_count"),
        "quality_declared_excluded_count": len(metadata.get("excluded") or []),
        "quality_excluded_snapshot_count": declared_excluded_snapshot,
        "quality_excluded_not_snapshot_count": declared_excluded_other,
        "models": model_pins,
        "ref": {"path": str(REF), "sha256": REF_SHA, "bytes": REF.stat().st_size},
        "params": {"path": str(PARAMS), "sha256": PARAMS_SHA, "bytes": PARAMS.stat().st_size},
        "script": {"path": str(Path(__file__).resolve()), "sha256": sha256_file(__file__)},
        "test": ({"path": str(TEST_FILE.resolve()), "sha256": sha256_file(TEST_FILE)}
                 if TEST_FILE.is_file() else None),
        "session_plan": {"ort_sessions": 5, "integer_reference_kernels": 1,
                         "ort_calls_per_sample": 5, "integer_calls_per_sample": 1,
                         "models": list(MODEL_NAMES), "sides": list(SIDES)},
        "invariants": {"identity_only": True, "no_training": True, "no_device_io": True,
                       "same_original_images_for_fp32_and_hybrid": True,
                       "one_shared_int8_feature_for_both_hybrids": True,
                       "reference_mask_from_old_only": True,
                       "mask_does_not_use_candidate_outputs": True,
                       "no_accuracy_or_groundtruth": True},
        "counts": counts, "samples": included, "excluded": excluded,
        "run_index": run_stats,
    }
    dump(out / "contract.json", contract)
    prepare_status = {"status": "prepared", "contract_sha256": sha256_file(out / "contract.json"),
                      "included": len(included), "excluded": len(excluded),
                      "models": {p["name"]: p["sha256"] for p in model_pins},
                      "session_created": False, "onnxruntime_imported": "onnxruntime" in sys.modules,
                      "counts": counts}
    dump(out / "prepare_status.json", prepare_status)
    print(json.dumps({"stage": "prepare", "status": "prepared", "included": len(included),
                      "excluded": len(excluded), "out": str(out)}, ensure_ascii=False), flush=True)


def verify_pin(pin, label):
    path = Path(pin["path"])
    if not path.is_file():
        raise FileNotFoundError("missing frozen input: " + str(path))
    actual = sha256_file(path)
    if pin.get("bytes") is not None and path.stat().st_size != pin["bytes"]:
        raise ValueError("frozen size mismatch: " + label)
    expected = pin.get("sha256") or pin.get("file_sha256")
    if not expected:
        raise ValueError("frozen pin has no file hash: " + label)
    if actual != expected:
        raise ValueError("frozen SHA256 mismatch: " + label)


def verify_contract(contract, samples_path):
    if sha256_file(samples_path) != contract["samples_sha256"]:
        raise ValueError("usable_samples.json changed after prepare")
    for pin in contract["models"]:
        verify_pin(pin, "model " + pin["name"])
    verify_pin(contract["ref"], "ref_int8.py")
    verify_pin(contract["params"], "params.json")
    verify_pin(contract["script"], "field_replay.py")
    if contract.get("test"):
        verify_pin(contract["test"], "test_field_replay.py")
    for sample in contract["samples"]:
        for role in ROLES:
            pin = sample["images"][role]
            verify_pin(pin, sample["sample_id"] + "/" + role)
        if sha256_file(Path(sample["frame_dir"]) / "metadata.json") != sample["metadata_sha256"]:
            raise ValueError("metadata changed after prepare: " + sample["sample_id"])
    for run, stats in contract.get("run_index", {}).items():
        manifest = stats.get("manifest")
        if manifest:
            verify_pin(manifest, "run manifest " + run)


def load_pinned_images(sample):
    images = []
    for role in ROLES:
        pin = sample["images"][role]
        data = Path(pin["path"]).read_bytes()
        if len(data) != pin["bytes"] or sha256_bytes(data) != pin["file_sha256"]:
            raise ValueError("original image changed after prepare: " + sample["sample_id"] + "/" + role)
        image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None or image.shape != (480, 640, 3) or image.dtype != np.uint8:
            raise ValueError("image decode/shape/type failure: " + sample["sample_id"] + "/" + role)
        if sha256_bytes(image.tobytes()) != pin["pixel_sha256"]:
            raise ValueError("decoded pixel hash mismatch: " + sample["sample_id"] + "/" + role)
        images.append(image)
    return tuple(images)


def check_cpu_session(session, label):
    options = session.get_session_options()
    if session.get_providers() != ["CPUExecutionProvider"] or options.intra_op_num_threads != 1 or options.inter_op_num_threads != 1:
        raise ValueError(f"{label} must use CPUExecutionProvider and ORT intra/inter threads=1")


def interface(session, kind, label):
    inputs, outputs = session.get_inputs(), session.get_outputs()
    if len(inputs) != 1:
        raise ValueError(f"{label} expects exactly one input")
    expected = [1, 3, 3, 66, 200] if kind == "image" else list(OUT_SHAPE)
    if list(inputs[0].shape) != expected or inputs[0].type != "tensor(float)":
        raise ValueError(f"{label} input contract is not float {expected}")
    names = [output.name for output in outputs]
    if "speed_raw" in names:
        output = "speed_raw"
    elif kind == "partb" and len(outputs) == 1:
        output = outputs[0].name
    else:
        raise ValueError(f"{label} output contract is not speed_raw")
    check_cpu_session(session, label)
    return {"input": inputs[0].name, "output": output, "input_shape": expected,
            "camera_order": list(ROLES), "side_order": list(SIDES)}


def checked_pair(value):
    value = np.asarray(value, dtype=np.float32).reshape(-1)
    if value.shape != (2,) or not np.isfinite(value).all():
        raise ValueError("prediction must be finite shape (2,)")
    return value


def predict(session, contract, tensor):
    tensor = np.asarray(tensor)
    if tensor.shape != (1, 3, 3, 66, 200) or tensor.dtype != np.float32 or not np.isfinite(tensor).all():
        raise ValueError("production tensor must be finite float32 [1,3,3,66,200]")
    return checked_pair(np.asarray(session.run([contract["output"]], {contract["input"]: tensor})[0]))


def integer_feature(images, kernel, lut, dequant, pre_lut_fn):
    pre_lut_fn(images, lut, kernel.inbuf)
    out = kernel.run()
    feature = np.take(dequant, np.asarray(out).view(np.uint8)).reshape(OUT_SHAPE)
    if feature.dtype != np.float32 or not np.isfinite(feature).all():
        raise ValueError("integer feature is not finite float32")
    return feature


def write_csv(path, rows):
    rows = iter(rows)
    first = next(rows)
    with Path(path).open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(first))
        writer.writeheader()
        writer.writerow(first)
        writer.writerows(rows)


def build_strata(arrays):
    run = arrays["run"]
    primary = arrays["primary"]
    fresh = arrays["fresh"]
    full = arrays["full"]
    index_valid = arrays["index_valid"]
    recovered = arrays["recovered"]
    result = {"all": np.ones(len(run), dtype=bool)}
    for name in sorted(set(run.tolist())):
        result[f"run:{name}"] = run == name
    result["fresh"] = fresh
    result["full"] = full
    result["primary"] = primary
    result["primary_index_valid"] = primary & index_valid
    result["primary_recovered"] = primary & recovered
    result["secondary"] = ~primary
    return result


def make_report(contract, arrays, gate, calls, kernel_starts, samples_path, contract_sha256):
    prediction = arrays["prediction"]
    old = prediction[:, 0]
    masks = {"old_abs_d_gt15": arrays["old_abs_d_gt15"], "old_abs_d_gt40": arrays["old_abs_d_gt40"]}
    strata = build_strata(arrays)
    report_strata = {}
    for name, mask in strata.items():
        mask = np.asarray(mask, dtype=bool)
        n = int(mask.sum())
        identity = {model: identity_distribution(prediction[mask, j])
                    for j, model in enumerate(MODEL_NAMES)}
        reference = {}
        for mask_name, base_mask in masks.items():
            fixed = mask & base_mask
            reference[mask_name] = {
                "definition": "old_nuc |d|>15" if mask_name.endswith("15") else "old_nuc |d|>40",
                "frames": int(fixed.sum()),
                "candidates": {model: response_metrics(old, prediction[:, j], fixed)
                               for j, model in enumerate(MODEL_NAMES[1:], 1)},
            }
        report_strata[name] = {
            "frames": n,
            "fresh_missing": int(np.sum(mask & arrays["fresh_missing"])),
            "full_missing": int(np.sum(mask & arrays["full_missing"])),
            "identity_distribution": identity,
            "reference_masks": reference,
            "quantization_delta": {
                "new_fp32_vs_new_hybrid": {
                    "note": "same new model: FP32 full vs exact integer front + original part B",
                    "all_samples": quantization_delta(prediction[:, 1], prediction[:, 2], mask),
                    "old_mask15": quantization_delta(prediction[:, 1], prediction[:, 2], mask & masks["old_abs_d_gt15"]),
                },
                "b_full_vs_b_hybrid": {
                    "note": "candidate B: full FP32 vs same exact integer feature + B part B",
                    "all_samples": quantization_delta(prediction[:, 3], prediction[:, 4], mask),
                    "old_mask15": quantization_delta(prediction[:, 3], prediction[:, 4], mask & masks["old_abs_d_gt15"]),
                },
            },
        }
    quality_counts = {
        "included": int(len(arrays["run"])),
        "full": int(arrays["full"].sum()), "full_missing": int(arrays["full_missing"].sum()),
        "fresh": int(arrays["fresh"].sum()),
        "fresh_missing": int(arrays["fresh_missing"].sum()),
        "primary": int(arrays["primary"].sum()),
        "primary_index_valid": int((arrays["primary"] & arrays["index_valid"]).sum()),
        "primary_recovered": int((arrays["primary"] & arrays["recovered"]).sum()),
        "index_conflict": int(arrays["index_conflict"].sum()),
    }
    primary_stats = contract["run_index"].get(contract["primary_run"], {})
    primary_note = ("Primary run has %s snapshot directories, %s intact index records, %s zero index bytes "
                    "and missing_close_log=%s; recovered directories are reported separately." % (
                        primary_stats.get("snapshot_directories"), len(primary_stats.get("good_frame_ids", [])),
                        primary_stats.get("index_zero_bytes"), primary_stats.get("missing_close_log")))
    return {
        "schema": "field_capture_replay_report_v1",
        "status": "已验证",
        "scope": "local CPU identity replay; no human truth, no accuracy, no physical direction/curvature claim",
        "contract_sha256": contract_sha256,
        "samples_path": str(samples_path), "samples_sha256": contract["samples_sha256"],
        "primary_run": contract["primary_run"],
        "model_names": list(MODEL_NAMES),
        "model_sha256": {pin["name"]: pin["sha256"] for pin in contract["models"]},
        "ref_params_sha256": {"ref_int8.py": contract["ref"]["sha256"], "params.json": contract["params"]["sha256"]},
        "preprocess": contract["preprocess"],
        "quality_rules": contract.get("quality_rules"),
        "quality_declared": {"sample_count": contract.get("quality_sample_count"),
                             "usable_count": contract.get("quality_usable_count"),
                             "excluded_count": contract.get("quality_declared_excluded_count"),
                             "excluded_snapshot_count": contract.get("quality_excluded_snapshot_count"),
                             "excluded_not_snapshot_count": contract.get("quality_excluded_not_snapshot_count")},
        "baseline_gate": {key: gate[key] for key in ("status", "frames", "threshold", "max_abs", "by_side_max_abs", "recorded_is_raw", "raw_evidence")},
        "call_counts": calls, "reference_kernel_starts": int(kernel_starts),
        "quality_counts": quality_counts,
        "counts": contract["counts"],
        "runs_listed": sorted(contract["counts"]),
        "secondary_runs_listed": [run for run in sorted(contract["counts"]) if run != contract["primary_run"]],
        "secondary_coverage_warning": None if len(contract["counts"]) >= 4 else
        "fewer than four runs listed; secondary coverage may be incomplete, do not treat primary as the only run",
        "run_index": contract["run_index"],
        "excluded": contract["excluded"],
        "strata": report_strata,
        "definitions": {
            "d": "(left - right) / 2", "base": "(left + right) / 2",
            "reference_mask": "fixed by old_nuc identity only: |d|>15, secondary |d|>40; candidates never enter mask",
            "half_amplitude": "|candidate d| <= 0.5 * |old d| on the fixed old mask",
            "nearstraight": "|candidate d| <= 5 on the fixed old mask",
            "strong_reverse": "old d * candidate d < 0 and |candidate d| > 5 on the fixed old mask",
            "denominator_rule": "every candidate uses the same fixed old-mask denominator; no candidate-specific filtering",
            "quantization": "only new FP32 vs new hybrid and B full vs B hybrid; old vs new is not quantization",
        },
        "limitations": [
            "No human labels or trajectory truth; no accuracy, MAE against labels or old-as-groundtruth claim.",
            "Old-based masks select reference response only; they do not define physical left/right or turn curvature.",
            "No field brightness causal conclusion and no counterfactual illumination; this replay is identity only.",
            primary_note,
            "Single-camera anomaly/repeated-frame exclusions come only from the quality sample list; excluded counts and repeated-frame bias remain.",
            "Repeated pixels and repeated timestamps are not deduplicated or treated as stop frames; the quality labels decide inclusion and freshness.",
            "fresh labels are consumed as given (production age<=200ms, host skew<=100ms) and are never inferred or used to drop samples here.",
            "Local CPU ORT, not board PL or vehicle validation; no camera/serial/pynq import or control output.",
            "ORT cross-platform differences are tolerated only by the 0.001 old baseline gate; larger differences stop the run.",
        ],
        "not_executed": ["remote/board", "real PL", "camera/serial/motor/closed-loop", "training or model changes",
                         "brightness/perturbation repair", "field root cause", "two-lap or trajectory completion"],
    }


def run(samples_path, out, artifact_root=ARTIFACT_ROOT, runs_root=None):
    artifact_root = Path(artifact_root).resolve()
    out = Path(out).resolve()
    samples_path = resolve_input_path(samples_path, artifact_root)
    contract = json.loads((out / "contract.json").read_text(encoding="utf-8"))
    status = json.loads((out / "prepare_status.json").read_text(encoding="utf-8"))
    if sha256_file(out / "contract.json") != status["contract_sha256"]:
        raise ValueError("contract seal mismatch")
    verify_contract(contract, samples_path)
    dump(out / "run_claim.json", {"status": "running", "started_at": datetime.now(timezone.utc).isoformat(),
                                  "resume_supported": False})
    cv2.setNumThreads(1)
    if any(name == "pynq" or name.startswith("pynq.") or name == "serial" or name.startswith("serial.")
           for name in sys.modules):
        raise RuntimeError("unexpected pynq/serial import")
    import onnxruntime as ort
    from ps.pl_infer import (ReferenceKernel, build_input_lut, check_params, dequant_table,
                             load_module, make_session, pre_lut)
    params = json.loads(Path(contract["params"]["path"]).read_text(encoding="utf-8"))
    check_params(params)
    samples = contract["samples"]
    n = len(samples)
    started = time.monotonic()
    environment = {"python": sys.version, "python_executable": sys.executable, "numpy": np.__version__,
                   "opencv": cv2.__version__, "onnxruntime": ort.__version__, "platform": platform.platform(),
                   "cwd": str(Path.cwd()), "ort_intra_threads": 1, "ort_inter_threads": 1,
                   "cv2_threads": cv2.getNumThreads(), "provider": "CPUExecutionProvider",
                   "device": "local Windows CPU only", "benchmark": "未执行", "bytecode_written": False}
    dump(out / "environment.json", environment)
    dump(out / "command.json", {"argv": sys.argv, "started_at": datetime.now(timezone.utc).isoformat(),
                                "sample_count": n, "scope": "offline identity replay only"})
    model_paths = {pin["name"]: pin["path"] for pin in contract["models"]}
    old_session = make_session(model_paths["old_nuc"], 1)
    old_interface = interface(old_session, "image", "old_nuc")
    recorded = np.asarray([[s["recorded_old"]["left"], s["recorded_old"]["right"]] for s in samples], dtype=np.float32)
    recomputed = np.empty((n, 2), dtype=np.float32)
    baseline_rows = []
    for i, sample in enumerate(samples):
        images = load_pinned_images(sample)
        tensor = preprocess_arrays(*images)
        recomputed[i] = predict(old_session, old_interface, tensor)
        error = np.abs(recomputed[i].astype(np.float64) - recorded[i].astype(np.float64))
        baseline_rows.append({"sample_index": i, "sample_id": sample["sample_id"], "run": sample["run"],
                              "frame_id": sample["frame_id"], "recorded": recorded[i].tolist(),
                              "recomputed": recomputed[i].tolist(), "abs_error": error.tolist(),
                              "max_abs": float(error.max()), "finite": True})
        if (i + 1) % 64 == 0 or i + 1 == n:
            print(f"baseline {i + 1}/{n}", flush=True)
    gate = baseline_gate(recorded, recomputed)
    gate["rows"] = baseline_rows
    with (out / "baseline_predictions.npz").open("xb") as stream:
        np.savez_compressed(stream, sample=np.array([s["sample_id"] for s in samples]),
                            recorded_old=recorded, recomputed_old=recomputed,
                            abs_error=np.abs(recomputed.astype(np.float64) - recorded.astype(np.float64)))
    dump(out / "baseline_gate.json", gate)
    if gate["status"] != "已验证":
        failure = {"status": "失败", "stage": "baseline_gate", "first_error": "old baseline maxabs exceeds 0.001",
                   "max_abs": gate["max_abs"], "threshold": gate["threshold"],
                   "note": "per-row errors are retained in baseline_gate.json; no candidate inference was run"}
        dump(out / "run_failure.json", failure)
        dump(out / "run_record.json", {"status": "失败", "stage": "baseline_gate",
                                       "elapsed_seconds_run_record_only": time.monotonic() - started,
                                       "sessions_created": 1, **failure})
        print(json.dumps(failure, ensure_ascii=False), flush=True)
        return 1
    new_session = make_session(model_paths["new_fp32"], 1)
    bfull_session = make_session(model_paths["b_full"], 1)
    partb_orig_session = make_session(model_paths["new_hybrid"], 1)
    partb_b_session = make_session(model_paths["b_hybrid"], 1)
    interfaces = {"new_fp32": interface(new_session, "image", "new_fp32"),
                  "b_full": interface(bfull_session, "image", "b_full"),
                  "new_hybrid": interface(partb_orig_session, "partb", "new_hybrid"),
                  "b_hybrid": interface(partb_b_session, "partb", "b_hybrid")}
    ref_module = load_module("ref_int8", contract["ref"]["path"])
    kernel = ReferenceKernel(ref_module, params)
    lut, dequant = build_input_lut(), dequant_table()
    prediction = np.full((n, len(MODEL_NAMES), 2), np.nan, dtype=np.float32)
    feature_sha = []
    calls = {name: 0 for name in MODEL_NAMES}
    calls["old_nuc"] = n
    for i, sample in enumerate(samples):
        images = load_pinned_images(sample)
        tensor = preprocess_arrays(*images)
        prediction[i, 0] = recomputed[i]
        prediction[i, 1] = predict(new_session, interfaces["new_fp32"], tensor)
        prediction[i, 3] = predict(bfull_session, interfaces["b_full"], tensor)
        calls["new_fp32"] += 1
        calls["b_full"] += 1
        feature = integer_feature(images, kernel, lut, dequant, pre_lut)
        feature_sha.append(sha256_bytes(feature.tobytes()))
        prediction[i, 2] = checked_pair(partb_orig_session.run(
            [interfaces["new_hybrid"]["output"]], {interfaces["new_hybrid"]["input"]: feature})[0])
        prediction[i, 4] = checked_pair(partb_b_session.run(
            [interfaces["b_hybrid"]["output"]], {interfaces["b_hybrid"]["input"]: feature})[0])
        calls["new_hybrid"] += 1
        calls["b_hybrid"] += 1
        if not np.isfinite(prediction[i]).all():
            raise ValueError("nonfinite prediction at " + sample["sample_id"])
        if (i + 1) % 32 == 0 or i + 1 == n:
            print(f"identity {i + 1}/{n}", flush=True)
    expected_calls = {name: n for name in MODEL_NAMES}
    if calls != expected_calls:
        raise ValueError(f"ORT call-count mismatch: {calls} != {expected_calls}")
    if kernel.starts != n:
        raise ValueError(f"integer reference start count {kernel.starts} != {n}")
    arrays = {
        "prediction": prediction,
        "model_names": np.array(MODEL_NAMES),
        "model_sha256": np.array([next(pin["sha256"] for pin in contract["models"] if pin["name"] == name)
                                  for name in MODEL_NAMES]),
        "side_names": np.array(SIDES),
        "sample": np.array([s["sample_id"] for s in samples]),
        "run": np.array([s["run"] for s in samples]),
        "frame_id": np.array([s["frame_id"] for s in samples], dtype=np.int64),
        "recorded_old": recorded,
        "baseline_abs_error": np.abs(recomputed.astype(np.float64) - recorded.astype(np.float64)).astype(np.float32),
        "old_abs_d_gt15": np.abs(d_value(prediction[:, 0])) > 15,
        "old_abs_d_gt40": np.abs(d_value(prediction[:, 0])) > 40,
        "feature_sha256": np.array(feature_sha),
        "full": np.array([s["quality"]["full"] for s in samples], dtype=bool),
        "full_missing": np.array([s["quality"]["full_missing"] for s in samples], dtype=bool),
        "fresh": np.array([bool(s["quality"]["fresh"]) for s in samples], dtype=bool),
        "fresh_missing": np.array([s["quality"]["fresh_missing"] for s in samples], dtype=bool),
        "primary": np.array([s["quality"]["primary"] for s in samples], dtype=bool),
        "index_valid": np.array([s["quality"]["index_valid"] for s in samples], dtype=bool),
        "recovered": np.array([s["quality"]["recovered"] for s in samples], dtype=bool),
        "index_conflict": np.array([s["index_flag_conflict"] for s in samples], dtype=bool),
    }
    with (out / "predictions.npz").open("xb") as stream:
        np.savez_compressed(stream, **arrays)
    old_d = d_value(prediction[:, 0])
    old_base = base_value(prediction[:, 0])
    def rows():
        for i, sample in enumerate(samples):
            for j, model in enumerate(MODEL_NAMES):
                for k, side in enumerate(SIDES):
                    yield {"sample": sample["sample_id"], "model": model, "side": side,
                           "run": sample["run"], "frame_id": sample["frame_id"],
                           "value": float(prediction[i, j, k]), "finite": bool(np.isfinite(prediction[i, j, k])),
                           "d": float(d_value(prediction[i, j])), "base": float(base_value(prediction[i, j])),
                           "old_d": float(old_d[i]), "old_base": float(old_base[i]),
                           "delta_d_vs_old": float(d_value(prediction[i, j]) - old_d[i]),
                           "delta_base_vs_old": float(base_value(prediction[i, j]) - old_base[i]),
                           "old_mask15": bool(arrays["old_abs_d_gt15"][i]),
                           "old_mask40": bool(arrays["old_abs_d_gt40"][i]),
                           "full": bool(arrays["full"][i]), "fresh": bool(arrays["fresh"][i]),
                           "primary": bool(arrays["primary"][i]),
                           "index_valid": bool(arrays["index_valid"][i]),
                           "recovered": bool(arrays["recovered"][i])}
    write_csv(out / "predictions.csv", rows())
    call_counts = {"sessions_created": 5, "ort_calls": calls, "ort_calls_per_sample": 5,
                   "integer_reference_calls": {"count": n, "per_sample": 1},
                   "shared_feature_per_sample": True, "models": list(MODEL_NAMES)}
    report = make_report(contract, arrays, gate, call_counts, kernel.starts, samples_path,
                         sha256_file(out / "contract.json"))
    report["status"] = "已验证"
    report["report_contract_path"] = str(out / "contract.json")
    dump(out / "report.json", report)
    run_record = {"status": "已验证", "started_at": datetime.now(timezone.utc).isoformat(),
                  "elapsed_seconds_run_record_only": time.monotonic() - started,
                  "not_a_benchmark": True, "samples": n, "models": len(MODEL_NAMES),
                  "predictions": int(n * len(MODEL_NAMES) * len(SIDES)),
                  "call_counts": call_counts, "integer_reference_starts": kernel.starts,
                  "baseline_max_abs": gate["max_abs"], "baseline_threshold": gate["threshold"]}
    dump(out / "run_record.json", run_record)
    primary_stats = contract["run_index"].get(contract["primary_run"], {})
    primary_note = ("Primary run has %s snapshot directories, %s intact index records, %s zero index bytes "
                    "and missing_close_log=%s; recovered directories are listed separately." % (
                        primary_stats.get("snapshot_directories"), len(primary_stats.get("good_frame_ids", [])),
                        primary_stats.get("index_zero_bytes"), primary_stats.get("missing_close_log")))
    write_text(out / "README.md", f"""# Field identity replay {out.name}

Local Windows CPU only. No camera, serial, motor, PYNQ or PL access; no training,
model/config/production changes and no automatic brightness correction.

## Frozen inputs
- Contract: `contract.json` (samples, model/ref/source hashes, every original PNG file/pixel hash) is sealed before any ORT session.
- Five paths: `old_nuc`, `new_fp32`, `new_hybrid` (exact integer conv0-2 + original part B), `b_full`, `b_hybrid` (same exact integer feature + B part B).
- ORT sessions: 5; ORT calls: 5/sample; exact integer reference: 1/sample. Every sample uses the same original images and one shared int8 feature for both hybrid paths.
- Baseline gate: recomputed old-NUC CPU output against `metadata.metadata.speed_left/right`, maxabs <= 0.001 per frame. Per-row errors are retained; any excess stops before candidate inference.

## Metrics and limits
- d=(L-R)/2, base=(L+R)/2. No human labels or trajectory truth: no accuracy and old is not ground truth.
- The fixed reference mask is `old_nuc |d|>15` (secondary `|d|>40`); candidate outputs never affect it. All candidates use that same denominator.
- Half amplitude, nearstraight and reverse-sign counts retain numerator/denominator. FP32-vs-hybrid differences are quantization comparisons only for the same new model or candidate B; old-vs-new is not a quantization comparison.
- Strata: all, each run, fresh, full, primary, primary_index_valid, primary_recovered, secondary. Missing fresh/full labels are not guessed; they are reported as missing and stay out of that stratum.
- `fresh` is consumed from the quality file only (declared production rule age<=200ms and host skew<=100ms); it labels, never drops, samples. Repeated pixels/timestamps are not deduplicated or treated as stop frames.
- `{primary_note}` No physical left/right, curvature, two-lap or field brightness causal claim.
- `predictions.npz` axes `[sample,model,side]`; `predictions.csv` is long form sample/model/side. `report.json` holds per-stratum distributions and denominators.
""")
    products = [{"path": path.name, "bytes": path.stat().st_size, "sha256": sha256_file(path)}
                for path in sorted(out.iterdir()) if path.is_file() and path.name != "outputs_manifest.json"]
    dump(out / "outputs_manifest.json", {"status": "已验证", "files": products, "self_excluded": True,
                                        "independent_numerical_acceptance": "未执行; coordinator recomputes independently"})
    print(json.dumps({"stage": "complete", "status": "已验证", "samples": n,
                      "predictions": n * len(MODEL_NAMES) * len(SIDES),
                      "baseline_max_abs": gate["max_abs"], "out": str(out)}, ensure_ascii=False), flush=True)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "run"))
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, default=ARTIFACT_ROOT)
    parser.add_argument("--runs-root", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.mode == "prepare":
            prepare(args.samples, args.out, args.artifact_root, args.runs_root)
        else:
            return run(args.samples, args.out, args.artifact_root, args.runs_root)
    except Exception as error:
        out = Path(args.out).resolve()
        if out.is_dir():
            failure = out / (args.mode + "_failure.json")
            if not failure.exists():
                dump(failure, {"status": "失败", "stage": args.mode, "type": type(error).__name__,
                               "first_error": str(error), "time": datetime.now(timezone.utc).isoformat()})
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
