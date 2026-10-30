from pathlib import Path
import json
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ps.car_loop import CarLoop, EventLog, RUNNING, WAIT_DEVICES, WAIT_START
from ps.control_arbiter import ControlDecision
from ps.packet import encode_side_speed, stop_frame


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class Devices:
    def __init__(self, problem=None):
        self.problem = problem

    def check(self):
        return self.problem


class Cameras:
    def __init__(self, clock):
        self.clock, self.age, self.error = clock, 0.01, None

    def latest(self):
        if self.error:
            raise self.error
        return [(name, self.clock() - self.age) for name in ("L", "F", "R")]


class Link:
    def __init__(self):
        self.frames, self.fail, self.short = [], False, False

    def write(self, data):
        if self.fail:
            raise OSError("port gone")
        self.frames.append(bytes(data))
        return len(data) - 1 if self.short else len(data)


class Supervisor:
    def __init__(self, decisions=()):
        self.decisions = list(decisions)
        self.calls = []
        self.reset_count = 0

    def evaluate(self, frames, left, right):
        self.calls.append((frames, left, right))
        return self.decisions.pop(0)

    def reset(self):
        self.reset_count += 1


class Log:
    def __init__(self):
        self.events = []

    def __call__(self, kind, **data):
        self.events.append((kind, data))

    def kinds(self):
        return [k for k, _ in self.events]


def make(problem=None, speeds=(200.0, 180.0), started=True, supervisor=None):
    clock = Clock()
    parts = {"devices": Devices(problem), "cameras": Cameras(clock), "link": Link(), "log": Log(),
             "speeds": list(speeds), "started": [started], "order": []}

    def infer(left, front, right):
        parts["order"].append((left, front, right))
        value = parts["speeds"]
        if isinstance(value, Exception):
            raise value
        return tuple(value)

    loop = CarLoop(parts["devices"], parts["cameras"], infer, parts["link"],
                   lambda: parts["started"][0], parts["log"], clock=clock, sleep=lambda s: None,
                   supervisor=supervisor)
    return loop, parts


def run_to_running(loop):
    loop.step()  # WAIT_DEVICES -> WAIT_START
    loop.step()  # WAIT_START -> RUNNING
    assert loop.state == RUNNING


def test_waits_silently_while_devices_are_missing():
    loop, p = make(problem="serial converter is not on port 1-1.2")
    for _ in range(5):
        assert loop.step() == loop.poll_s
    assert loop.state == WAIT_DEVICES and p["link"].frames == []
    assert p["log"].kinds().count("devices_not_ready") == 1  # 同一原因只记一次


def test_waits_silently_for_manual_start():
    loop, p = make(started=False)
    for _ in range(5):
        loop.step()
    assert loop.state == WAIT_START and p["link"].frames == []
    p["started"][0] = True
    loop.step()
    assert loop.state == RUNNING


def test_running_sends_legacy_bytes_in_left_front_right_order():
    loop, p = make(speeds=(200.7, 99.2))
    run_to_running(loop)
    loop.step()
    assert p["link"].frames == [encode_side_speed(200.7, 99.2)]
    assert p["order"] == [("L", "F", "R")]


def test_stale_frame_sends_stop_and_waits_again():
    loop, p = make()
    run_to_running(loop)
    loop.step()
    p["cameras"].age = 0.5
    loop.step()
    assert p["link"].frames[-1] == stop_frame()
    assert loop.state == WAIT_DEVICES and "stale_frame" in p["log"].kinds()


def test_camera_error_and_serial_error_fall_back_to_waiting():
    loop, p = make()
    run_to_running(loop)
    loop.step()
    p["cameras"].error = RuntimeError("left camera is not on port 1-1.3")
    loop.step()
    assert p["link"].frames[-1] == stop_frame() and loop.state == WAIT_DEVICES

    loop2, q = make()
    run_to_running(loop2)
    loop2.step()
    q["link"].fail = True
    loop2.step()
    assert loop2.state == WAIT_DEVICES
    assert "serial_error" in q["log"].kinds() and "stop_frame_failed" in q["log"].kinds()


def test_short_write_is_a_serial_error():
    loop, p = make()
    run_to_running(loop)
    p["link"].short = True
    loop.step()
    assert "serial_error" in p["log"].kinds()


def test_negative_speed_is_not_sent_and_repeated_rejects_stop_the_car():
    loop, p = make()
    run_to_running(loop)
    loop.step()
    p["speeds"] = [-3.0, 100.0]
    for _ in range(loop.max_rejects - 1):
        loop.step()
    assert len(p["link"].frames) == 1  # 只有第一帧正常速度
    loop.step()
    assert p["link"].frames[-1] == stop_frame()
    assert loop.stats["frames_rejected"] == loop.max_rejects


def test_inference_error_stops_the_car():
    loop, p = make()
    run_to_running(loop)
    loop.step()
    p["speeds"] = RuntimeError("kernel did not finish")
    loop.step()
    assert p["link"].frames[-1] == stop_frame() and loop.state == WAIT_DEVICES


def test_fatal_error_stops_the_car_and_ends_the_program():
    from ps.car_loop import FatalError
    loop, p = make()
    run_to_running(loop)
    loop.step()
    p["speeds"] = FatalError("kernel did not finish within 1.0s")
    with pytest.raises(FatalError):
        loop.run(should_stop=lambda: False)
    assert p["link"].frames[-1] == stop_frame()
    kinds = p["log"].kinds()
    assert "fatal" in kinds and kinds[-1] == "exit"
    assert "inference_error" not in kinds  # 不能当成普通错误回到等待重试


def test_no_stop_frame_if_nothing_was_ever_sent():
    loop, p = make(started=False)
    loop.run(should_stop=iter([False, False, True]).__next__)
    assert p["link"].frames == []
    assert p["log"].kinds()[-1] == "exit"


def test_exit_after_driving_sends_stop_even_on_ctrl_c():
    loop, p = make()
    calls = iter([False, False, False])

    def stop():
        try:
            return next(calls)
        except StopIteration:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        loop.run(should_stop=stop)
    assert p["link"].frames[-1] == stop_frame()
    kind, data = p["log"].events[-1]
    assert kind == "exit" and "KeyboardInterrupt" in data["reason"]


def test_optional_supervisor_can_stop_and_resume_without_changing_state():
    supervisor = Supervisor([
        ControlDecision(0.0, 0.0, True, "pedestrian"),
        ControlDecision(200.0, 180.0, False, "driver"),
    ])
    loop, p = make(supervisor=supervisor)
    run_to_running(loop)

    loop.step()
    assert p["link"].frames[-1] == stop_frame()
    assert loop.state == RUNNING and loop.stats["stop_frames"] == 1

    loop.step()
    assert p["link"].frames[-1] == encode_side_speed(200.0, 180.0)
    assert loop.stats["frames_sent"] == 1
    assert len(supervisor.calls) == 2
    assert p["log"].kinds().count("control_decision") == 2


def test_supervisor_error_uses_inference_fault_path():
    supervisor = Supervisor([ControlDecision(200.0, 180.0, False, "driver")])
    loop, p = make(supervisor=supervisor)
    run_to_running(loop)
    loop.step()

    def broken(frames, left, right):
        raise RuntimeError("roadsign stale")

    supervisor.evaluate = broken
    loop.step()
    assert p["link"].frames[-1] == stop_frame()
    assert loop.state == WAIT_DEVICES and "supervisor_error" in p["log"].kinds()


def test_manual_stop_preempts_supervisor_and_resets_it():
    supervisor = Supervisor([ControlDecision(200.0, 180.0, False, "driver")])
    loop, p = make(supervisor=supervisor)
    run_to_running(loop)
    loop.step()
    assert len(supervisor.calls) == 1

    loop.stop_request = lambda: "manual e-stop"
    loop.step()
    assert loop.state == WAIT_START
    assert len(supervisor.calls) == 1
    assert p["link"].frames[-1] == stop_frame()
    assert supervisor.reset_count == 2  # 初次人工启动一次，人工急停后再复位一次


def test_finish_stop_waits_for_manual_start_and_resets_supervisor():
    supervisor = Supervisor([ControlDecision(0.0, 0.0, True, "finish", enter_wait_start=True)])
    loop, p = make(supervisor=supervisor)
    run_to_running(loop)

    loop.step()
    assert p["link"].frames[-1] == stop_frame()
    assert loop.state == WAIT_START
    assert supervisor.reset_count == 2


def test_event_log_writes_one_json_per_line(tmp_path):
    path = tmp_path / "events.jsonl"
    log = EventLog(path, clock=lambda: 0.0)
    log("state", old=None, new="WAIT_DEVICES")
    log("stop_frame", reason="停车")
    log.close()
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    assert [x["kind"] for x in lines] == ["state", "stop_frame"]
    assert lines[1]["reason"] == "停车" and lines[0]["utc"].startswith("1970-01-01")
