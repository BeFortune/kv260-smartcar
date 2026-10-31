"""OFFLINE fixed-left fixture preview. No cameras, serial, PYNQ, or vehicle CLI."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from ps.control_v2 import DriverSnapshot, PolicyConfig, SignSnapshot
from ps.control_v2.audit import AuditChannel, write_audit
from ps.control_v2.controller import Controller, SnapshotSource
from ps.control_v2.fixed_left import FrozenRoute, FixedLeftPolicy, PERIOD_NS, ROW_COUNT
from ps.control_v2.sender import MemorySink, WriteFeedback
from ps.packet import stop_frame
from ps.roadsign import CLASS_NAMES, Detection

SCENARIOS = ('complete', 'red', 'pedestrian', 'manual', 'crosswalk', 'limit', 'stale',
             'worker_error', 'sender_error', 'audit_error', 'late', 'missed_eof', 'directions')
SCOPE = ('OFFLINE synthetic accepted detections + raw driver fixtures, virtual monotonic clock; '
         'preview bytes or MemorySink only; not actual YOLO/driver inference, vehicle motion, '
         'STM32 reception, real 10/15Hz performance, or reaching the exit')


class _VirtualClock:
    ns = 10_000_000_000

    def __call__(self):
        return self.ns / 1_000_000_000


class _FixtureSender:
    """Synchronous controlled fixture, NOT the real threaded sender or a device."""
    def __init__(self, clock, *, memory_sink):
        self.generation, self.clock = 1, clock
        self.sink = MemorySink() if memory_sink else None
        self.fault = self.feedback = None
        self._events, self._stopped = [], False
        self.last_seq = 0

    def _record(self, seq, frame):
        written = None if self.sink is None else self.sink.write(frame)
        self.feedback = WriteFeedback(1, seq, frame.hex(),
                                      'not_sent_preview' if self.sink is None else 'fixture_memory_written',
                                      written, self.clock(), None)
        self._events.append(self.feedback)

    def offer(self, command):
        if (self._stopped or self.fault or command.generation != self.generation
                or command.seq <= self.last_seq or not command.issued <= self.clock() < command.expires):
            self.fault = self.fault or 'fixture_sender_rejected'
            return False
        self.last_seq = command.seq
        self._record(command.seq, command.frame)
        return True

    def request_stop(self, *, reason):
        self._stopped = True
        self._record(None, stop_frame())

    def drain_feedback(self):
        values = tuple(self._events)
        self._events.clear()
        return values


def _config():
    """Fixture values only: no validated vehicle safety defaults."""
    return PolicyConfig(max_driver_age_s=.5, max_sign_age_s=.5, confirm_frames=2,
                        pedestrian_clear_frames=2, crosswalk_clear_frames=2,
                        crosswalk_hold_s=.3, speed_limit_factor=.5, command_ttl_s=.2,
                        confirmation_max_gap_s=.2)


def _snapshots(seq, now, ids=(), *, healthy=True):
    driver = DriverSnapshot(1, seq, now, now, True, None, 200., 100.)
    signs = SignSnapshot(1, seq, now, now, healthy, None if healthy else 'fixture_worker_error',
                         tuple(Detection(i, CLASS_NAMES[i], .9, 100., 100.) for i in ids))
    return driver, signs


def _dump(path, value):
    with Path(path).open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def replay_fixture(*, tick_hz, max_raw_speed, max_lateness_s, scenario, memory_sink=False):
    """One full route or a permanent interruption, using only accepted fixture frames."""
    if type(tick_hz) is not int or tick_hz not in (10, 15, 50) or scenario not in SCENARIOS:
        raise ValueError('unsupported fixture tick rate/scenario')
    if type(memory_sink) is not bool:
        raise ValueError('memory_sink must be bool')
    route, clock, config = FrozenRoute.load(max_raw_speed=max_raw_speed), _VirtualClock(), _config()
    policy = FixedLeftPolicy(config, route, generation=1, max_lateness_s=max_lateness_s)
    sender = _FixtureSender(clock, memory_sink=memory_sink)
    audit, d_source, s_source = AuditChannel(2048), SnapshotSource(), SnapshotSource()
    d, s = _snapshots(1, clock(), (0,) if scenario == 'directions' else ())
    policy.arm(generation=1)
    if not policy.start(clock(), d, s, generation=1, fixed_origin_confirmed=True):
        raise ValueError('fixture start unexpectedly refused')
    controller = Controller(policy, d_source, s_source, sender, audit, clock=clock)
    samples, cached = [], (d, s)
    triggered = False
    offset_ns = 0
    for n in range(10 * tick_hz + 1):
        clock.ns = 10_000_000_000 + (n * 1_000_000_000 + tick_hz // 2) // tick_hz + offset_ns
        elapsed = clock.ns - 10_000_000_000
        event = 400_000_000 <= elapsed < 600_000_000
        if scenario in ('late', 'missed_eof') and event and not triggered:
            offset_ns = 76_000_000 if scenario == 'late' else 9_800_000_000 - elapsed
            clock.ns += offset_ns
            elapsed = clock.ns - 10_000_000_000
            triggered = True
        ids = {'red': (6,), 'pedestrian': (9,), 'crosswalk': (2,), 'limit': (3,)}.get(scenario, ()) if event else ()
        if scenario == 'directions':
            ids = ((0,), (1,), (0, 1), (7, 8))[n % 4]
        if elapsed >= 700_000_000 and scenario not in ('complete', 'directions'):
            ids = (4, 5)  # explicit clearing attempts must never resume a route
        d, s = _snapshots(n + 1, clock(), ids, healthy=not (scenario == 'worker_error' and event))
        if scenario == 'stale' and elapsed >= 400_000_000 and not triggered:
            triggered = True
        if scenario == 'stale' and 400_000_000 <= elapsed < 1_100_000_000:
            d = cached[0]
        elif not triggered or scenario != 'stale':
            cached = (d, s)
        if scenario == 'sender_error' and event:
            sender.fault = 'fixture_sender_error'
        if scenario == 'audit_error' and event:
            audit.fail('fixture_audit_error')
        d_source.publish(d)
        s_source.publish(s)
        row = controller.tick(emergency_stop=scenario == 'manual' and event)
        samples.append({'tick_seq': row.tick_seq, 'now_ns': clock.ns, 'driver_fixture': asdict(d),
                        'signs_fixture': asdict(s), 'emergency_stop': scenario == 'manual' and event,
                        'scope': 'fixture, not new model inference'})
    rows = audit.drain()
    candidates = [r for r in rows if r.decision.route_meta.candidate_issued]
    firsts = [r for r in candidates if not r.decision.route_meta.reused]
    order = [r.decision.route_meta.row_index for r in firsts]
    terminal = rows[-1].decision.route_meta.status
    abort_ticks = [r.tick_seq for r in rows if r.decision.route_meta.status == 'aborted']
    no_resume = not abort_ticks or all(r.decision.stop for r in rows if r.tick_seq >= abort_ticks[0])
    complete = scenario in ('complete', 'directions')
    eof = next((r for r in rows if r.decision.route_meta.status == 'completed'), None)
    valid = ((terminal == 'completed' and order == list(range(ROW_COUNT)) and eof is not None)
             if complete else (terminal == 'aborted' and bool(abort_ticks) and no_resume))
    summary = {'schema': 'fixed_left_offline_replay_v1', 'status': '已验证' if valid else '失败',
               'date': datetime.now(timezone.utc).isoformat(), 'scope': SCOPE,
               'fixture': True, 'scenario': scenario, 'tick_hz': tick_hz,
               'sender': 'controlled_fixture_memory_sink' if memory_sink else 'preview_only',
               'nominal_hz': 10, 'nominal_period_ns': PERIOD_NS, 'nominal_duration_ns': ROW_COUNT * PERIOD_NS,
               'startup_phase': 'row0 at t0; unlike old sleep-before-each-row; no physical timing claim',
               'timing_note': 'first observed candidates quantized by control ticks; nominal due is NOT exact physical hold',
               'max_lateness_s': max_lateness_s, 'max_raw_speed': max_raw_speed,
               'parameter_scope': 'diagnostic only, NOT vehicle safety values', 'config': asdict(config),
               'route_source': route.source, 'route_sha256': route.sha256,
               'ticks': len(rows), 'unique_row_coverage': sorted(set(order)), 'first_observed_order': order,
               'row_order_ok': order == list(range(len(order))),
               'repeat_count': sum(r.decision.route_meta.reused for r in candidates),
               'first_observed_lateness_ns': [r.decision.route_meta.lateness_ns for r in firsts],
               'first_observed_lateness_max_ns': max((r.decision.route_meta.lateness_ns for r in firsts), default=None),
               'eof_stop_now_ns': None if eof is None else eof.decision.route_meta.now_ns,
               'eof_stop_elapsed_ns': None if eof is None else eof.decision.route_meta.elapsed_ns,
               'terminal_status': terminal, 'first_abort_tick': None if not abort_ticks else abort_ticks[0],
               'abort_reason': rows[-1].decision.route_meta.abort_reason,
               'no_resume_after_abort': no_resume, 'reasons': dict(Counter(r.decision.reason for r in rows)),
               'memory_frame_count': 0 if sender.sink is None else len(sender.sink.frames),
               'driver_fixture_distinct_seq': len({r.driver.source_seq for r in rows}),
               'actual_new_inference_count': 0, 'physical_write_count': 0,
               'eof_stop_required': True, 'no_driver_handback': True,
               'row_coverage_scope': 'on-time generated candidates ONLY, not per-row physical writes; real latest mailbox may overwrite pending speed',
               'start_contract': 'start accepts at t0 without emitting; caller must tick at t0; a missed first slot aborts',
               'source_sha256': {str(p.relative_to(PROJECT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in [PROJECT / 'ps/control_v2/fixed_left.py', Path(__file__)]}}
    return rows, samples, summary


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', action='store_true', required=True, help='synthetic snapshots + virtual clock only')
    parser.add_argument('--tick-hz', type=int, choices=(10, 15, 50), required=True)
    parser.add_argument('--max-raw-speed', type=float, required=True, help='explicit diagnostic raw bound, NOT safe vehicle speed')
    parser.add_argument('--max-lateness-s', type=float, default=.075, help='diagnostic default .075; NOT a vehicle safety value')
    parser.add_argument('--scenario', choices=SCENARIOS, default='complete')
    parser.add_argument('--memory-sink', action='store_true', help='fake writes only to in-process MemorySink; default preview')
    parser.add_argument('--out-dir', type=Path, required=True, help='new directory only; overwrite refused')
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.out_dir.mkdir(parents=True, exist_ok=False)
    except OSError as error:
        print(f'fixed_left_replay: {type(error).__name__}: {error}', file=sys.stderr)
        return 1
    try:
        rows, samples, summary = replay_fixture(tick_hz=args.tick_hz, max_raw_speed=args.max_raw_speed,
                                               max_lateness_s=args.max_lateness_s, scenario=args.scenario,
                                               memory_sink=args.memory_sink)
        write_audit(args.out_dir / 'audit.jsonl', rows)
        with (args.out_dir / 'fixture_frames.jsonl').open('x', encoding='utf-8', newline='\n') as stream:
            for sample in samples:
                stream.write(json.dumps(sample, ensure_ascii=False, allow_nan=False) + '\n')
        _dump(args.out_dir / 'summary.json', summary)
        print(json.dumps({'status': summary['status'], 'terminal_status': summary['terminal_status'],
                          'ticks': summary['ticks'], 'rows': len(summary['unique_row_coverage']),
                          'scope': SCOPE, 'out_dir': str(args.out_dir)}, ensure_ascii=False))
        return 0 if summary['status'] == '已验证' else 1
    except Exception as error:
        _dump(args.out_dir / 'failure.json', {'status': '失败', 'scope': SCOPE,
                                            'first_error': f'{type(error).__name__}: {error}'})
        print(f'fixed_left_replay: {type(error).__name__}: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
