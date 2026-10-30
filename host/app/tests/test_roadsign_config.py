from pathlib import Path
import hashlib
import json
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ps.roadsign_supervisor import load_roadsign_config


def test_roadsign_config_resolves_model_and_required_limits(tmp_path):
    model = tmp_path / "roadsign.onnx"
    model.write_bytes(b"fixture")
    sha = hashlib.sha256(model.read_bytes()).hexdigest()
    path = tmp_path / "roadsign.json"
    path.write_text(json.dumps({"schema": "roadsign_supervisor_config_v1",
                                "model": "roadsign.onnx", "sha256": sha,
                                "max_result_age_s": 0.25, "threads": 2}), encoding="utf-8")

    config = load_roadsign_config(path)

    assert config.model_path == model
    assert config.sha256 == sha
    assert config.max_result_age_s == 0.25
    assert config.threads == 2


def test_roadsign_config_rejects_wrong_model_hash(tmp_path):
    model = tmp_path / "roadsign.onnx"
    model.write_bytes(b"fixture")
    path = tmp_path / "roadsign.json"
    path.write_text(json.dumps({"model": "roadsign.onnx", "sha256": "0" * 64,
                                "max_result_age_s": 0.25}), encoding="utf-8")

    with pytest.raises(ValueError, match="SHA256"):
        load_roadsign_config(path)


def test_roadsign_config_rejects_boolean_age(tmp_path):
    model = tmp_path / "roadsign.onnx"
    model.write_bytes(b"fixture")
    sha = hashlib.sha256(model.read_bytes()).hexdigest()
    path = tmp_path / "roadsign.json"
    path.write_text(json.dumps({"model": "roadsign.onnx", "sha256": sha,
                                "max_result_age_s": True}), encoding="utf-8")

    with pytest.raises(ValueError, match="max_result_age_s"):
        load_roadsign_config(path)
