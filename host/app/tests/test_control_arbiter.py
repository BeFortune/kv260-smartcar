from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ps.control_arbiter import RuleArbiter
from ps.roadsign import CLASS_NAMES


def params(**enabled):
    result = {name: False for name in CLASS_NAMES}
    for name, value in enabled.items():
        result["ROADSIGN_" + name] = value
    return result


def test_no_roadsign_keeps_driver_speed():
    arbiter = RuleArbiter()

    decision = arbiter.decide(params(), 200.0, 180.0, now=0.0)

    assert decision.stop is False
    assert decision.left == 200.0
    assert decision.right == 180.0
    assert decision.reason == "driver"


def test_pedestrian_stops_current_frame_and_clearance_resumes_driver():
    arbiter = RuleArbiter()

    stopped = arbiter.decide(params(pedestrian=True), 200.0, 180.0, now=0.0)
    resumed = arbiter.decide(params(), 200.0, 180.0, now=0.1)

    assert stopped.stop is True and stopped.left == stopped.right == 0.0
    assert stopped.reason == "pedestrian"
    assert resumed.stop is False and resumed.left == 200.0 and resumed.right == 180.0


def test_sidewalk_stop_is_nonblocking_and_expires_after_three_seconds():
    arbiter = RuleArbiter()

    first = arbiter.decide(params(side_walk=True), 200.0, 180.0, now=10.0)
    waiting = arbiter.decide(params(), 200.0, 180.0, now=12.9)
    expired = arbiter.decide(params(), 200.0, 180.0, now=13.0)

    assert first.stop is True and first.reason == "side_walk"
    assert waiting.stop is True and waiting.reason == "side_walk"
    assert expired.stop is False and expired.left == 200.0 and expired.right == 180.0


def test_speed_limit_is_latched_until_limit_off():
    arbiter = RuleArbiter()

    limited = arbiter.decide(params(limit_on=True), 200.0, 180.0, now=0.0)
    held = arbiter.decide(params(), 200.0, 180.0, now=0.1)
    released = arbiter.decide(params(limit_off=True), 200.0, 180.0, now=0.2)

    assert (limited.left, limited.right) == (150.0, 135.0)
    assert limited.reason == "driver_limited"
    assert (held.left, held.right) == (150.0, 135.0)
    assert (released.left, released.right) == (200.0, 180.0)
    assert released.reason == "driver"


def test_red_light_approach_stop_latch_and_green_resume_are_nonblocking():
    arbiter = RuleArbiter()
    arbiter.decide(params(limit_off=True), 200.0, 180.0, now=0.0)

    approach = arbiter.decide(params(red_light=True), 200.0, 180.0, now=1.0)
    waiting = arbiter.decide(params(), 200.0, 180.0, now=2.149)
    stopped = arbiter.decide(params(), 200.0, 180.0, now=2.15)
    held = arbiter.decide(params(), 200.0, 180.0, now=3.0)
    resumed = arbiter.decide(params(green_light=True), 200.0, 180.0, now=3.1)

    assert (approach.left, approach.right, approach.stop) == (170.0, 190.0, False)
    assert (waiting.left, waiting.right, waiting.stop) == (170.0, 190.0, False)
    assert stopped.stop is True and stopped.reason == "red_light"
    assert held.stop is True and held.reason == "red_light"
    assert resumed.stop is False and resumed.left == 200.0 and resumed.right == 180.0


def test_warning_and_replay_direction_finish_with_manual_rearm():
    arbiter = RuleArbiter()
    arbiter.set_replay_direction("left")

    finishing = arbiter.decide(params(warning=True, left_turn=True), 200.0, 180.0, now=0.0)
    waiting = arbiter.decide(params(), 200.0, 180.0, now=1.6)
    finished = arbiter.decide(params(), 200.0, 180.0, now=1.7)
    arbiter.reset()
    rearmed = arbiter.decide(params(), 200.0, 180.0, now=1.8)

    assert (finishing.left, finishing.right, finishing.stop) == (200.0, 200.0, False)
    assert (waiting.left, waiting.right, waiting.stop) == (200.0, 200.0, False)
    assert finished.stop is True and finished.enter_wait_start is True
    assert finished.reason == "finish"
    assert rearmed.stop is False and rearmed.left == 200.0 and rearmed.right == 180.0
