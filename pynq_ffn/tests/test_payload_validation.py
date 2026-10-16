from pathlib import Path
import io
import json
import sys
import warnings
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.validate_payload import confined_member, collect_frames, validate, MODEL_SHA256


@pytest.mark.parametrize("raw", ["../secret.jpeg", "..\\..\\x.jpeg", "a/../../b.jpeg",
                                  "/etc/passwd", "C:/x.jpeg", "", "//server/x"])
def test_payload_paths_cannot_escape_recording(raw):
    with pytest.raises(ValueError):
        confined_member("data/segment/auto_data.csv", raw)


def test_payload_csv_camera_order():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("data/segment/auto_data.csv", "./front/1.jpeg,./left/1.jpeg,./right/1.jpeg,200,250\n")
        for camera in ("front", "left", "right"):
            archive.writestr(f"data/segment/{camera}/1.jpeg", b"placeholder")
    buffer.seek(0)
    with zipfile.ZipFile(buffer) as archive:
        frames = collect_frames(archive, set(archive.namelist()))
    assert len(frames) == 1
    assert frames[0]["front"] == "data/segment/front/1.jpeg"
    assert frames[0]["left"] == "data/segment/left/1.jpeg"
    assert frames[0]["recorded_left"] == 200


def make_payload(path, *, declared_count=1, manifest_sha=MODEL_SHA256, duplicate=False):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("payload_manifest.json", json.dumps({
            "version": "test", "model_sha256": manifest_sha,
            "recordings": [{"segment": "segment", "frames": declared_count}]}))
        archive.writestr("model/model_current.onnx", b"not-the-model")
        archive.writestr("data/segment/auto_data.csv", "front/1.jpeg,left/1.jpeg,right/1.jpeg,200,250\n")
        for camera in ("front", "left", "right"):
            archive.writestr(f"data/segment/{camera}/1.jpeg", b"placeholder")
        if duplicate:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                archive.writestr("data/segment/front/1.jpeg", b"duplicate")


@pytest.mark.parametrize("case,expected", [("count", "counts"), ("manifest", "manifest names"),
                                            ("model", "differs"), ("duplicate", "Duplicate")])
def test_payload_integrity_rejected_before_inference(tmp_path, case, expected):
    payload = tmp_path / "payload.zip"
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "model.onnx").write_bytes(b"not-the-model")
    make_payload(payload, declared_count=2 if case == "count" else 1,
                 manifest_sha="wrong" if case == "manifest" else MODEL_SHA256,
                 duplicate=case == "duplicate")
    with pytest.raises(ValueError, match=expected):
        validate(payload, bundle)


def test_missing_payload_manifest_and_model_fail_closed(tmp_path):
    payload = tmp_path / "payload.zip"
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("unrelated.txt", "nothing")
    with pytest.raises(ValueError, match="original payload manifest"):
        validate(payload, tmp_path)
