from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ps.control_v2 import ControlPolicy, DriverSnapshot, PolicyConfig, SignSnapshot


def config(**changes):
    values = dict(max_driver_age_s=0.5, max_sign_age_s=0.5, confirm_frames=2,
                  pedestrian_clear_frames=2, crosswalk_clear_frames=2,
                  crosswalk_hold_s=0.3, speed_limit_factor=0.5, command_ttl_s=0.2,
                  confirmation_max_gap_s=0.2)
    values.update(changes)
    return PolicyConfig(**values)


def driver(seq=1, at=10.0, *, generation=1, left=200.0, right=100.0, completed=None):
    return DriverSnapshot(generation, seq, at, at if completed is None else completed,
                          True, None, left, right)


def signs(seq=1, at=10.0, ids=(), *, generation=1, completed=None):
    from ps.roadsign import CLASS_NAMES, Detection
    accepted = tuple(Detection(i, CLASS_NAMES[i], 0.9, 100.0, 100.0) for i in ids)
    return SignSnapshot(generation, seq, at, at if completed is None else completed,
                        True, None, accepted)


def test_first_results_wait_without_fault_then_drive_with_unmodified_raw_values():
    policy = ControlPolicy(config(), generation=1)
    waiting = policy.step(10.0, None, None)
    assert waiting.stop and waiting.reason == "waiting_results"
    assert waiting.state.fault is None
    ready = policy.step(10.0, driver(), signs())
    assert not ready.stop and (ready.left, ready.right) == (200.0, 100.0)
    assert ready.reason == "driver"


def test_capture_age_not_completion_age_fault_latches_until_new_generation():
    policy = ControlPolicy(config(), generation=1)
    stale = policy.step(10.6, driver(completed=10.6), signs(completed=10.6))
    assert stale.stop and stale.state.fault == "driver_stale"
    assert policy.step(10.7, driver(2, 10.7), signs(2, 10.7)).stop
    with pytest.raises(ValueError):
        policy.reset(1)
    policy.reset(2)
    assert policy.step(10.8, None, None).reason == "waiting_results"
    assert not policy.step(10.8, driver(1, 10.8, generation=2),
                           signs(1, 10.8, generation=2)).stop


@pytest.mark.parametrize("failure", ["future", "order", "generation", "mutated_seq", "clock", "illegal_speed", "worker"])
def test_bad_inputs_fail_closed_and_do_not_self_recover(failure):
    policy = ControlPolicy(config(), generation=1)
    policy.step(10.0, driver(), signs())
    now, d, s = 10.1, driver(2, 10.1), signs(2, 10.1)
    if failure == "future":
        d = driver(2, 10.2)
    elif failure == "order":
        d = driver(0, 9.9)
    elif failure == "generation":
        s = signs(2, 10.1, generation=2)
    elif failure == "mutated_seq":
        d = driver(1, 10.0, left=250.0)
    elif failure == "clock":
        now = 9.9
    elif failure == "illegal_speed":
        d = driver(2, 10.1, left=-0.5)
    elif failure == "worker":
        s = SignSnapshot(1, 2, 10.1, 10.1, False, "inference failure", ())
    decision = policy.step(now, d, s)
    assert decision.stop and decision.reason.startswith("fault:")
    assert policy.step(10.3, driver(3, 10.3), signs(3, 10.3)).stop


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, "10"])
def test_nonfinite_or_wrong_type_timestamps_and_config_are_rejected(bad):
    with pytest.raises(ValueError):
        driver(at=bad)
    with pytest.raises(ValueError):
        config(max_driver_age_s=bad)


def test_snapshot_is_frozen_and_ten_class_mapping_is_strict():
    from dataclasses import FrozenInstanceError
    from ps.roadsign import Detection
    with pytest.raises(ValueError):
        DriverSnapshot(1, 1, 10., 10., False, "worker_error", float("nan"), 100.)
    with pytest.raises(FrozenInstanceError):
        driver().left = 1
    for detection in (Detection(10, "ROADSIGN_cone_bucket", .9, 100, 100),
                      Detection(0, "ROADSIGN_right_turn", .9, 100, 100)):
        with pytest.raises(ValueError):
            SignSnapshot(1, 1, 10., 10., True, None, (detection,))


def advance(policy, seq, at, ids=(), **kwargs):
    return policy.step(at, driver(seq, at), signs(seq, at, ids), **kwargs)


def test_red_and_pedestrian_stop_immediately_green_only_clears_red_not_pedestrian():
    policy = ControlPolicy(config(), generation=1)
    assert advance(policy, 1, 10., (6,)).reason == "red_light"
    assert advance(policy, 2, 10.05).reason == "red_light"
    # Green with red is not a clearing observation, even if repeated.
    assert advance(policy, 3, 10.10, (5, 6)).state.red
    assert advance(policy, 4, 10.15, (5,)).state.red
    cached = policy.step(10.16, driver(4, 10.15), signs(4, 10.15, (5,)))
    assert cached.state.red
    decision = advance(policy, 5, 10.20, (5, 9))
    assert not decision.state.red and decision.reason == "pedestrian"
    assert advance(policy, 6, 10.25).reason == "pedestrian"
    assert not advance(policy, 7, 10.30).stop


def test_emergency_is_latched_and_cannot_be_overwritten_by_driver_or_green():
    policy = ControlPolicy(config(), generation=1)
    assert advance(policy, 1, 10., emergency_stop=True).reason == "emergency_stop"
    assert advance(policy, 2, 10.1, (5,)).reason == "emergency_stop"
    policy.reset(2)
    assert not policy.step(10.2, driver(1, 10.2, generation=2), signs(1, 10.2, generation=2)).stop


def test_single_conflicting_limit_pair_is_immediately_conservative():
    policy = ControlPolicy(config(), generation=1)
    limited = advance(policy, 1, 10., (3, 4))
    assert limited.state.limited and limited.left == 100. and limited.right == 50.


def test_limit_can_switch_repeatedly_conflicts_select_limit_and_preserve_ratio():
    policy = ControlPolicy(config(), generation=1)
    seq, at = 0, 10.0
    for ids, expected in [((3,), .5), ((4,), 1.), ((3, 4), .5), ((4,), 1.), ((3,), .5)]:
        for _ in range(2):
            seq += 1
            at += .05
            result = advance(policy, seq, at, ids)
        assert not result.stop
        assert (result.left, result.right) == (200 * expected, 100 * expected)


def test_confirmation_requires_distinct_new_frames_and_bounded_capture_gap():
    policy = ControlPolicy(config(), generation=1)
    assert not advance(policy, 1, 10., (3,)).state.limited
    assert not policy.step(10.1, driver(), signs(ids=(3,))).state.limited
    assert not advance(policy, 2, 10.3, (3,)).state.limited
    assert advance(policy, 3, 10.35, (3,)).state.limited


def test_crosswalk_hold_is_not_extended_and_rearms_after_fresh_clear_frames():
    policy = ControlPolicy(config(), generation=1)
    assert not advance(policy, 1, 10., (2,)).stop
    hold = advance(policy, 2, 10.05, (2,))
    assert hold.reason == "crosswalk"
    deadline = hold.state.crosswalk_until
    assert advance(policy, 3, 10.10, (2,)).state.crosswalk_until == deadline
    assert not advance(policy, 4, 10.4, (2,)).stop
    assert not advance(policy, 5, 10.45).state.crosswalk_armed
    assert advance(policy, 6, 10.5).state.crosswalk_armed
    assert not advance(policy, 7, 10.55, (2,)).stop
    assert advance(policy, 8, 10.6, (2,)).reason == "crosswalk"


@pytest.mark.parametrize("ids,direction,reason", [((0,), "left", "junction_route_unavailable"),
                                                    ((1,), "right", "junction_route_unavailable"),
                                                    ((0, 1), "conflict", "junction_conflict")])
def test_junction_intent_is_locked_without_executor_until_explicit_completion(ids, direction, reason):
    policy = ControlPolicy(config(), generation=1)
    advance(policy, 1, 10., ids)
    decision = advance(policy, 2, 10.05, ids)
    assert decision.stop and decision.reason == reason
    intent = decision.state.junction
    assert intent.direction == direction
    assert advance(policy, 3, 10.1, ids).state.junction == intent
    assert advance(policy, 4, 10.15).state.junction == intent
    with pytest.raises(ValueError):
        policy.complete_route(2, intent.intent_seq)
    policy.complete_route(1, intent.intent_seq)
    assert not advance(policy, 5, 10.2).stop
    # Repeated single route sign cannot re-trigger; a live conflict always stops.
    repeated = advance(policy, 6, 10.25, ids)
    assert repeated.stop is (direction == "conflict")


def test_warning_change_line_and_direction_do_not_invent_finish_or_fixed_forward():
    policy = ControlPolicy(config(), generation=1)
    assert advance(policy, 1, 10., (8, 7)).reason == "driver"
    assert advance(policy, 2, 10.05, (8, 7)).reason == "driver"
    assert not advance(policy, 3, 10.1, (0, 8)).stop
    assert advance(policy, 4, 10.15, (0, 8)).reason == "junction_route_unavailable"
