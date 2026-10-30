"""把前视路标检测接入规则仲裁，并强制检测结果新鲜度。"""
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import time

from .control_arbiter import RuleArbiter
from .roadsign import RoadSignONNX


class RoadSignStale(RuntimeError):
    """当前控制周期使用的路标结果已经超过允许年龄。"""


class RoadSignSupervisor:
    def __init__(self, detector, *, max_result_age_s, arbiter=None, clock=time.monotonic):
        if not math.isfinite(max_result_age_s) or max_result_age_s <= 0:
            raise ValueError("max_result_age_s 必须是有限正数")
        self.detector = detector
        self.arbiter = RuleArbiter() if arbiter is None else arbiter
        self.max_result_age_s = max_result_age_s
        self.clock = clock
        self.last_frame = None

    def reset(self):
        self.arbiter.reset()
        self.last_frame = None

    def evaluate(self, frames, left, right):
        self.last_frame = None
        images = [image for image, _ in frames]
        grabbed = [timestamp for _, timestamp in frames]
        frame = self.detector.detect(images[1], grabbed=grabbed[1])
        # last_frame 是本周期检测审计结果；是否允许控车仍由新鲜度门禁决定。
        self.last_frame = frame
        now = self.clock()
        age = now - frame.grabbed
        if not math.isfinite(age) or age < 0 or age > self.max_result_age_s:
            raise RoadSignStale(f"路标结果过期：age_s={age:.6f} limit={self.max_result_age_s:.6f}")
        return self.arbiter.decide(frame.result.params, left, right, now=now)


@dataclass(frozen=True)
class RoadSignConfig:
    model_path: Path
    sha256: str
    max_result_age_s: float
    threads: int


def load_roadsign_config(path):
    path = Path(path).resolve()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:
        raise ValueError(f"路标配置不可读：{error}") from error
    if value.get("schema", "roadsign_supervisor_config_v1") != "roadsign_supervisor_config_v1":
        raise ValueError("路标配置 schema 不受支持")
    model_value = value.get("model")
    expected = value.get("sha256")
    if not isinstance(model_value, str) or not model_value:
        raise ValueError("路标配置缺少 model")
    if not isinstance(expected, str) or len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
        raise ValueError("路标配置需要 64 位小写 SHA256")
    age = value.get("max_result_age_s")
    if type(age) not in (int, float) or not math.isfinite(age) or age <= 0:
        raise ValueError("路标配置需要有限正数 max_result_age_s")
    threads = value.get("threads", 1)
    if type(threads) is not int or threads < 1:
        raise ValueError("路标 threads 必须是正整数")
    model_path = Path(model_value)
    if not model_path.is_absolute():
        model_path = path.parent / model_path
    model_path = model_path.resolve()
    if not model_path.is_file():
        raise ValueError("路标模型不存在：" + str(model_path))
    digest = hashlib.sha256()
    with model_path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 << 20), b""):
            digest.update(block)
    actual = digest.hexdigest()
    if actual != expected:
        raise ValueError(f"路标模型 SHA256 不一致：{actual} != {expected}")
    return RoadSignConfig(model_path, expected, float(age), threads)


def make_roadsign_supervisor(config, *, clock=time.monotonic):
    detector = RoadSignONNX(config.model_path, expected_sha256=config.sha256,
                            threads=config.threads, clock=clock)
    return RoadSignSupervisor(detector, max_result_age_s=config.max_result_age_s, clock=clock)
