"""Opt-in fixed full-left candidate, offline only; never imports device modules.

The 100 ms slots are nominal CSV time, not measured motion. Caller-supplied raw
limits/lateness are diagnostic constraints, not validated vehicle safety values.
"""
from dataclasses import dataclass, replace
import csv
from decimal import Decimal, ROUND_HALF_EVEN
import hashlib
import io
from pathlib import Path

from .policy import ControlPolicy
from .snapshots import Decision, DriverSnapshot, SignSnapshot, finite, integer

LEFT_SHA256 = '031339b47d8a7254aceadb846a1253dbb6b28e9b084393666f86a466d13b96bb'
LEFT_SOURCE = Path(__file__).resolve().parents[2] / 'assets/fixed_left/left.csv'
PERIOD_NS = 100_000_000
ROW_COUNT = 98


@dataclass(frozen=True)
class RouteRow:
    references: tuple[str, str, str]
    left: float
    right: float


def _raw_limit(value):
    value = finite(value, 'max_raw_speed')
    if not 0 < value <= 999:
        raise ValueError('max_raw_speed must be explicit and in (0, 999]; not a safety certification')
    return value


def _parse_csv_bytes(raw, *, expected_sha, expected_rows, max_raw_speed):
    """Pure parser test seam. Only FrozenRoute.load binds an executable route."""
    limit = _raw_limit(max_raw_speed)
    if hashlib.sha256(raw).hexdigest() != expected_sha:
        raise ValueError('route_sha256_mismatch')
    rows = []
    for cells in csv.reader(io.StringIO(raw.decode('utf-8'), newline=''), strict=True):
        if not cells:
            continue
        if len(cells) != 5 or any(not cell.strip() for cell in cells):
            raise ValueError('route requires five nonempty columns')
        left, right = (finite(float(cell), 'route speed') for cell in cells[3:])
        if not (0 <= left <= 999 and 0 <= right <= 999):
            raise ValueError('route raw speed outside encoding range')
        if max(left, right) > limit:
            raise ValueError('route exceeds explicit max_raw_speed')
        rows.append(RouteRow(tuple(cells[:3]), left, right))
    if len(rows) != expected_rows:
        raise ValueError('route_row_count')
    return tuple(rows)


@dataclass(frozen=True, init=False)
class FrozenRoute:
    rows: tuple[RouteRow, ...]
    source: str
    sha256: str
    max_raw_speed: float

    @classmethod
    def load(cls, *, max_raw_speed):
        """Read only the pinned original left.csv; no alternate path/SHA/branch."""
        limit = _raw_limit(max_raw_speed)
        raw = LEFT_SOURCE.read_bytes()
        rows = _parse_csv_bytes(raw, expected_sha=LEFT_SHA256,
                                expected_rows=ROW_COUNT, max_raw_speed=limit)
        route = object.__new__(cls)
        for key, value in (('rows', rows), ('source', str(LEFT_SOURCE)),
                           ('sha256', LEFT_SHA256), ('max_raw_speed', limit)):
            object.__setattr__(route, key, value)
        return route


def _time_ns(now):
    value = finite(now, 'now')
    if value < 0:
        raise ValueError('clock_out_of_order')
    # Quantize absolute times once, then use integer subtraction/division. In
    # particular, do not floor((19.7 - 10.) / .1) in binary floating point.
    return int((Decimal(str(value)) * 1_000_000_000).to_integral_value(rounding=ROUND_HALF_EVEN))


class FixedLeftPlayer:
    """Nonblocking, one candidate per call; no sleep, queue, catch-up or restart."""
    def __init__(self, route, *, generation, max_lateness_s):
        if type(route) is not FrozenRoute or route.sha256 != LEFT_SHA256 or len(route.rows) != ROW_COUNT:
            raise ValueError('only a loaded frozen left route is allowed')
        integer(generation, 'generation', 1)
        lateness = finite(max_lateness_s, 'max_lateness_s')
        if not 0 < lateness < .1:
            raise ValueError('max_lateness_s must be explicit and in (0, .1)')
        self.max_lateness_ns = _time_ns(lateness)
        if not 0 < self.max_lateness_ns < PERIOD_NS:
            raise ValueError('max_lateness_s must be representable in nanoseconds')
        self.route, self.generation = route, generation
        self.status, self.reason = 'disarmed', None
        self.start_attempted = False
        self.start_ns = self.last_now_ns = None
        self.last_now = None
        self.row_index = None

    def observe_time(self, now):
        now_ns = _time_ns(now)
        value = finite(now, 'now')
        if self.last_now is not None and value < self.last_now:
            raise ValueError('clock_out_of_order')
        if self.last_now_ns is not None and now_ns < self.last_now_ns:
            raise ValueError('clock_out_of_order')
        self.last_now, self.last_now_ns = value, now_ns
        return now_ns

    def arm(self, *, generation):
        integer(generation, 'generation', 1)
        if generation != self.generation or self.status != 'disarmed' or self.start_attempted:
            raise ValueError('arm requires this new generation and an unused instance')
        self.status = 'armed'

    def begin(self, now, *, generation, fixed_origin_confirmed):
        if self.start_attempted:
            self.abort('second_start')
            raise ValueError('start is single-use; rebuild the whole chain with a newer generation')
        self.start_attempted = True
        if generation != self.generation or fixed_origin_confirmed is not True or self.status != 'armed':
            self.abort('start_contract')
            raise ValueError('start requires armed new generation and explicit fixed origin/heading confirmation')
        try:
            integer(generation, 'generation', 1)
            self.start_ns = self.observe_time(now)
        except (ValueError, TypeError) as error:
            self.abort(str(error))
            raise
        self.status = 'running'

    def abort(self, reason):
        if self.status not in ('aborted', 'completed'):
            self.status, self.reason = 'aborted', str(reason)

    def sample(self, now):
        """Return (row, reused); invalid clocks are fail-closed ValueErrors."""
        try:
            now_ns = self.observe_time(now)
        except (ValueError, TypeError) as error:
            self.abort(str(error))
            raise
        if self.status != 'running':
            return None, False
        elapsed = now_ns - self.start_ns
        slot = elapsed // PERIOD_NS
        expected = 0 if self.row_index is None else self.row_index + 1
        if slot >= ROW_COUNT:
            if self.row_index != ROW_COUNT - 1:
                self.abort('missed_slot_before_eof')
            elif elapsed - ROW_COUNT * PERIOD_NS > self.max_lateness_ns:
                self.abort('eof_lateness')
            else:
                self.status, self.reason = 'completed', 'eof_stop'
            return None, False
        if self.row_index == slot:
            return self.route.rows[slot], True
        if slot != expected:
            self.abort('missed_slot')
            return None, False
        if elapsed - slot * PERIOD_NS > self.max_lateness_ns:
            self.abort('row_lateness')
            return None, False
        self.row_index = int(slot)
        return self.route.rows[slot], False


@dataclass(frozen=True)
class RouteMeta:
    status: str
    route_source: str
    route_sha256: str
    generation: int
    row_index: int | None
    route_seq: int | None
    nominal_period_ns: int
    start_ns: int | None
    now_ns: int | None
    nominal_due_ns: int | None
    elapsed_ns: int | None
    lateness_ns: int | None
    max_lateness_ns: int
    reused: bool
    candidate_issued: bool
    desired: tuple[float, float] | None
    final: tuple[float, float]
    command_ttl_s: float
    abort_reason: str | None
    start_rejection: str | None
    driver_raw: tuple
    driver_source_seq: int | None
    sign_raw: SignSnapshot | None


@dataclass(frozen=True)
class FixedLeftDecision(Decision):
    route_meta: RouteMeta


class FixedLeftPolicy(ControlPolicy):
    """Safety-veto wrapper using the original snapshot validation/state machine.

    Construct with a fresh generation for policy/sources/sender together. start()
    is a synchronous one-shot request, never an instruction queued behind a hold.
    It emits no command; Controller.step at t0 owns the first row candidate.
    """
    def __init__(self, config, route, *, generation, max_lateness_s):
        super().__init__(config, generation=generation)
        self.player = FixedLeftPlayer(route, generation=generation, max_lateness_s=max_lateness_s)
        self.start_rejection = None

    def _observe(self, signs, now):
        # ControlPolicy.step has ALREADY validated the unmodified entire snapshot
        # into _last['signs']. Filtering here cannot conceal changed_same_seq.
        safety_view = replace(signs, detections=tuple(d for d in signs.detections if d.class_id not in (0, 1)))
        super()._observe(safety_view, now)

    def arm(self, *, generation):
        self.player.arm(generation=generation)

    def reset(self, new_generation):
        raise ValueError('fixed route cannot reset/resume; rebuild the entire chain with a newer generation')

    def complete_route(self, generation, intent_seq):
        raise ValueError('fixed route EOF stays stopped; no handback to driver')

    def abort(self, reason='manual_abort'):
        self.player.abort(reason)

    def start(self, now, driver, signs, *, generation, fixed_origin_confirmed):
        try:
            self.player.begin(now, generation=generation, fixed_origin_confirmed=fixed_origin_confirmed)
        except (ValueError, TypeError):
            self.player.abort('start_contract')
            raise
        # Full original snapshots are checked at the request instant. Unsafe
        # requests are consumed and cannot become a delayed start after clearing.
        safe = super().step(now, driver, signs)
        reason = safe.reason if safe.stop else ('speed_limit' if safe.state.limited else None)
        if reason is not None:
            self.start_rejection = reason
            self.player.abort('start_rejected:' + reason)
            return False
        return True

    def _decision(self, safe, driver, signs, *, row=None, reused=False, reason=None):
        p = self.player
        index = p.row_index
        now_ns = p.last_now_ns
        due = None if index is None else p.start_ns + index * PERIOD_NS
        desired = None if index is None else (p.route.rows[index].left, p.route.rows[index].right)
        final = (row.left, row.right) if row is not None else (0., 0.)
        meta = RouteMeta(p.status, p.route.source, p.route.sha256, self.generation,
                         index, None if index is None else index + 1, PERIOD_NS,
                         p.start_ns, now_ns, due,
                         None if p.start_ns is None or now_ns is None else now_ns - p.start_ns,
                         None if due is None or now_ns is None else now_ns - due,
                         p.max_lateness_ns, reused, row is not None, desired, final,
                         self.config.command_ttl_s, p.reason if p.status == 'aborted' else None,
                         self.start_rejection,
                         (driver.left, driver.right) if isinstance(driver, DriverSnapshot) else (None, None),
                         driver.frame_seq if isinstance(driver, DriverSnapshot) else None,
                         signs if isinstance(signs, SignSnapshot) else None)
        return FixedLeftDecision(*final, row is None, reason or ('fixed_left' if row else 'route_' + p.status),
                                 safe.state, meta)

    def step(self, now, driver, signs, *, emergency_stop=False, external_fault=None):
        safe = super().step(now, driver, signs, emergency_stop=emergency_stop, external_fault=external_fault)
        try:
            self.player.observe_time(now)
        except (ValueError, TypeError) as error:
            self._fault = self._fault or str(error)
            safe = Decision(0., 0., True, 'fault:' + self._fault, self.state)
        veto = safe.reason if safe.stop else ('speed_limit' if safe.state.limited else None)
        if veto:
            if self.player.status == 'running' or safe.state.fault or safe.state.emergency:
                self.player.abort(veto)
            return self._decision(safe, driver, signs, reason=veto)
        if self.player.status != 'running':
            return self._decision(safe, driver, signs)
        row, reused = self.player.sample(now)
        if self.player.status == 'aborted':
            self._fault = self._fault or 'route:' + self.player.reason
            safe = Decision(0., 0., True, 'fault:' + self._fault, self.state)
            return self._decision(safe, driver, signs, reason=safe.reason)
        return self._decision(safe, driver, signs, row=row, reused=reused)
