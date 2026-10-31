"""Fixed-left offline candidate: no devices, CSV image paths are never opened."""
from dataclasses import FrozenInstanceError
import hashlib
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ps.control_v2.fixed_left import FrozenRoute, LEFT_SHA256, FixedLeftPolicy
from ps.control_v2 import DriverSnapshot, SignSnapshot
from test_control_v2_policy import config, driver, signs
from ps.packet import encode_side_speed, parse_like_stm32


def test_pinned_full_left_source_raw_values_and_original_packet_truncation():
    route = FrozenRoute.load(max_raw_speed=300.)
    assert route.sha256 == LEFT_SHA256 == '031339b47d8a7254aceadb846a1253dbb6b28e9b084393666f86a466d13b96bb'
    assert len(route.rows) == 98
    assert (route.rows[9].left, route.rows[9].right) == (191.088, 208.912)
    assert (route.rows[97].left, route.rows[97].right) == (146.552, 253.448)
    frame = encode_side_speed(route.rows[9].left, route.rows[9].right)
    assert len(frame) == 21 and parse_like_stm32(frame) == [191, 191, 208, 208]
    assert hashlib.sha256(Path(route.source).read_bytes()).hexdigest() == route.sha256
    with pytest.raises(FrozenInstanceError):
        route.rows = ()
    with pytest.raises(TypeError):
        FrozenRoute.load(path='right.csv', max_raw_speed=300.)


def make_policy():
    return FixedLeftPolicy(config(), FrozenRoute.load(max_raw_speed=300.),
                           generation=1, max_lateness_s=.075)


def test_arm_is_stationary_start_is_explicit_and_driver_stays_raw_health_only():
    policy = make_policy()
    assert policy.step(10., driver(), signs()).stop
    policy.arm(generation=1)
    assert policy.step(10., driver(), signs()).reason == 'route_armed'
    assert policy.start(10., driver(), signs(), generation=1, fixed_origin_confirmed=True)
    tick = policy.step(10., driver(), signs())
    assert not tick.stop and tick.route_meta.row_index == 0
    assert (tick.left, tick.right) == (200., 200.)
    assert not tick.route_meta.reused
    assert policy.step(10., driver(), signs()).route_meta.reused
    assert tick.route_meta.driver_raw == (200., 100.)
    assert tick.route_meta.driver_source_seq == 1
    assert tick.state.fault is None and tick.state.emergency is False


@pytest.mark.parametrize('hz', [10, 15, 50])
def test_full_98_rows_follow_nominal_integer_slots_not_tick_count_and_eof_stays_stop(hz):
    policy = make_policy()
    policy.arm(generation=1)
    assert policy.start(10., driver(), signs(), generation=1, fixed_origin_confirmed=True)
    observed, repeats = [], 0
    for n in range(10 * hz + 1):
        now_ns = 10_000_000_000 + (n * 1_000_000_000 + hz // 2) // hz
        now = now_ns / 1_000_000_000
        decision = policy.step(now, driver(n + 1, now), signs(n + 1, now))
        meta = decision.route_meta
        if meta.status == 'running':
            assert not decision.stop and meta.candidate_issued
            if not meta.reused:
                observed.append(meta.row_index)
                assert meta.nominal_due_ns == 10_000_000_000 + meta.row_index * 100_000_000
                assert meta.lateness_ns <= 75_000_000
            else:
                repeats += 1
        else:
            assert meta.status == 'completed' and decision.stop
            assert now_ns >= 19_800_000_000
    assert observed == list(range(98))
    assert repeats == {10: 0, 15: 49, 50: 392}[hz]
    assert policy.step(20.1, driver(1000, 20.1), signs(1000, 20.1)).stop
    with pytest.raises(ValueError):
        policy.complete_route(1, 1)
    with pytest.raises(ValueError):
        policy.reset(2)


@pytest.mark.parametrize('kind', ['red', 'pedestrian', 'manual', 'crosswalk', 'limit',
                                  'driver_stale', 'sign_stale', 'worker_error', 'external'])
def test_safety_veto_aborts_and_no_clear_event_can_resume(kind):
    policy = make_policy()
    policy.arm(generation=1)
    assert policy.start(10., driver(), signs(), generation=1, fixed_origin_confirmed=True)
    assert not policy.step(10., driver(), signs()).stop
    ids = {'red': (6,), 'pedestrian': (9,), 'crosswalk': (2,), 'limit': (3,)}.get(kind, ())
    decision = policy.step(10.05, driver(2, 10.05), signs(2, 10.05, ids),
                           emergency_stop=kind == 'manual',
                           external_fault='fixture_io_error' if kind == 'external' else None)
    if kind in ('crosswalk', 'limit'):
        assert not decision.stop  # original two-fresh-frame confirmation remains intact
        decision = policy.step(10.1, driver(3, 10.1), signs(3, 10.1, ids))
    elif kind in ('driver_stale', 'sign_stale'):
        d = driver(3, 10.6) if kind == 'sign_stale' else driver(2, 10.05)
        s = signs(3, 10.6) if kind == 'driver_stale' else signs(2, 10.05)
        decision = policy.step(10.6, d, s)
    elif kind == 'worker_error':
        decision = policy.step(10.1, driver(3, 10.1),
                               SignSnapshot(1, 3, 10.1, 10.1, False, 'fixture failure', ()))
    assert decision.stop and decision.route_meta.status == 'aborted'
    assert decision.route_meta.abort_reason
    for seq in range(10, 14):
        now = 11. + seq / 100
        clear = policy.step(now, driver(seq, now), signs(seq, now, (4, 5)))
        assert clear.stop and clear.route_meta.status == 'aborted'
    if kind == 'limit':
        assert decision.state.limited and not clear.state.limited
    with pytest.raises(ValueError):
        policy.start(12., driver(20, 12.), signs(20, 12.), generation=1, fixed_origin_confirmed=True)


@pytest.mark.parametrize('bad', ['missing_driver', 'missing_sign', 'red', 'limit', 'confirmation', 'old_generation'])
def test_start_rejected_requests_are_not_delayed_or_retried(bad):
    policy = make_policy()
    policy.arm(generation=1)
    d, s = driver(), signs()
    if bad == 'missing_driver':
        d = None
    if bad == 'missing_sign':
        s = None
    if bad == 'red':
        s = signs(ids=(6,))
    if bad == 'limit':
        s = signs(ids=(3, 4))
    if bad in ('confirmation', 'old_generation'):
        with pytest.raises(ValueError):
            policy.start(10., d, s, generation=0 if bad == 'old_generation' else 1,
                         fixed_origin_confirmed=False if bad == 'confirmation' else True)
    else:
        assert not policy.start(10., d, s, generation=1, fixed_origin_confirmed=True)
    result = policy.step(10.1, driver(2, 10.1), signs(2, 10.1, (4, 5)))
    assert result.stop and result.route_meta.status == 'aborted'


def test_directions_are_ignored_only_after_entire_snapshot_identity_validation():
    policy = make_policy()
    policy.arm(generation=1)
    assert policy.start(10., driver(), signs(ids=(1,)), generation=1, fixed_origin_confirmed=True)
    assert not policy.step(10., driver(), signs(ids=(1,))).stop
    for seq, ids in enumerate([(7, 8), (0,), (1,), (0, 1)], 2):
        now = 10. + (seq - 1) * .02
        decision = policy.step(now, driver(seq, now), signs(seq, now, ids))
        assert not decision.stop and decision.state.junction is None
        assert decision.route_meta.sign_raw.detections == signs(seq, now, ids).detections
    changed = policy.step(10.09, driver(6, 10.09), signs(5, 10.08, (0,)))
    assert changed.stop and changed.state.fault == 'signs_changed_same_seq'
    assert changed.route_meta.status == 'aborted'


def test_snapshot_order_identity_generation_and_cached_freshness_are_not_filtered():
    bad_cases = [
        (driver(0, 9.9), signs(2, 10.1), 'driver_out_of_order'),
        (driver(2, 10.1), signs(0, 9.9), 'signs_out_of_order'),
        (driver(2, 10.1), signs(2, 10.1, generation=2), 'signs_generation'),
        (driver(left=201.), signs(), 'driver_changed_same_seq'),
    ]
    for d, s, reason in bad_cases:
        policy = make_policy()
        policy.arm(generation=1)
        assert policy.start(10., driver(), signs(), generation=1, fixed_origin_confirmed=True)
        policy.step(10., driver(), signs())
        result = policy.step(10.1, d, s)
        assert result.stop and result.state.fault == reason
    policy = make_policy()
    policy.arm(generation=1)
    assert policy.start(10., driver(), signs(), generation=1, fixed_origin_confirmed=True)
    policy.step(10., driver(), signs())
    reused = policy.step(10.05, driver(), signs())
    assert not reused.stop and reused.route_meta.reused
    assert policy.step(10.51, driver(), signs()).route_meta.status == 'aborted'


@pytest.mark.parametrize('kind', ['late', 'missed', 'missed_eof', 'eof_late'])
def test_lateness_and_missed_slots_never_skip_or_catch_up(kind):
    policy = make_policy()
    policy.arm(generation=1)
    assert policy.start(10., driver(), signs(), generation=1, fixed_origin_confirmed=True)
    policy.step(10., driver(), signs())
    if kind == 'eof_late':
        for n in range(1, 98):
            now = (10_000_000_000 + n * 100_000_000) / 1e9
            assert not policy.step(now, driver(n + 1, now), signs(n + 1, now)).stop
    now = {'late': 10.175000001, 'missed': 10.2, 'missed_eof': 19.8, 'eof_late': 19.875000001}[kind]
    result = policy.step(now, driver(200, now), signs(200, now))
    assert result.stop and result.route_meta.status == 'aborted'
    assert result.route_meta.row_index == (97 if kind == 'eof_late' else 0)
    assert not result.route_meta.candidate_issued
    assert policy.step(now + .1, driver(201, now + .1), signs(201, now + .1)).stop


def test_raw_parser_is_strict_without_accessing_first_three_columns(monkeypatch):
    from ps.control_v2.fixed_left import _parse_csv_bytes
    good = b'never-open-a,never-open-b,never-open-c,12.9,34.1\n'
    def parse(raw, rows=1, expected=None, limit=300.):
        return _parse_csv_bytes(raw, expected_sha=expected or hashlib.sha256(raw).hexdigest(),
                                expected_rows=rows, max_raw_speed=limit)
    assert parse(good)[0].left == 12.9
    for raw in [b'', b'a,b,c,1\n', b'a,b,c,1,2,3\n', b',b,c,1,2\n',
                b'a,b,c,nan,2\n', b'a,b,c,inf,2\n', b'a,b,c,True,2\n',
                b'a,b,c,-0.001,2\n', b'a,b,c,999.001,2\n', b'a,b,c,300.001,2\n']:
        with pytest.raises(ValueError):
            parse(raw)
    with pytest.raises(ValueError, match='sha256'):
        parse(good, expected='0' * 64)
    with pytest.raises(ValueError, match='row_count'):
        parse(good, rows=98)
    import ps.control_v2.fixed_left as module
    monkeypatch.setattr(module, 'LEFT_SOURCE', Path('not-a-real-left-file.csv'))
    with pytest.raises(FileNotFoundError):
        FrozenRoute.load(max_raw_speed=300.)


def test_explicit_numeric_limits_reject_bool_nonfinite_and_out_of_contract():
    from ps.control_v2.fixed_left import FixedLeftPlayer
    for bad in [True, float('nan'), float('inf'), -1, 0, 1000, '300']:
        with pytest.raises(ValueError):
            FrozenRoute.load(max_raw_speed=bad)
    with pytest.raises(ValueError):
        FrozenRoute.load(max_raw_speed=297.)
    route = FrozenRoute.load(max_raw_speed=300.)
    for bad in [True, float('nan'), float('inf'), -1, 0, .1, '0.075', 1e-12, .099999999999]:
        with pytest.raises(ValueError):
            FixedLeftPlayer(route, generation=1, max_lateness_s=bad)
    for generation in [True, 0, -1, 1.0]:
        with pytest.raises(ValueError):
            FixedLeftPlayer(route, generation=generation, max_lateness_s=.075)


def test_bad_or_backwards_clock_aborts_and_same_time_does_not_advance():
    for bad in [True, float('nan'), float('inf'), '10', -1, 9.9999999999]:
        policy = make_policy()
        policy.arm(generation=1)
        assert policy.start(10., driver(), signs(), generation=1, fixed_origin_confirmed=True)
        first = policy.step(10., driver(), signs())
        repeat = policy.step(10., driver(), signs())
        assert first.route_meta.row_index == repeat.route_meta.row_index == 0
        assert repeat.route_meta.reused
        result = policy.step(bad, driver(), signs())
        assert result.stop and result.route_meta.status == 'aborted' and result.state.fault


def test_second_start_unarmed_start_and_non_boolean_origin_cannot_produce_motion():
    for mode in ['unarmed', 'second', 'origin_integer', 'start_delayed']:
        policy = make_policy()
        if mode != 'unarmed':
            policy.arm(generation=1)
        if mode == 'start_delayed':
            assert policy.start(10., driver(), signs(), generation=1, fixed_origin_confirmed=True)
            result = policy.step(10.1, driver(2, 10.1), signs(2, 10.1))
            assert result.stop and result.route_meta.abort_reason == 'missed_slot'
            continue
        if mode == 'second':
            assert policy.start(10., driver(), signs(), generation=1, fixed_origin_confirmed=True)
            assert not policy.step(10., driver(), signs()).stop
        with pytest.raises(ValueError):
            policy.start(10., driver(), signs(), generation=1,
                         fixed_origin_confirmed=1 if mode == 'origin_integer' else True)
        assert policy.step(10., driver(), signs()).stop


@pytest.mark.parametrize('failure', ['reject', 'sender_feedback', 'audit_overflow', 'audit_append'])
def test_original_controller_feedback_and_audit_errors_abort_without_extra_advance(failure, tmp_path):
    from dataclasses import asdict
    from ps.control_v2.audit import AuditChannel, write_audit
    from ps.control_v2.controller import Controller, SnapshotSource
    from ps.control_v2.sender import MemorySink, Sender
    from ps.packet import stop_frame
    class Clock:
        now = 10.
        def __call__(self):
            return self.now
    class RejectSender(Sender):
        def offer(self, command):
            self._fail('fixture_reject')
            return False
    class AppendFailure(AuditChannel):
        def append(self, row):
            self.fail('fixture_append_error')
            return False
    clock, policy = Clock(), make_policy()
    d, s = SnapshotSource(), SnapshotSource()
    d.publish(driver())
    s.publish(signs())
    policy.arm(generation=1)
    assert policy.start(10., d.snapshot(), s.snapshot(), generation=1, fixed_origin_confirmed=True)
    sink = MemorySink()
    sender = (RejectSender if failure == 'reject' else Sender)(sink, generation=1, stop_ttl_s=.2, clock=clock)
    audit = AppendFailure(20) if failure == 'audit_append' else AuditChannel(1 if failure == 'audit_overflow' else 20)
    if failure == 'sender_feedback':
        sender._fail('fixture_sender_feedback_error')  # injected fault, no thread/device
    controller = Controller(policy, d, s, sender, audit, clock=clock)
    row = controller.tick()
    if failure in ('audit_overflow', 'audit_append'):
        if failure == 'audit_append':
            assert sender.stop_latched  # Controller latches sender immediately; policy sees append failure next tick
        clock.now = 10.01
        row = controller.tick()
    assert row.decision.stop and row.decision.route_meta.status == 'aborted'
    assert row.decision.state.fault and sender.stop_latched
    assert row.decision.route_meta.row_index in (None, 0)
    assert row.encoded_hex == stop_frame().hex() and sink.frames == ()
    assert asdict(row)['decision']['route_meta']['driver_raw'] == (200., 100.)
    write_audit(tmp_path / 'tick.jsonl', [row])
    assert 'route_meta' in (tmp_path / 'tick.jsonl').read_text(encoding='utf-8')


def test_offline_tool_has_only_fixture_preview_or_memory_sink_and_exclusive_audit(tmp_path):
    import json
    from tools.fixed_left_replay import main, build_parser, SCENARIOS
    help_text = build_parser().format_help()
    assert '--fixture' in help_text and '--memory-sink' in help_text
    assert '--serial' not in help_text and '--csv' not in help_text
    for scenario in SCENARIOS:
        out = tmp_path / scenario
        args = ['--fixture', '--tick-hz', '15', '--max-raw-speed', '300',
                '--max-lateness-s', '.075', '--scenario', scenario, '--out-dir', str(out), '--memory-sink']
        assert main(args) == 0
        summary = json.loads((out / 'summary.json').read_text(encoding='utf-8'))
        rows = [json.loads(line) for line in (out / 'audit.jsonl').read_text(encoding='utf-8').splitlines()]
        assert summary['scope'].startswith('OFFLINE') and summary['nominal_hz'] == 10
        assert summary['status'] == '已验证' and summary['no_resume_after_abort']
        assert rows[0]['driver_raw'] == [200., 100.] and rows[0]['final'] == [200., 200.]
        assert rows[0]['decision']['route_meta']['desired'] == [200., 200.]
        assert len(bytes.fromhex(rows[0]['encoded_hex'])) == 21
        if scenario in ('complete', 'directions'):
            assert summary['unique_row_coverage'] == list(range(98))
            assert summary['repeat_count'] == 49 and summary['eof_stop_elapsed_ns'] == 9_800_000_000
            assert max(summary['first_observed_lateness_ns']) == 33_333_333
        else:
            assert summary['terminal_status'] == 'aborted'
        previous = (out / 'summary.json').read_bytes()
        assert main(args) == 1
        assert (out / 'summary.json').read_bytes() == previous
