"""Offline-only immutable values. No device or inference session imports."""
from dataclasses import dataclass
import math
from numbers import Real

from ps.roadsign import CLASS_NAMES, Detection

# The historical parser has an eleventh cone slot; the pinned model has ten classes.
MODEL_CLASS_NAMES = CLASS_NAMES[:10]


def finite(value, name):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def integer(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")


@dataclass(frozen=True)
class PolicyConfig:
    max_driver_age_s: float
    max_sign_age_s: float
    confirm_frames: int
    pedestrian_clear_frames: int
    crosswalk_clear_frames: int
    crosswalk_hold_s: float
    speed_limit_factor: float
    command_ttl_s: float
    confirmation_max_gap_s: float

    def __post_init__(self):
        for name in ("max_driver_age_s", "max_sign_age_s", "crosswalk_hold_s",
                     "command_ttl_s", "confirmation_max_gap_s"):
            if finite(getattr(self, name), name) <= 0:
                raise ValueError(f"{name} must be positive")
        for name in ("confirm_frames", "pedestrian_clear_frames", "crosswalk_clear_frames"):
            integer(getattr(self, name), name, 1)
        if not 0 < finite(self.speed_limit_factor, "speed_limit_factor") <= 1:
            raise ValueError("speed_limit_factor must be in (0, 1]")


@dataclass(frozen=True)
class ResultSnapshot:
    generation: int
    frame_seq: int
    grabbed: float
    completed: float
    healthy: bool
    error: str | None

    def __post_init__(self):
        integer(self.generation, "generation", 1)
        integer(self.frame_seq, "frame_seq")
        if finite(self.grabbed, "grabbed") < 0 or finite(self.completed, "completed") < self.grabbed:
            raise ValueError("result timestamps must satisfy 0 <= grabbed <= completed")
        if type(self.healthy) is not bool:
            raise ValueError("healthy must be bool")
        if self.healthy and self.error is not None:
            raise ValueError("healthy result cannot carry an error")
        if not self.healthy and (not isinstance(self.error, str) or not self.error.strip()):
            raise ValueError("unhealthy result requires an error")


@dataclass(frozen=True)
class DriverSnapshot(ResultSnapshot):
    left: float | None
    right: float | None

    def __post_init__(self):
        super().__post_init__()
        if self.healthy:
            finite(self.left, "driver.left")
            finite(self.right, "driver.right")
        elif self.left is not None or self.right is not None:
            raise ValueError("unhealthy driver must not carry cached wheel values")


@dataclass(frozen=True)
class SignSnapshot(ResultSnapshot):
    detections: tuple[Detection, ...]

    def __post_init__(self):
        super().__post_init__()
        if type(self.detections) is not tuple:
            raise ValueError("detections must be an immutable tuple")
        for item in self.detections:
            if not isinstance(item, Detection):
                raise ValueError("detections must contain accepted Detection values")
            integer(item.class_id, "class_id")
            if item.class_id >= len(MODEL_CLASS_NAMES) or item.class_name != MODEL_CLASS_NAMES[item.class_id]:
                raise ValueError("detection does not match the ten-class model mapping")
            if not 0 <= finite(item.confidence, "confidence") <= 1:
                raise ValueError("confidence must be in [0, 1]")
            if finite(item.width, "width") <= 0 or finite(item.height, "height") <= 0:
                raise ValueError("detection size must be positive")


@dataclass(frozen=True)
class RouteIntent:
    generation: int
    intent_seq: int
    direction: str
    source_seq: int


@dataclass(frozen=True)
class PolicyState:
    generation: int
    fault: str | None
    emergency: bool
    red: bool
    pedestrian: bool
    limited: bool
    crosswalk_until: float | None
    crosswalk_armed: bool
    junction: RouteIntent | None


@dataclass(frozen=True)
class Decision:
    left: float
    right: float
    stop: bool
    reason: str
    state: PolicyState
