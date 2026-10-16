from pathlib import Path
import sys
import numpy as np
import pytest
import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ps.preprocess import preprocess_arrays, read_records, checked_image_path, legacy_packet_preview


def test_camera_order_crop_and_dtype():
    images = [np.full((480, 640, 3), value, dtype=np.uint8) for value in (30, 90, 180)]
    result = preprocess_arrays(*images)
    assert result.shape == (1, 3, 3, 66, 200)
    assert result.dtype == np.float32 and result.flags.c_contiguous
    np.testing.assert_allclose(result[0, :, 0, 0, 0], np.asarray([30, 90, 180])/255-.5, atol=1e-7, rtol=0)
    np.testing.assert_allclose(result[0, :, 1:, 0, 0], 128/255-.5, atol=1e-7, rtol=0)
    altered = [x.copy() for x in images]
    for x in altered:
        x[:150] = 255
        x[479] = 255
    np.testing.assert_array_equal(result, preprocess_arrays(*altered))


def test_colored_pixels_reference_and_rejections():
    rng = np.random.default_rng(5)
    images = [rng.integers(0, 256, (480, 640, 3), dtype=np.uint8) for _ in range(3)]
    reference = []
    for x in images:
        yuv = cv2.cvtColor(cv2.resize(x[150:479], (200, 66), interpolation=cv2.INTER_LINEAR), cv2.COLOR_BGR2YUV)
        reference.append(cv2.addWeighted(yuv, 1/255., yuv, 0, -.5, dtype=cv2.CV_32F).transpose(2, 0, 1))
    np.testing.assert_array_equal(preprocess_arrays(*images), np.stack(reference)[None])
    for wrong in (None, np.zeros((66, 200, 3), dtype=np.uint8), np.zeros((480, 640, 3), dtype=np.float32)):
        with pytest.raises(ValueError):
            preprocess_arrays(wrong, images[1], images[2])


def test_records_and_preview(tmp_path):
    for camera in ("front", "left", "right"):
        (tmp_path / camera).mkdir()
        (tmp_path / camera / "1.jpeg").write_bytes(b"placeholder")
    csv = tmp_path / "auto_data.csv"
    csv.write_text("./front/1.jpeg,./left/1.jpeg,./right/1.jpeg,200,250,3\n", encoding="utf8")
    record = read_records([tmp_path])[0]
    assert record.left.parent.name == "left" and record.front.parent.name == "front"
    with pytest.raises(ValueError):
        checked_image_path(tmp_path, "../outside.jpeg")
    with pytest.raises(ValueError):
        read_records([tmp_path, tmp_path])
    with pytest.raises(ValueError):
        read_records([tmp_path], 0)
    assert bytes.fromhex(legacy_packet_preview(200.9, 250.9)) == b"%@12001200125012501\xff\xff"
    assert legacy_packet_preview(-1, 250) == ""
    assert legacy_packet_preview(float("nan"), 250) == ""
    csv.write_text("front/1.jpeg,left/1.jpeg,right/1.jpeg,nan,250\n")
    with pytest.raises(ValueError):
        read_records([tmp_path])
