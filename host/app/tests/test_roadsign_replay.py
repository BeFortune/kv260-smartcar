from pathlib import Path
import hashlib
import json
import sys
from types import SimpleNamespace

import cv2
import numpy as np
import onnxruntime as ort
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ps.control_arbiter import RuleArbiter
from tools import roadsign_replay as replay
from tools.field_replay import read_triplet
from tools.roadsign_replay import ReplayClock


class PerfClock:
    def __init__(self):
        self.value = 10.0

    def __call__(self):
        return self.value


def detection_output(class_id=None):
    output = np.zeros((1, 15, 17), dtype=np.float32)
    if class_id is not None:
        output[0, 0:4, 0] = [4, 4, 4, 4]
        output[0, 4 + class_id, 0] = 0.9
    return output


@pytest.fixture
def replay_case(tmp_path, monkeypatch):
    """Pinned PNG + real detector/preprocessing/arbiter, injected CPU sessions only."""
    perf = PerfClock()
    monkeypatch.setattr(replay.time, "perf_counter", perf)
    workspace = tmp_path / "workspace"
    old_path = workspace / "ros_craic_car/src/base_control/resource/model_current.onnx"
    old_path.parent.mkdir(parents=True)
    old_path.write_bytes(b"fake driver session identity")
    monkeypatch.setattr(replay, "WORKSPACE", workspace)

    class FakeDriver:
        def __init__(self, path, threads):
            self.last_ms = {}

        def __call__(self, *images):
            assert len(images) == 3
            perf.value += 0.01
            self.last_ms = {"total": 10.0}
            return 200.0, 180.0

    monkeypatch.setattr(replay, "CpuOriginalInfer", FakeDriver)

    def make_case(actions, *, detector_elapsed=0.02, capture_age=0.02, runs=None):
        actions = iter(actions)

        class FakeSession:
            calls = 0

            def __init__(self, path, sess_options, providers):
                self.providers = providers

            def get_inputs(self):
                return [SimpleNamespace(name="images", shape=[1, 3, 8, 8], type="tensor(float)")]

            def get_outputs(self):
                return [SimpleNamespace(name="output", shape=[1, 15, 17])]

            def get_providers(self):
                return self.providers

            def run(self, names, feed):
                FakeSession.calls += 1
                assert feed["images"].shape == (1, 3, 8, 8)
                perf.value += detector_elapsed
                action = next(actions)
                if isinstance(action, Exception):
                    raise action
                return [action]

        monkeypatch.setattr(ort, "InferenceSession", FakeSession)
        images_dir = tmp_path / "images"
        images_dir.mkdir()
        for role, value in zip(("left", "front", "right"), (10, 20, 30)):
            assert cv2.imwrite(str(images_dir / f"{role}.png"),
                               np.full((480, 640, 3), value, dtype=np.uint8))
        _, pins = read_triplet(images_dir)
        samples = []
        for index, run_name in enumerate(runs or ["run_a"]):
            frame_dir = tmp_path / f"frame_{index + 1:06d}"
            frame_dir.mkdir()
            selected_at = 100.0 + index
            metadata = {"t_mono": selected_at,
                        **{f"grabbed_{role}_mono": selected_at - capture_age
                           for role in ("left", "front", "right")}}
            path = frame_dir / "metadata.json"
            path.write_text(json.dumps({"metadata": metadata}), encoding="utf-8")
            samples.append({"sample_id": f"{run_name}/{index + 1}", "run": run_name,
                            "frame_id": index + 1, "frame_dir": str(frame_dir),
                            "metadata_sha256": replay.sha256_file(path), "images": pins,
                            "recorded_old": {"left": 200.0, "right": 180.0}})
        contract = tmp_path / "contract.json"
        contract.write_text(json.dumps({"samples": samples}), encoding="utf-8")
        model = tmp_path / "fake_session.bin"
        model.write_bytes(b"offline fake session fixture; not a model")
        out = tmp_path / "output"
        out.mkdir()
        args = SimpleNamespace(contract=contract, roadsign_model=model,
                               roadsign_sha256=hashlib.sha256(model.read_bytes()).hexdigest(),
                               driver_threads=1, roadsign_threads=1, max_result_age_s=0.2,
                               limit=None, out_dir=out)
        return SimpleNamespace(args=args, session=FakeSession, perf=perf)

    return make_case


def replay_results(case):
    rows = [json.loads(line) for line in
            (case.args.out_dir / "predictions.jsonl").read_text(encoding="utf-8").splitlines()]
    result = json.loads((case.args.out_dir / "summary.json").read_text(encoding="utf-8"))
    return rows, result


def test_replay_clock_preserves_capture_age_plus_perf_counter_elapsed(monkeypatch):
    perf = PerfClock()
    monkeypatch.setattr(replay.time, "perf_counter", perf)
    monkeypatch.setattr(replay.time, "monotonic", lambda: 9999.0)
    clock = ReplayClock()

    clock.set_frames([100.0, 100.002, 100.001], t_mono=100.1)
    perf.value += 0.025

    assert clock() == pytest.approx(100.125)
    assert clock() - 100.002 == pytest.approx(0.123)


def test_replay_first_stale_frame_is_logged_as_failure_with_current_detection(replay_case):
    case = replay_case([detection_output(9)], detector_elapsed=0.3)

    assert replay.run(case.args) == 1

    rows, result = replay_results(case)
    assert len(rows) == 1
    assert rows[0]["decision"]["reason"] == "supervisor_stale"
    assert rows[0]["decision"]["stop"] is True
    assert rows[0]["roadsign"]["params"]["ROADSIGN_pedestrian"] is True
    assert rows[0]["roadsign"]["latency_ms"] == pytest.approx(300.0)
    assert rows[0]["roadsign"]["detections"][0]["class_id"] == 9
    assert result["status"] == "失败"
    assert result["stale_count"] == 1
    assert result["errors"] == []


def test_replay_valid_frame_separates_recorded_capture_from_current_cpu_timing(replay_case):
    case = replay_case([detection_output()], capture_age=0.08)

    assert replay.run(case.args) == 0

    rows, result = replay_results(case)
    row = rows[0]
    assert row["decision"]["reason"] == "driver"
    assert row["decision"]["left"] == 200.0 and row["decision"]["right"] == 180.0
    assert row["timing"]["recorded_capture_t_mono"] == 100.0
    assert row["timing"]["recorded_decision_t_mono"] is None
    assert row["timing"]["capture_age_ms"] == pytest.approx([80.0] * 3)
    assert row["timing"]["current_cpu_elapsed_ms"] == pytest.approx(30.0)
    assert row["timing"]["replay_decision_t_mono"] == pytest.approx(100.03)
    assert row["roadsign"]["age_ms"] == pytest.approx(110.0)
    assert row["roadsign"]["latency_ms"] == pytest.approx(20.0)
    assert result["status"] == "已验证"
    assert "perf_counter" in result["timing_note"]


@pytest.mark.parametrize("preceded_by_success", [False, True])
def test_replay_detector_failure_never_reuses_previous_result(replay_case, preceded_by_success):
    actions = ([detection_output(9)] if preceded_by_success else []) + [RuntimeError("detector fixture failure")]
    case = replay_case(actions, runs=["run_a"] * len(actions))

    assert replay.run(case.args) == 1

    rows, result = replay_results(case)
    failure = rows[-1]
    assert failure["decision"]["reason"] == "supervisor_error"
    assert failure["decision"]["left"] == failure["decision"]["right"] == 0.0
    assert failure["decision"]["stop"] is True
    assert "roadsign" not in failure
    assert result["status"] == "失败" and result["stale_count"] == 0
    assert len(result["errors"]) == 1
    assert result["errors"][0]["sample_id"] == failure["sample_id"]
    assert result["errors"][0]["type"] == "RoadSignError"
    if preceded_by_success:
        assert rows[0]["decision"]["reason"] == "pedestrian"
        assert rows[0]["roadsign"]["params"]["ROADSIGN_pedestrian"] is True


def test_replay_historical_capture_age_can_fail_fast_current_detection(replay_case):
    case = replay_case([detection_output(6)], capture_age=0.3)

    assert replay.run(case.args) == 1

    rows, result = replay_results(case)
    assert rows[0]["decision"]["reason"] == "supervisor_stale"
    assert rows[0]["roadsign"]["latency_ms"] == pytest.approx(20.0)
    assert rows[0]["roadsign"]["age_ms"] == pytest.approx(330.0)
    assert rows[0]["roadsign"]["params"]["ROADSIGN_red_light"] is True
    assert result["stale_count"] == 1


@pytest.mark.parametrize("grabbed,t_mono", [
    ([100.0, 100.01, 100.02], None),
    ([100.0, 100.01, 100.02], float("nan")),
    ([100.0, 100.01, 100.02], float("inf")),
    ([100.0, 100.01, 100.02], True),
    ([100.0, 100.01, 100.02], "100.1"),
    ([100.0, 100.01, 100.02], 100.01),
    ([100.0, float("nan"), 100.02], 100.1),
    ([100.0, 100.01], 100.1),
    ([100.0, True, 100.02], 100.1),
])
def test_replay_clock_rejects_missing_invalid_or_future_timestamps(grabbed, t_mono):
    with pytest.raises(ValueError):
        ReplayClock().set_frames(grabbed, t_mono=t_mono)


@pytest.mark.parametrize("field,value", [
    ("t_mono", None),
    ("t_mono", float("nan")),
    ("t_mono", 99.0),
    ("grabbed_front_mono", True),
    ("grabbed_front_mono", "99.98"),
])
def test_replay_records_invalid_metadata_as_failure_before_running_inference(replay_case, field, value):
    case = replay_case([detection_output()])
    contract = json.loads(case.args.contract.read_text(encoding="utf-8"))
    sample = contract["samples"][0]
    path = Path(sample["frame_dir"]) / "metadata.json"
    metadata = json.loads(path.read_text(encoding="utf-8"))
    if value is None:
        metadata["metadata"].pop(field)
    else:
        metadata["metadata"][field] = value
    path.write_text(json.dumps(metadata), encoding="utf-8")
    sample["metadata_sha256"] = replay.sha256_file(path)
    case.args.contract.write_text(json.dumps(contract), encoding="utf-8")
    output = case.args.out_dir.parent / "rejected"

    exit_code = replay.main([
        "--contract", str(case.args.contract), "--roadsign-model", str(case.args.roadsign_model),
        "--roadsign-sha256", case.args.roadsign_sha256, "--max-result-age-s", "0.2",
        "--out-dir", str(output),
    ])

    assert exit_code == 1
    failure = json.loads((output / "failure.json").read_text(encoding="utf-8"))
    assert failure["status"] == "失败" and failure["type"] == "ValueError"
    assert case.session.calls == 0
    assert not (output / "summary.json").exists()


def test_replay_without_direction_injection_does_not_claim_finish_coverage(replay_case):
    case = replay_case([detection_output(8), detection_output(0)], runs=["run_a", "run_a"])

    assert replay.run(case.args) == 0

    rows, result = replay_results(case)
    assert all(row["decision"]["reason"] != "finish" for row in rows)
    assert result["finish"]["coverage"] == "not exercised"
    assert "set_replay_direction" in result["finish"]["scope"]


def test_replay_finish_halts_without_reset_or_cross_run_restart(replay_case, monkeypatch):
    case = replay_case([detection_output(8), detection_output(0), detection_output(),
                        detection_output(), RuntimeError("must not reach next run")],
                       runs=["run_a"] * 4 + ["run_b"])
    instances = []

    class DirectedSupervisor(replay.RoadSignSupervisor):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.reset_count = 0
            instances.append(self)

        def reset(self):
            super().reset()
            self.reset_count += 1
            self.arbiter.set_replay_direction("left")

    monkeypatch.setattr(replay, "RoadSignSupervisor", DirectedSupervisor)

    assert replay.run(case.args) == 0

    rows, result = replay_results(case)
    assert len(rows) == 4 and case.session.calls == 4
    assert rows[-1]["decision"]["reason"] == "finish"
    assert rows[-1]["decision"]["stop"] is True
    assert rows[-1]["decision"]["enter_wait_start"] is True
    assert instances[0].reset_count == 1
    assert instances[0].arbiter.is_warning is True
    assert result["finish"]["coverage"] == "exercised"
    assert result["finish"]["terminal_sample"] == rows[-1]["sample_id"]
    assert result["finish"]["halt_reason"] == "enter_wait_start"
    assert result["requested_samples"] == 5 and result["unprocessed_samples"] == 1


@pytest.mark.parametrize("preceded_by_success", [False, True])
def test_replay_arbiter_exception_does_not_reuse_another_frames_decision(
        replay_case, monkeypatch, preceded_by_success):
    count = 2 if preceded_by_success else 1
    case = replay_case([detection_output()] * count, runs=["run_a"] * count)
    supervisor_type = replay.RoadSignSupervisor

    def supervisor_with_fault(*args, **kwargs):
        class FailingArbiter(RuleArbiter):
            def decide(self, *args, **kwargs):
                self.calls = getattr(self, "calls", 0) + 1
                if self.calls == count:
                    raise RuntimeError("arbiter fixture failure")
                return super().decide(*args, **kwargs)

        return supervisor_type(*args, arbiter=FailingArbiter(), **kwargs)

    monkeypatch.setattr(replay, "RoadSignSupervisor", supervisor_with_fault)

    assert replay.run(case.args) == 1

    rows, result = replay_results(case)
    assert rows[-1]["decision"]["reason"] == "supervisor_error"
    assert rows[-1]["decision"]["stop"] is True
    assert rows[-1]["decision"]["left"] == rows[-1]["decision"]["right"] == 0.0
    assert rows[-1]["roadsign"]["latency_ms"] == pytest.approx(20.0)
    assert len(result["errors"]) == 1 and result["errors"][0]["type"] == "RuntimeError"
