"""tools/car/car_main.py 的离线测试。所有硬件换成假的，整数参考当 PL。"""
from pathlib import Path
import argparse
import csv
import json
import sys

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "car"))
import car_main  # noqa: E402
from ps import pl_infer  # noqa: E402
from ps.packet import encode_side_speed, stop_frame  # noqa: E402

MODELS = {
    "bit": ROOT.parent.parent / "fpga/build/conv_front_int8_20261021.bit",
    "ref": ROOT.parent.parent / "host/model/ref_int8.py",
    "params": ROOT.parent.parent / "host/model/params.json",
    "partb": ROOT.parent.parent / "host/model/s16_j1c2_cut2int8_ps.onnx",
}


@pytest.fixture
def models_dir(tmp_path):
    d = tmp_path / "models"
    d.mkdir()
    for name, src in MODELS.items():
        (d / car_main.MODEL_FILES[name]).write_bytes(src.read_bytes())
    return d


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += max(s, 0.001)


class FakeCams:
    def __init__(self, clock):
        self.clock, self.closed = clock, False
        img = np.zeros((480, 640, 3), np.uint8)
        img[:, :320] = 200
        self.images = [img, np.full_like(img, 90), np.full_like(img, 160)]

    def ensure_running(self):
        return None

    def latest(self):
        return [(im, self.clock() - 0.02) for im in self.images]

    def stats(self):
        return {"restarts": 0}

    def close(self):
        self.closed = True


class FakeLink:
    def __init__(self):
        self.frames, self.opens, self.closed = [], 0, False

    def ensure_open(self, device):
        self.opens = self.opens or 1
        return None

    def write(self, data):
        self.frames.append(bytes(data))
        return len(data)

    def close(self):
        self.closed = True


class FakeJs:
    """按脚本吐出启停：script 是 (第几次查询, 'toggle'/'stop') 的列表。"""

    def __init__(self, script):
        self.script, self.calls = list(script), 0
        self.device, self.opens, self.presses = "js0", 1, {"toggle": 0, "stop": 0}
        self.closed = False

    def check(self):
        return None

    def _pop(self):
        self.calls += 1
        if self.script and self.script[0][0] <= self.calls:
            kind = self.script.pop(0)[1]
            self.presses[kind] += 1
            return kind
        return None

    def clear(self):
        pass

    def start_trigger(self):
        return self._pop() == "toggle"

    def stop_request(self):
        kind = self._pop()
        return None if kind is None else f"{kind} pressed"

    def close(self):
        self.closed = True


class FakeHw:
    def __init__(self, tmp_path, js_script, marker=True, stop_after=None, kernel=None):
        self.clock = Clock()
        self.sleep = self.clock.sleep
        self.marker = tmp_path / "run" / "pl_loaded.json"
        self.marker.parent.mkdir()
        if marker:
            self.marker.write_text(json.dumps({"boot_id": "b" * 32, "bit_sha256": pl_infer.PINNED_SHA256["bit"]}))
        self.cams, self.link, self.js = FakeCams(self.clock), FakeLink(), FakeJs(js_script)
        self.kernel = kernel
        self.parked = False
        self.stop_after = stop_after

    def boot_id(self):
        return "b" * 32

    def wait_for_pl(self, log, stop):
        return car_main.wait_for_pl(self.marker, self.boot_id(), log, stop, poll_s=1.0, sleep=self._sleep_and_stop(stop))

    def _sleep_and_stop(self, stop):
        def sleep(s):
            self.sleep(s)
            if self.stop_after is not None and self.clock.t >= self.stop_after:
                stop.reason = "SIGTERM"
        return sleep

    def attach(self, bit_path):
        if self.kernel is not None:
            return self.kernel
        ref = pl_infer.load_module("ref_int8_t", MODELS["ref"])
        params = json.loads(MODELS["params"].read_text(encoding="utf-8"))
        return pl_infer.ReferenceKernel(ref, params)

    def make_cams(self, config):
        return self.cams

    def make_link(self, line):
        return self.link

    def make_js(self, config):
        return self.js

    def verify_cams(self, config):
        return None

    def resolve_serial(self, config):
        return {"device": "ttyUSB0"}

    def park(self):
        self.parked = True


def args_for(tmp_path, models_dir, **kw):
    a = car_main.build_cli().parse_args(["--runs-dir", str(tmp_path / "runs"), "--models", str(models_dir),
                                        "--backend", "pl"])
    for k, v in kw.items():
        setattr(a, k, v)
    return a


def events_of(tmp_path):
    runs = sorted((tmp_path / "runs").iterdir())
    lines = (runs[-1] / "events.jsonl").read_text(encoding="utf-8").splitlines()
    return runs[-1], [json.loads(x) for x in lines]


def stop_when(hw, t):
    flag = car_main.StopFlag()
    orig = hw.sleep

    def sleep(s):
        orig(s)
        if hw.clock.t >= t:
            flag.reason = "SIGTERM"
    hw.sleep = sleep
    return flag


def test_full_run_toggle_start_drive_toggle_stop_then_sigterm(tmp_path, models_dir):
    hw = FakeHw(tmp_path, js_script=[(3, "toggle"), (60, "toggle")])
    stop = stop_when(hw, hw.clock.t + 20.0)
    code = car_main.run(args_for(tmp_path, models_dir), hw, stop)
    assert code == car_main.EXIT_OK
    run_dir, events = events_of(tmp_path)
    kinds = [e["kind"] for e in events]
    assert kinds[0] == "start" and kinds[-1] == "exit"
    assert "config_ok" in kinds and "pl_marker" in kinds
    selftest = next(e for e in events if e["kind"] == "pl_self_test")
    assert selftest["mismatch_bytes"] == 0 and selftest["bytes"] == pl_infer.OUT_BYTES
    assert "manual_stop" in kinds
    speed = [f for f in hw.link.frames if f != stop_frame()]
    assert len(speed) > 10
    assert all(f == speed[0] for f in speed)                # 同样的图，同样的速度
    assert hw.link.frames[-1] == stop_frame()
    with open(run_dir / "frames.csv", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == len(speed)
    left, right = float(rows[0]["speed_left"]), float(rows[0]["speed_right"])
    assert speed[0] == encode_side_speed(left, right)
    assert hw.cams.closed and hw.link.closed and hw.js.closed


def test_waits_for_marker_and_never_attaches_pl_without_it(tmp_path, models_dir):
    hw = FakeHw(tmp_path, js_script=[], marker=False)
    attached = []
    hw.attach = lambda bit: attached.append(bit)
    hw.stop_after = hw.clock.t + 10.0
    code = car_main.run(args_for(tmp_path, models_dir), hw, car_main.StopFlag())
    assert code == car_main.EXIT_OK and attached == [] and hw.link.frames == []
    _, events = events_of(tmp_path)
    waits = [e for e in events if e["kind"] == "pl_not_ready"]
    assert len(waits) == 1 and "no marker" in waits[0]["problem"]   # 同一原因只记一次


def test_marker_from_previous_boot_is_rejected(tmp_path):
    marker = tmp_path / "m.json"
    marker.write_text(json.dumps({"boot_id": "old", "bit_sha256": pl_infer.PINNED_SHA256["bit"]}))
    assert "another boot" in car_main.marker_problem(marker, "new")
    marker.write_text(json.dumps({"boot_id": "new", "bit_sha256": "0" * 64}))
    assert "different bit" in car_main.marker_problem(marker, "new")
    marker.write_text(json.dumps({"boot_id": "new", "bit_sha256": pl_infer.PINNED_SHA256["bit"]}))
    assert car_main.marker_problem(marker, "new") is None


def test_tampered_model_is_a_config_error_and_touches_nothing(tmp_path, models_dir):
    onnx = models_dir / car_main.MODEL_FILES["partb"]
    onnx.write_bytes(onnx.read_bytes() + b"\0")
    hw = FakeHw(tmp_path, js_script=[])
    hw.attach = lambda bit: pytest.fail("must not attach PL")
    code = car_main.run(args_for(tmp_path, models_dir), hw, car_main.StopFlag())
    assert code == car_main.EXIT_CONFIG
    _, events = events_of(tmp_path)
    err = next(e for e in events if e["kind"] == "config_error")
    assert "partb sha256 mismatch" in err["error"]


def test_pl_self_test_mismatch_stops_before_any_device(tmp_path, models_dir):
    hw = FakeHw(tmp_path, js_script=[])
    real = hw.attach(None)

    class BadKernel:
        inbuf, outbuf, starts, hung = real.inbuf, real.outbuf, 0, False

        def run(self):
            real.run()
            self.outbuf[0] ^= 1
            return self.outbuf

        def idle(self):
            return True, 4
    hw.kernel = BadKernel()
    hw.make_cams = lambda c: pytest.fail("must not open cameras")
    code = car_main.run(args_for(tmp_path, models_dir), hw, car_main.StopFlag())
    assert code == car_main.EXIT_CONFIG
    _, events = events_of(tmp_path)
    assert next(e for e in events if e["kind"] == "pl_self_test")["mismatch_bytes"] == 1


def test_kernel_hang_sends_stop_and_parks(tmp_path, models_dir):
    hw = FakeHw(tmp_path, js_script=[(2, "toggle")])
    real = hw.attach(None)

    class HangLater:
        inbuf, outbuf, starts, hung = real.inbuf, real.outbuf, 0, False

        def run(self):
            self.starts += 1
            if self.starts > 4:                      # 自检 + 几帧之后卡死
                self.hung = True
                raise TimeoutError("kernel did not finish within 1.0s")
            return real.run()

        def idle(self):
            return False, 1
    hw.kernel = HangLater()
    code = car_main.run(args_for(tmp_path, models_dir), hw, car_main.StopFlag())
    assert code == car_main.EXIT_ERROR and hw.parked
    assert hw.link.frames[-1] == stop_frame()
    _, events = events_of(tmp_path)
    kinds = [e["kind"] for e in events]
    assert "fatal" in kinds and "parked" in kinds


def test_heartbeat_is_logged(tmp_path, models_dir):
    hw = FakeHw(tmp_path, js_script=[(2, "toggle")])
    stop = stop_when(hw, hw.clock.t + 10.0)
    car_main.run(args_for(tmp_path, models_dir, heartbeat_s=2.0), hw, stop)
    _, events = events_of(tmp_path)
    beats = [e for e in events if e["kind"] == "heartbeat"]
    assert len(beats) >= 3
    assert beats[-1]["state"] == "RUNNING" and beats[-1]["frames_sent"] > 0
    assert beats[-1]["joystick"]["device"] == "js0"


def test_old_runs_are_pruned(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "not_a_run").mkdir()
    import os
    import time
    for i in range(5):
        p = runs / f"run_2026010{i}_000000_x"
        p.mkdir()
        os.utime(p, (time.time() - 100 + i, time.time() - 100 + i))
    current = car_main.make_run_dir(runs, "abcdef12" * 4, keep=3)
    left = sorted(p.name for p in runs.iterdir())
    assert current.name in left and "not_a_run" in left
    assert len([n for n in left if n.startswith("run_")]) == 3
    assert "run_20260104_000000_x" in left and "run_20260100_000000_x" not in left


def test_self_test_images_cover_every_pixel_value():
    images = car_main.self_test_images()
    assert all(im.shape == (480, 640, 3) and im.dtype == np.uint8 for im in images)
    assert len(np.unique(np.concatenate([im.reshape(-1) for im in images]))) == 256
    assert all(np.array_equal(a, b) for a, b in zip(images, car_main.self_test_images()))


def test_stop_flag_second_signal_interrupts():
    flag = car_main.StopFlag()
    flag(15, None)
    assert flag and flag.reason == "SIGTERM"
    with pytest.raises(KeyboardInterrupt):
        flag(2, None)
