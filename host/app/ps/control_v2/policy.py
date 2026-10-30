"""Pure offline control decision. This module cannot execute a route or write bytes."""
from .snapshots import (Decision, DriverSnapshot, PolicyState, RouteIntent, SignSnapshot, finite, integer)


class ControlPolicy:
    def __init__(self, config, *, generation):
        self.config = config
        integer(generation, "generation", 1)
        self.generation = generation
        self._initialize()

    def _initialize(self):
        self._fault = None
        self._emergency = False
        self._last_now = None
        self._last = {}
        self._counts = {}
        self._sign_grabbed = None
        self._red = False
        self._pedestrian = False
        self._limited = False
        self._crosswalk_until = None
        self._crosswalk_armed = True
        self._junction = None
        self._route_seq = 0
        self._junction_armed = True

    def reset(self, new_generation):
        integer(new_generation, "new_generation", 1)
        if new_generation <= self.generation:
            raise ValueError("reset requires a strictly newer generation")
        self.generation = new_generation
        self._initialize()

    @property
    def state(self):
        return PolicyState(self.generation, self._fault, self._emergency,
                           self._red, self._pedestrian, self._limited, self._crosswalk_until,
                           self._crosswalk_armed, self._junction)

    def complete_route(self, generation, intent_seq):
        integer(generation, "generation", 1)
        integer(intent_seq, "intent_seq", 1)
        if self._junction is None or (generation, intent_seq) != (self.generation, self._junction.intent_seq):
            raise ValueError("completion must match the active generation and route intent")
        self._junction = None
        self._junction_armed = False
        self._counts["junction_clear"] = 0
        for key in ("left", "right", "conflict"):
            self._counts[key] = 0

    def _count(self, key, present):
        self._counts[key] = self._counts.get(key, 0) + 1 if present else 0
        return self._counts[key]

    def _observe(self, signs, now):
        if self._sign_grabbed is not None and signs.grabbed - self._sign_grabbed > self.config.confirmation_max_gap_s:
            self._counts.clear()
        self._sign_grabbed = signs.grabbed
        ids = {item.class_id for item in signs.detections}
        # Accepted red/pedestrian observations stop immediately, not after confirmation.
        if 6 in ids:
            self._red = True
        if self._count("green", 5 in ids and 6 not in ids) >= self.config.confirm_frames:
            self._red = False
        if 9 in ids:
            self._pedestrian = True
        if self._count("pedestrian_clear", 9 not in ids) >= self.config.pedestrian_clear_frames:
            self._pedestrian = False
        if self._count("limit_on", 3 in ids) >= self.config.confirm_frames or (3 in ids and 4 in ids):
            self._limited = True
        if self._count("limit_off", 4 in ids and 3 not in ids) >= self.config.confirm_frames:
            self._limited = False

        clear = self._count("crosswalk_clear", 2 not in ids)
        if clear >= self.config.crosswalk_clear_frames and (self._crosswalk_until is None or now >= self._crosswalk_until):
            self._crosswalk_armed = True
        if self._count("crosswalk", 2 in ids) >= self.config.confirm_frames and self._crosswalk_armed:
            self._crosswalk_until = now + self.config.crosswalk_hold_s
            self._crosswalk_armed = False
            self._counts["crosswalk_clear"] = 0

        junction_clear = self._count("junction_clear", 0 not in ids and 1 not in ids)
        if junction_clear >= self.config.confirm_frames and self._junction is None:
            self._junction_armed = True
        left = self._count("left", 0 in ids and 1 not in ids)
        right = self._count("right", 1 in ids and 0 not in ids)
        conflict = self._count("conflict", 0 in ids and 1 in ids)
        # Simultaneous route signs stop immediately; no arbitrary default branch.
        if conflict:
            if self._junction is None or self._junction.direction != "conflict":
                self._route_seq += 1
                self._junction = RouteIntent(self.generation, self._route_seq, "conflict", signs.frame_seq)
                self._junction_armed = False
        elif self._junction is None and self._junction_armed and max(left, right) >= self.config.confirm_frames:
            self._route_seq += 1
            self._junction = RouteIntent(self.generation, self._route_seq, "left" if left > right else "right", signs.frame_seq)
            self._junction_armed = False

    def _validate(self, name, value, expected, now, max_age):
        if value is None:
            if name in self._last:
                raise ValueError(name + "_missing")
            return False
        if not isinstance(value, expected):
            raise ValueError(name + "_snapshot_type")
        if value.generation != self.generation:
            raise ValueError(name + "_generation")
        if value.completed > now or value.grabbed > now:
            raise ValueError(name + "_future")
        if not value.healthy:
            raise ValueError(name + "_unhealthy:" + value.error)
        if now - value.grabbed > max_age:
            raise ValueError(name + "_stale")
        previous = self._last.get(name)
        if previous is not None:
            if value.frame_seq < previous.frame_seq:
                raise ValueError(name + "_out_of_order")
            if value.frame_seq == previous.frame_seq:
                if value != previous:
                    raise ValueError(name + "_changed_same_seq")
                return False
            if value.grabbed <= previous.grabbed or value.completed < previous.completed:
                raise ValueError(name + "_time_order")
        if name == "driver" and not (0 <= value.left <= 999 and 0 <= value.right <= 999):
            raise ValueError("driver_illegal_speed")
        self._last[name] = value
        return True

    def step(self, now, driver, signs, *, emergency_stop=False, external_fault=None):
        if type(emergency_stop) is not bool:
            self._fault = self._fault or "invalid_emergency"
        elif emergency_stop:
            self._emergency = True
        if external_fault is not None:
            self._fault = self._fault or "external:" + str(external_fault)
        try:
            now = finite(now, "now")
            if now < 0 or (self._last_now is not None and now < self._last_now):
                raise ValueError("clock_out_of_order")
            self._last_now = now
            self._validate("driver", driver, DriverSnapshot, now, self.config.max_driver_age_s)
            new_signs = self._validate("signs", signs, SignSnapshot, now, self.config.max_sign_age_s)
            if new_signs and not self._fault:
                self._observe(signs, now)
        except (ValueError, TypeError, AttributeError) as error:
            self._fault = self._fault or str(error)
        if self._emergency:
            return Decision(0., 0., True, "emergency_stop", self.state)
        if self._fault:
            return Decision(0., 0., True, "fault:" + self._fault, self.state)
        if driver is None or signs is None:
            return Decision(0., 0., True, "waiting_results", self.state)
        reason = None
        if self._red:
            reason = "red_light"
        elif self._pedestrian:
            reason = "pedestrian"
        elif self._junction:
            reason = "junction_conflict" if self._junction.direction == "conflict" else "junction_route_unavailable"
        elif self._crosswalk_until is not None and now < self._crosswalk_until:
            reason = "crosswalk"
        if reason:
            return Decision(0., 0., True, reason, self.state)
        factor = self.config.speed_limit_factor if self._limited else 1.0
        return Decision(float(driver.left) * factor, float(driver.right) * factor,
                        False, "speed_limit" if self._limited else "driver", self.state)
