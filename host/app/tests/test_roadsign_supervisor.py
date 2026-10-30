from pathlib import Path
import hashlib
import math
import sys
import time

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ps.roadsign import CLASS_NAMES, RoadSignFrame, RoadSignONNX, RoadSignResult
from ps.roadsign_supervisor import RoadSignStale, RoadSignSupervisor


class Detector:
    def __init__(self, frame):
        self.frame = frame
        self.calls = []

    def detect(self, image, *, grabbed):
        self.calls.append((image, grabbed))
        return self.frame


def test_supervisor_rejects_stale_control_but_keeps_current_detection_for_audit():
    result = RoadSignResult((), {name: name == "ROADSIGN_pedestrian" for name in CLASS_NAMES})
    frame = RoadSignFrame(result, grabbed=1.0, completed=1.1,
                          latency_ms=100.0, model_sha256="a" * 64)
    supervisor = RoadSignSupervisor(Detector(frame), max_result_age_s=0.2,
                                    clock=lambda: 2.0)

    with pytest.raises(RoadSignStale, match="路标结果过期"):
        supervisor.evaluate((("L", 1.9), ("F", 1.0), ("R", 1.9)), 200.0, 180.0)

    assert supervisor.last_frame is frame
    assert supervisor.last_frame.latency_ms == 100.0
    assert supervisor.last_frame.result.params["ROADSIGN_pedestrian"] is True


def test_supervisor_uses_front_frame_and_fresh_detection_for_driver_decision():
    result = RoadSignResult((), {name: False for name in CLASS_NAMES})
    detector = Detector(RoadSignFrame(result, grabbed=1.9, completed=1.95,
                                      latency_ms=50.0, model_sha256="b" * 64))
    supervisor = RoadSignSupervisor(detector, max_result_age_s=0.2,
                                    clock=lambda: 2.0)

    decision = supervisor.evaluate((("L", 1.85), ("F", 1.9), ("R", 1.86)), 200.0, 180.0)

    assert detector.calls == [("F", 1.9)]
    assert decision.stop is False and decision.left == 200.0 and decision.right == 180.0
    assert supervisor.last_frame.model_sha256 == "b" * 64
    assert supervisor.last_frame.latency_ms == 50.0


@pytest.mark.parametrize("first_success", [False, True])
def test_supervisor_detector_failure_leaves_no_previous_audit_frame(first_success):
    result = RoadSignResult((), {name: False for name in CLASS_NAMES})
    frame = RoadSignFrame(result, grabbed=1.9, completed=1.95,
                          latency_ms=50.0, model_sha256="c" * 64)

    class FailingDetector(Detector):
        def detect(self, image, *, grabbed):
            if first_success and not self.calls:
                return super().detect(image, grabbed=grabbed)
            raise RuntimeError("detector fixture failure")

    supervisor = RoadSignSupervisor(FailingDetector(frame), max_result_age_s=0.2,
                                    clock=lambda: 2.0)
    frames = (("L", 1.9), ("F", 1.9), ("R", 1.9))
    if first_success:
        assert supervisor.evaluate(frames, 200.0, 180.0).reason == "driver"
        assert supervisor.last_frame is frame

    with pytest.raises(RuntimeError, match="detector fixture failure"):
        supervisor.evaluate(frames, 200.0, 180.0)

    assert supervisor.last_frame is None


@pytest.mark.parametrize("grabbed", [2.1, float("nan"), float("inf")])
def test_supervisor_rejects_future_or_invalid_result_time_but_keeps_current_audit(grabbed):
    result = RoadSignResult((), {name: name == "ROADSIGN_pedestrian" for name in CLASS_NAMES})
    frame = RoadSignFrame(result, grabbed=grabbed, completed=2.0,
                          latency_ms=10.0, model_sha256="d" * 64)
    supervisor = RoadSignSupervisor(Detector(frame), max_result_age_s=0.2,
                                    clock=lambda: 2.0)

    with pytest.raises(RoadSignStale):
        supervisor.evaluate((("L", 1.9), ("F", grabbed), ("R", 1.9)), 200.0, 180.0)

    assert supervisor.last_frame is frame


@pytest.fixture
def tiny_roadsign_detector(tmp_path):
    """New synthetic fixture under pytest tmp; never edits a production model."""
    import onnx
    from onnx import TensorProto, helper, numpy_helper

    output = np.zeros((1, 15, 17), dtype=np.float32)
    output[0, 0:4, 0] = [4, 4, 4, 4]
    output[0, 4 + 9, 0] = 0.9
    graph = helper.make_graph([
        helper.make_node("Constant", [], ["output"], value=numpy_helper.from_array(output)),
    ], "synthetic_roadsign_fixture", [helper.make_tensor_value_info("images", TensorProto.FLOAT, [1, 3, 8, 8])],
       [helper.make_tensor_value_info("output", TensorProto.FLOAT, [1, 15, 17])])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    onnx.checker.check_model(model)
    path = tmp_path / "tiny_roadsign_fixture.onnx"
    onnx.save_model(model, str(path))
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    clock = lambda: 100.1
    detector = RoadSignONNX(path, expected_sha256=sha, threads=1, clock=clock)
    return detector, clock


def test_supervisor_runs_real_cpu_onnx_fixture_and_audits_current_detection(tiny_roadsign_detector):
    detector, _ = tiny_roadsign_detector
    from tools.roadsign_replay import ReplayClock
    clock = ReplayClock()
    clock.set_frames([100.0] * 3, t_mono=100.1)
    detector.clock = clock
    supervisor = RoadSignSupervisor(detector, max_result_age_s=0.2, clock=clock)
    image = np.full((480, 640, 3), [10, 20, 30], dtype=np.uint8)
    started = time.perf_counter()

    decision = supervisor.evaluate([(image, 100.0)] * 3, 200.0, 180.0)

    elapsed_ms = (time.perf_counter() - started) * 1000
    assert decision.reason == "pedestrian" and decision.stop is True
    assert detector.session.get_providers() == ["CPUExecutionProvider"]
    assert supervisor.last_frame.result.params["ROADSIGN_pedestrian"] is True
    assert supervisor.last_frame.result.detections[0].class_id == 9
    assert supervisor.last_frame.model_sha256 == detector.sha256
    assert math.isfinite(supervisor.last_frame.latency_ms)
    assert 0.0 < supervisor.last_frame.latency_ms <= elapsed_ms


def test_supervisor_stale_real_cpu_onnx_result_is_audit_only(tiny_roadsign_detector):
    detector, clock = tiny_roadsign_detector
    supervisor = RoadSignSupervisor(detector, max_result_age_s=0.2, clock=clock)
    image = np.zeros((480, 640, 3), dtype=np.uint8)

    with pytest.raises(RoadSignStale):
        supervisor.evaluate([(image, 99.0)] * 3, 200.0, 180.0)

    assert supervisor.last_frame.grabbed == 99.0
    assert supervisor.last_frame.result.params["ROADSIGN_pedestrian"] is True


def test_supervisor_stale_audit_replaces_previous_successful_detection():
    first = RoadSignResult((), {name: name == "ROADSIGN_pedestrian" for name in CLASS_NAMES})
    current = RoadSignResult((), {name: name == "ROADSIGN_red_light" for name in CLASS_NAMES})
    detector = Detector(RoadSignFrame(first, grabbed=1.9, completed=1.95,
                                      latency_ms=50.0, model_sha256="e" * 64))
    supervisor = RoadSignSupervisor(detector, max_result_age_s=0.2, clock=lambda: 2.0)
    assert supervisor.evaluate((("L", 1.9), ("F", 1.9), ("R", 1.9)), 200.0, 180.0).reason == "pedestrian"
    detector.frame = RoadSignFrame(current, grabbed=1.0, completed=2.0,
                                    latency_ms=250.0, model_sha256="f" * 64)

    with pytest.raises(RoadSignStale):
        supervisor.evaluate((("L", 1.0), ("F", 1.0), ("R", 1.0)), 200.0, 180.0)

    assert supervisor.last_frame is detector.frame
    assert supervisor.last_frame.latency_ms == 250.0
    assert supervisor.last_frame.result.params["ROADSIGN_red_light"] is True
    assert supervisor.last_frame.result.params["ROADSIGN_pedestrian"] is False
