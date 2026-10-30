from pathlib import Path
import json
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import control_v2_replay as replay
from test_control_v2_policy import config
from dataclasses import asdict


def test_explicit_fixture_replay_outputs_audit_and_refuses_overwrite(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps(asdict(config())), encoding="utf-8")
    output = tmp_path / "fixture"
    assert replay.main(["--config", str(cfg), "--fixture", "--out-dir", str(output)]) == 0
    result = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in (output / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
    assert result["status"] == "已验证"
    assert result["fixture"] is True and "not real YOLO" in result["scope"]
    assert all(row["write_result"] == "not_sent_decision_replay" for row in rows)
    assert any(row["decision"]["reason"] == "junction_route_unavailable" for row in rows)
    assert any(row["decision"]["reason"] == "junction_conflict" for row in rows)
    assert any(row["signs"]["reused"] for row in rows)
    before = (output / "summary.json").read_bytes()
    assert replay.main(["--config", str(cfg), "--fixture", "--out-dir", str(output)]) == 1
    assert (output / "summary.json").read_bytes() == before


def test_saved_record_decision_replay_labels_reconstructed_driver_completion(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps(asdict(config())), encoding="utf-8")
    source = tmp_path / "saved.jsonl"
    record = {"sample_id": "run_a/1", "run": "run_a", "frame_id": 1,
              "grabbed": [10., 10.01, 10.02], "driver": [200., 100.],
              "roadsign": {"grabbed_t_mono": 10.01, "replay_completed_t_mono": 10.05,
                           "detections": []}, "timing": {"replay_decision_t_mono": 10.06}}
    source.write_text(json.dumps(record) + "\n", encoding="utf-8")
    output = tmp_path / "saved"
    assert replay.main(["--config", str(cfg), "--input", str(source), "--out-dir", str(output)]) == 0
    result = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert result["input_sha256"] and "reconstructed" in result["timing_note"]
    row = json.loads((output / "audit.jsonl").read_text(encoding="utf-8").strip())
    assert row["driver"]["age"] == pytest.approx(.06)
    assert row["signs"]["age"] == pytest.approx(.05)
    assert row["decision"]["reason"] == "driver"


def test_replay_does_not_expose_device_or_model_execution_flags():
    parser = replay.build_parser()
    for flag in ("--camera", "--serial", "--pl", "--model", "--device"):
        with pytest.raises(SystemExit):
            parser.parse_args(["--config", "unused", "--fixture", "--out-dir", "unused", flag, "x"])
