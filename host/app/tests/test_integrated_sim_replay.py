from pathlib import Path
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ps.control_v2 import ControlPolicy, PolicyConfig
from ps.packet import encode_side_speed, stop_frame
from ps.roadsign import CLASS_NAMES, Detection, RoadSignFrame, RoadSignResult
from tools.integrated_sim_replay import process_sample
from tools.roadsign_replay import ReplayClock


CONFIG = PolicyConfig(
    max_driver_age_s=.5,
    max_sign_age_s=.5,
    confirm_frames=2,
    pedestrian_clear_frames=3,
    crosswalk_clear_frames=3,
    crosswalk_hold_s=3.,
    speed_limit_factor=.75,
    command_ttl_s=.2,
    confirmation_max_gap_s=.5,
)


class FakeDriver:
    def __init__(self, values):
        self.values = values
        self.last_ms = {}
        self.kernel = type("Kernel", (), {"starts": 0})()

    def __call__(self, *images):
        self.kernel.starts += 1
        self.last_ms = {"pre": 1., "kernel": 2., "dequant": .5, "partb": 3., "total": 6.5}
        return self.values


class FakeDetector:
    def __init__(self, class_ids=()):
        self.class_ids = class_ids

    def detect(self, image, *, grabbed):
        detections = tuple(
            Detection(index, CLASS_NAMES[index], .9, 100., 100.) for index in self.class_ids
        )
        params = {name: False for name in CLASS_NAMES}
        for index in self.class_ids:
            params[CLASS_NAMES[index]] = True
        completed = grabbed
        return RoadSignFrame(RoadSignResult(detections, params), grabbed, completed, 10., "fake")


def run_one(driver_values=(210., 190.), class_ids=()):
    sample = {"sample_id": "run/frame", "run": "run", "frame_id": 1}
    images = tuple(np.zeros((480, 640, 3), dtype=np.uint8) for _ in range(3))
    grabbed = [99.9, 99.9, 99.9]
    metadata = {"t_mono": 100.}
    return process_sample(
        sample, images, grabbed, metadata, 1, FakeDriver(driver_values),
        FakeDetector(class_ids), ControlPolicy(CONFIG, generation=1), ReplayClock(),
    )


def test_driver_yolo_policy_and_packet_are_joined():
    row = run_one()
    assert row["integer_reference_driver"]["left"] == 210.
    assert row["decision"]["reason"] == "driver"
    assert row["decision"]["fault"] is None
    assert row["packet_hex"] == encode_side_speed(210., 190.).hex()


def test_red_light_has_priority_over_driver_speed():
    row = run_one(class_ids=(6,))
    assert row["roadsign"]["params"]["ROADSIGN_red_light"] is True
    assert row["decision"]["stop"] is True
    assert row["decision"]["reason"] == "red_light"
    assert row["packet_hex"] == stop_frame().hex()


def test_nonfinite_driver_output_fails_closed():
    with pytest.raises(ValueError, match="driver.left"):
        run_one(driver_values=(float("nan"), 190.))
