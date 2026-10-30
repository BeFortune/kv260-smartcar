from pathlib import Path
import hashlib
import sys

import numpy as np
import onnxruntime as ort
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ps.roadsign import RoadSignError, RoadSignONNX, parse_roadsign_output, preprocess_roadsign


def test_yolov5_pedestrian_detection_sets_parameter():
    output = np.zeros((1, 17, 16), dtype=np.float32)
    output[0, 0, :5] = [320.0, 240.0, 100.0, 120.0, 0.8]
    output[0, 0, 5 + 9] = 0.9

    result = parse_roadsign_output(output, input_width=640, input_height=640,
                                   image_shape=(480, 640, 3))

    assert result.params["ROADSIGN_pedestrian"] is True
    assert len(result.detections) == 1
    detection = result.detections[0]
    assert detection.class_name == "ROADSIGN_pedestrian"
    assert detection.confidence == np.float32(0.8)
    assert detection.width == 100.0
    assert detection.height == 90.0


def test_yolov8_output_without_objectness_sets_red_light():
    output = np.zeros((1, 15, 17), dtype=np.float32)
    for anchor in range(2):
        output[0, 0, anchor] = 320.0
        output[0, 1, anchor] = 240.0
        output[0, 2, anchor] = 80.0
        output[0, 3, anchor] = 80.0
        output[0, 4 + 6, anchor] = 0.8

    result = parse_roadsign_output(output, input_width=640, input_height=640,
                                   image_shape=(480, 640, 3))

    assert result.params["ROADSIGN_red_light"] is True
    assert len(result.detections) == 2
    assert result.detections[0].confidence == result.detections[1].confidence
    assert result.detections[0].class_name == "ROADSIGN_red_light"


def test_preprocess_resizes_bgr_to_rgb_nchw_without_letterbox():
    image = np.full((480, 640, 3), [10, 20, 30], dtype=np.uint8)

    tensor = preprocess_roadsign(image, input_width=4, input_height=2)

    assert tensor.shape == (1, 3, 2, 4)
    assert tensor.dtype == np.float32
    np.testing.assert_allclose(tensor[0, 0], 30 / 255)
    np.testing.assert_allclose(tensor[0, 1], 20 / 255)
    np.testing.assert_allclose(tensor[0, 2], 10 / 255)


def test_onnx_detector_checks_hash_and_runs_cpu_contract(tmp_path, monkeypatch):
    model_path = tmp_path / "roadsign.onnx"
    model_path.write_bytes(b"offline fake model")
    model_sha = hashlib.sha256(model_path.read_bytes()).hexdigest()

    class Value:
        def __init__(self, name, shape, value_type="tensor(float)"):
            self.name, self.shape, self.type = name, shape, value_type

    class FakeSession:
        instances = []

        def __init__(self, path, sess_options, providers):
            self.path, self.options, self.providers = path, sess_options, providers
            self.feed = None
            FakeSession.instances.append(self)

        def get_inputs(self):
            return [Value("images", [1, 3, 2, 4])]

        def get_outputs(self):
            return [Value("output", [1, 17, 16])]

        def get_providers(self):
            return self.providers

        def run(self, names, feed):
            self.feed = feed
            output = np.zeros((1, 17, 16), dtype=np.float32)
            output[0, 0, :5] = [320.0, 240.0, 100.0, 120.0, 0.8]
            output[0, 0, 5 + 9] = 0.9
            return [output]

    monkeypatch.setattr(ort, "InferenceSession", FakeSession)
    detector = RoadSignONNX(model_path, expected_sha256=model_sha, threads=1)

    frame = detector.detect(np.full((480, 640, 3), 20, dtype=np.uint8), grabbed=12.0)

    assert detector.sha256 == model_sha
    assert frame.grabbed == 12.0
    assert frame.result.params["ROADSIGN_pedestrian"] is True
    session = FakeSession.instances[0]
    assert session.providers == ["CPUExecutionProvider"]
    assert session.options.intra_op_num_threads == 1
    assert session.options.inter_op_num_threads == 1
    assert session.feed["images"].shape == (1, 3, 2, 4)


def test_onnx_inference_error_is_not_treated_as_no_detection(tmp_path, monkeypatch):
    model_path = tmp_path / "roadsign.onnx"
    model_path.write_bytes(b"offline fake model")
    model_sha = hashlib.sha256(model_path.read_bytes()).hexdigest()

    class Value:
        def __init__(self, name, shape, value_type="tensor(float)"):
            self.name, self.shape, self.type = name, shape, value_type

    class BrokenSession:
        def __init__(self, path, sess_options, providers):
            self.providers = providers

        def get_inputs(self):
            return [Value("images", [1, 3, 2, 4])]

        def get_outputs(self):
            return [Value("output", [1, 17, 16])]

        def get_providers(self):
            return self.providers

        def run(self, names, feed):
            raise RuntimeError("synthetic inference failure")

    monkeypatch.setattr(ort, "InferenceSession", BrokenSession)
    detector = RoadSignONNX(model_path, expected_sha256=model_sha)
    with pytest.raises(RoadSignError, match="路标推理失败"):
        detector.detect(np.zeros((480, 640, 3), dtype=np.uint8), grabbed=0.0)


def test_nonfinite_output_is_rejected_instead_of_becoming_empty_detection():
    output = np.full((1, 17, 16), np.nan, dtype=np.float32)

    with pytest.raises(ValueError, match="非有限"):
        parse_roadsign_output(output, input_width=640, input_height=640,
                              image_shape=(480, 640, 3))


@pytest.mark.parametrize("image_shape", [
    (0, 640, 3),      # 图高 0
    (480, 0, 3),      # 图宽 0
    (-1, 640, 3),     # 图高负
    (480, -1, 3),     # 图宽负
    (480.0, 640, 3),  # 非整数尺寸
])
def test_zero_or_negative_image_size_is_rejected(image_shape):
    """上游 cv::resize 不接受 0/负尺寸；公开解析入口也必须 fail-closed。"""
    output = np.zeros((1, 15, 64), dtype=np.float32)

    with pytest.raises(ValueError, match="图像尺寸必须是正整数"):
        parse_roadsign_output(output, input_width=640, input_height=640,
                              image_shape=image_shape)
