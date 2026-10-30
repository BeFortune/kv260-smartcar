"""Read-only fixed data identities. No camera, vehicle, network, or training imports."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import cv2
import numpy as np
from attention_rewrite import WORKSPACE, SOURCE_SHA256

PROJECT = WORKSPACE / "pynq_ffn"
CACHE = PROJECT / "artifacts/field_turning_diagnosis_20261027/run01/results/field_cache01/field_identity_cache.npz"
CACHE_SHA = "595da92b771545e3403c8dede3212e8983b2266063007813203f9de9b2f39e6c"
CONTRACT = PROJECT / "artifacts/field_capture_analysis_20261026/replay_identity02/contract.json"
CONTRACT_SHA = "2a4bebfa39bc9d55529febdef303c86f870e6f9ad79c55d602b07ac043008033"
LUT = PROJECT / "artifacts/model_train_v1_20261018/data_prepare/cache01/norm_lut.npy"
LUT_SHA = "4072338fae616cebd0b2548fafbfc4e4c5f153b6926d88075d33855cd1c97b76"
PREPROCESS = PROJECT / "ps/preprocess.py"
PREPROCESS_SHA = "6134e12a3a2f67d92c8b43f7801f21b0e9d570d0f153ce81ca75d623d8120ddb"


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(8 << 20), b""):
            value.update(block)
    return value.hexdigest()


def pinned(path, expected):
    actual = digest(path)
    if actual != expected:
        raise ValueError(f"Pinned input SHA changed: {path}")
    return {"path": str(path), "sha256": actual, "bytes": path.stat().st_size}


@dataclass
class DataSet:
    name: str
    images: np.ndarray
    sample_ids: np.ndarray
    segments: np.ndarray
    provenance: dict

    def input(self, index):
        return self.images[index:index + 1]


def load_field():
    cv2.setNumThreads(1)
    cv2.ocl.setUseOpenCL(False)
    files = {"cache": pinned(CACHE, CACHE_SHA), "contract": pinned(CONTRACT, CONTRACT_SHA),
             "norm_lut": pinned(LUT, LUT_SHA), "preprocess": pinned(PREPROCESS, PREPROCESS_SHA)}
    manifest_path = CACHE.with_name("field_identity_cache_manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["cache"]["sha256"] != CACHE_SHA or not manifest["bit_exact_lut_vs_production_preprocess"]:
        raise ValueError("Field cache provenance mismatch")
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    if contract["quality_usable_count"] != 635 or contract["preprocess"]["camera_order"] != ["left", "front", "right"]:
        raise ValueError("Field contract sample/order changed")
    lut = np.load(LUT, allow_pickle=False)
    pixels = np.arange(256, dtype=np.uint8).reshape(256, 1)
    actual_lut = cv2.addWeighted(pixels, 1 / 255., pixels, 0., -.5, dtype=cv2.CV_32F).reshape(256)
    if lut.shape != (256,) or lut.dtype != np.float32 or not np.array_equal(lut, actual_lut):
        raise ValueError("Original cv2 normalization is not bit exact to cache LUT")
    with np.load(CACHE, allow_pickle=False) as archive:
        yuv = archive["yuv"]
        ids = archive["sample"].copy()
        runs = archive["run"].copy()
        frames = archive["frame_id"].copy()
    if yuv.shape != (635, 3, 3, 66, 200) or yuv.dtype != np.uint8:
        raise ValueError("Unexpected field yuv field dtype/shape")
    samples = contract["samples"]
    if len(samples) != 635 or ids.tolist() != [s["sample_id"] for s in samples] or runs.tolist() != [s["run"] for s in samples] or frames.tolist() != [s["frame_id"] for s in samples]:
        raise ValueError("Field cache ordering differs from pinned original contract")
    images = np.ascontiguousarray(np.take(lut, yuv), dtype=np.float32)
    provenance = {"dataset": "field_635", "samples": 635, "files": files,
                  "cache_manifest_sha256": digest(manifest_path), "cache_field": "yuv",
                  "first_yuv_pixel": int(yuv[0, 0, 0, 0, 0]),
                  "normalization_256_values_bit_exact": True,
                  "normalization": "np.take(pinned float32 norm_lut, uint8 yuv); LUT equals cv2.addWeighted for every possible byte",
                  "camera_order": ["left", "front", "right"], "same_source_original_cache": True,
                  "input_tensor_sha256": hashlib.sha256(images.tobytes()).hexdigest(),
                  "ordered_samples_sha256": hashlib.sha256("\n".join(ids.tolist()).encode()).hexdigest()}
    return DataSet("field_635", images, ids, runs, provenance)


def load_payload():
    import importlib.util
    import sys
    import zipfile
    if str(PROJECT) not in sys.path:
        sys.path.insert(0, str(PROJECT))
    path = WORKSPACE / "kv260_v1/v1_payload.zip"
    cv2.setNumThreads(1)
    cv2.ocl.setUseOpenCL(False)
    before = digest(path)
    pinned(PREPROCESS, PREPROCESS_SHA)
    spec = importlib.util.spec_from_file_location("attention01_payload_helpers", PROJECT / "tools/validate_payload.py")
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Duplicate payload members")
        source = archive.read("model/model_current.onnx")
        if hashlib.sha256(source).hexdigest() != SOURCE_SHA256:
            raise ValueError("Payload source SHA changed")
        manifest = json.loads(archive.read("payload_manifest.json"))
        if manifest.get("model_sha256") != SOURCE_SHA256:
            raise ValueError("Payload manifest model SHA changed")
        frames = helpers.collect_frames(archive, set(names))
        declared = {item["segment"]: int(item["frames"]) for item in manifest["recordings"]}
        observed = {name: sum(frame["segment"] == name for frame in frames) for name in {f["segment"] for f in frames}}
        if len(frames) != 173 or declared != observed:
            raise ValueError("Payload counts differ from fixed 173 contract")
        images = np.empty((173, 3, 3, 66, 200), dtype=np.float32)
        member_hashes = {}
        for index, frame in enumerate(frames):
            decoded = []
            for role in ("left", "front", "right"):
                raw = archive.read(frame[role])
                member_hashes[frame[role]] = hashlib.sha256(raw).hexdigest()
                decoded.append(helpers._decode(frame[role], raw))
            images[index] = helpers.preprocess_arrays(*decoded)[0]
    if digest(path) != before:
        raise ValueError("Payload changed during read-only loading")
    ids = np.array([f"{frame['segment']}/{frame['frame_index']:06d}" for frame in frames])
    segments = np.array([frame["segment"] for frame in frames])
    provenance = {"dataset": "payload_173", "samples": 173, "zip": str(path),
                  "zip_sha256_before": before, "zip_sha256_after": before,
                  "zip_bytes": path.stat().st_size, "model_sha256": SOURCE_SHA256,
                  "declared_matches_observed": True, "segment_counts": observed,
                  "camera_order": ["left", "front", "right"],
                  "preprocess_sha256": PREPROCESS_SHA,
                  "preprocess": "unchanged ps.preprocess.preprocess_arrays; ZIP JPEG cv2.imdecode",
                  "ordering": "tools.validate_payload.collect_frames: sorted csv paths, original row order",
                  "image_member_sha256": member_hashes,
                  "input_tensor_sha256": hashlib.sha256(images.tobytes()).hexdigest(),
                  "ordered_samples_sha256": hashlib.sha256("\n".join(ids.tolist()).encode()).hexdigest()}
    return DataSet("payload_173", np.ascontiguousarray(images), ids, segments, provenance)
