"""路标规则与驾驶输出的统一仲裁；不打开相机、串口或 PL。"""
from dataclasses import dataclass


@dataclass(frozen=True)
class ControlDecision:
    left: float
    right: float
    stop: bool
    reason: str
    enter_wait_start: bool = False


class RuleArbiter:
    def __init__(self):
        self.limit_rate = 1.0
        self.is_side_walk = False
        self.side_walk_until = None
        self.is_limit_on = False
        self.is_limit_off = False
        self.is_red_light = False
        self.is_green_light = False
        self.is_allow = True
        self.red_approach_until = None
        self.is_left_turn = False
        self.is_right_turn = False
        self.is_warning = False
        self.finish_until = None

    def reset(self):
        self.__init__()

    def set_replay_direction(self, direction):
        if direction not in ("left", "right"):
            raise ValueError("回放方向必须是 left 或 right")
        self.is_left_turn = direction == "left"
        self.is_right_turn = direction == "right"

    def decide(self, params, left, right, *, now):
        if params["ROADSIGN_pedestrian"]:
            return ControlDecision(0.0, 0.0, True, "pedestrian")
        if self.side_walk_until is not None:
            if now < self.side_walk_until:
                return ControlDecision(0.0, 0.0, True, "side_walk")
            self.side_walk_until = None
        if self.red_approach_until is not None:
            if now < self.red_approach_until:
                return ControlDecision(170.0, 190.0, False, "red_light_approach")
            self.red_approach_until = None
            self.is_red_light = True
            self.is_allow = False
            return ControlDecision(0.0, 0.0, True, "red_light")
        if self.finish_until is not None:
            if now < self.finish_until:
                return ControlDecision(200.0, 200.0, False, "finish")
            self.finish_until = None
            return ControlDecision(0.0, 0.0, True, "finish", enter_wait_start=True)
        if not self.is_limit_on and params["ROADSIGN_limit_on"]:
            self.is_limit_on = True
            self.is_limit_off = False
            self.limit_rate = 0.75
        if not self.is_limit_off and params["ROADSIGN_limit_off"]:
            self.is_limit_off = True
            self.limit_rate = 1.0
        if not self.is_side_walk and params["ROADSIGN_side_walk"]:
            self.is_side_walk = True
            self.side_walk_until = now + 3.0
            return ControlDecision(0.0, 0.0, True, "side_walk")
        if self.is_limit_off and not self.is_red_light and params["ROADSIGN_red_light"]:
            self.red_approach_until = now + 1.15
            return ControlDecision(170.0, 190.0, False, "red_light_approach")
        if not self.is_green_light and self.is_red_light and params["ROADSIGN_green_light"]:
            self.is_green_light = True
            self.is_allow = True
        if not self.is_warning and params["ROADSIGN_warning"]:
            self.is_warning = True
        if not self.is_allow:
            return ControlDecision(0.0, 0.0, True, "red_light")
        if (self.is_warning and (self.is_left_turn or self.is_right_turn)
                and (params["ROADSIGN_left_turn"] or params["ROADSIGN_right_turn"])):
            self.finish_until = now + 1.7
            return ControlDecision(200.0, 200.0, False, "finish")
        reason = "driver_limited" if self.limit_rate != 1.0 else "driver"
        return ControlDecision(left * self.limit_rate, right * self.limit_rate,
                               False, reason)
