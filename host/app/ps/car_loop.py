"""三路主程序的控制逻辑。硬件全部靠注入，这个文件本身不打开相机、串口，不 import pynq。

流程（状态机）：
  WAIT_DEVICES  相机、串口、推理有一样没准备好：什么都不发，每隔 poll_s 再查。
  WAIT_START    设备齐了，等人工启动：什么都不发。启动方式还没定，由 start_trigger 注入。
  RUNNING       每轮取三路最新帧 -> 推理 -> 编帧 -> 发送。
出错处理：
  - 某路帧过期（超过 max_age_s）或相机断开：补发一帧停车帧，回到 WAIT_DEVICES。
  - 推理异常、串口写失败：补发停车帧（能发就发），回到 WAIT_DEVICES。
  - FatalError（PL 核超时卡死）：补发停车帧后整个程序退出，不重试。
  - 速度编不成合法帧（负数、>999、NaN）：这一帧不发，记事件；连续 max_rejects 帧都这样就补发停车帧。
  - 程序退出（正常、Ctrl+C、异常）：如果发过速度，退出前补发一帧停车帧。
每个状态变化、错误、停车帧都写进事件日志（JSONL，一行一个事件，写完立即落盘）。
"""

import json
import os
import time
from datetime import datetime, timezone

from .packet import PacketRejected, encode_side_speed, stop_frame

WAIT_DEVICES, WAIT_START, RUNNING = "WAIT_DEVICES", "WAIT_START", "RUNNING"


class FatalError(RuntimeError):
    """不能恢复的错误（比如 PL 核超时卡死）：补停车帧后整个程序退出，不回到等待重试。"""


class EventLog:
    """事件日志。一行一个 JSON，写完 flush + fsync，断电也尽量不丢。"""

    def __init__(self, path, clock=time.time):
        self.stream = open(path, "a", encoding="utf-8")
        self.clock = clock

    def __call__(self, kind, **data):
        record = {"utc": datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(),
                  "kind": kind, **data}
        self.stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        self.stream.flush()
        os.fsync(self.stream.fileno())

    def close(self):
        self.stream.close()


class CarLoop:
    """把设备检查、启动、推理、发送串起来。

    devices.check() -> None 表示齐了，否则返回原因字符串。
    cameras.latest() -> [(左帧, 抓帧时间), (前帧, ...), (右帧, ...)]，相机断开时抛错。
    infer(left, front, right) -> (左速度, 右速度)。
    link.write(bytes) -> 实际写出的字节数。
    start_trigger() -> True 表示人工已启动。
    stop_request() -> None 表示继续跑，否则返回原因：补停车帧，回到 WAIT_START（手柄停车/急停）。
    on_wait_start() 每次进入 WAIT_START 时调用一次（清掉积压的按键）。
    supervisor 为可选控制仲裁器：evaluate(frames,left,right) 在推理后、编码前调用，
    返回 ControlDecision；进入 WAIT_START 时调用 reset()。缺省为 None，保持原驾驶路径。
    """

    def __init__(self, devices, cameras, infer, link, start_trigger, log,
                 max_age_s=0.2, poll_s=1.0, max_rejects=5, period_s=1 / 15,
                 clock=time.monotonic, sleep=time.sleep,
                 stop_request=lambda: None, on_wait_start=lambda: None, start_poll_s=0.05,
                 supervisor=None):
        self.devices, self.cameras, self.infer, self.link = devices, cameras, infer, link
        self.start_trigger, self.log = start_trigger, log
        self.stop_request, self.on_wait_start, self.start_poll_s = stop_request, on_wait_start, start_poll_s
        self.supervisor = supervisor
        self._last_control_reason = None
        self.max_age_s, self.poll_s, self.max_rejects, self.period_s = max_age_s, poll_s, max_rejects, period_s
        self.clock, self.sleep = clock, sleep
        self.state = None
        self.sent_speed = False      # 发过速度帧之后，退出或出错才需要补停车帧
        self.rejects = 0
        self.stats = {"frames_sent": 0, "frames_rejected": 0, "stop_frames": 0}
        self._enter(WAIT_DEVICES, reason="startup")

    def _enter(self, state, **why):
        if state != self.state:
            self.log("state", old=self.state, new=state, **why)
            self.state = state
            if state == WAIT_START:
                if self.supervisor is not None:
                    self.supervisor.reset()
                    self._last_control_reason = None
                self.on_wait_start()

    def _send(self, frame):
        written = self.link.write(frame)
        if written != len(frame):
            raise IOError(f"serial wrote {written} of {len(frame)} bytes")

    def send_stop(self, reason):
        """补发停车帧。只在发过速度以后才发；发失败也只记日志，不再抛错。"""
        if not self.sent_speed:
            return
        try:
            self._send(stop_frame())
            self.stats["stop_frames"] += 1
            self.sent_speed = False
            self.log("stop_frame", reason=reason)
        except Exception as error:  # noqa: BLE001
            self.log("stop_frame_failed", reason=reason, error=f"{type(error).__name__}: {error}")

    def _fault(self, kind, error):
        self.log(kind, error=f"{type(error).__name__}: {error}" if isinstance(error, BaseException) else error)
        self.send_stop(kind)
        self._enter(WAIT_DEVICES, reason=kind)

    def step(self):
        """跑一轮。返回下一轮前应该等待的秒数。"""
        if self.state == WAIT_DEVICES:
            problem = self.devices.check()
            if problem is None:
                self._enter(WAIT_START, reason="devices ready")
                return 0.0
            if problem != getattr(self, "_last_problem", None):  # 同一个原因只记一次，避免刷屏
                self.log("devices_not_ready", problem=problem)
            self._last_problem = problem
            return self.poll_s
        self._last_problem = None
        if self.state == WAIT_START:
            problem = self.devices.check()
            if problem is not None:
                self._enter(WAIT_DEVICES, reason=problem)
                return self.poll_s
            if self.start_trigger():
                self.rejects = 0
                self._enter(RUNNING, reason="manual start")
                return 0.0
            return self.start_poll_s  # 按键要及时响应，不按 poll_s 等
        return self._run_once()

    def _run_once(self):
        begin = self.clock()
        reason = self.stop_request()
        if reason is not None:
            self.log("manual_stop", reason=reason)
            self.send_stop(reason)
            self._enter(WAIT_START, reason=reason)
            return 0.0
        try:
            frames = self.cameras.latest()
        except Exception as error:  # noqa: BLE001
            self._fault("camera_error", error)
            return self.poll_s
        now = self.clock()
        ages = [now - grabbed for _, grabbed in frames]
        if max(ages) > self.max_age_s:
            self._fault("stale_frame", f"ages_s={[round(a, 3) for a in ages]} limit={self.max_age_s}")
            return self.poll_s
        try:
            left, right = self.infer(*(image for image, _ in frames))
        except FatalError as error:
            self.log("fatal", error=f"{type(error).__name__}: {error}")
            self.send_stop("fatal")
            raise
        except Exception as error:  # noqa: BLE001
            self._fault("inference_error", error)
            return self.poll_s
        if self.supervisor is not None:
            try:
                decision = self.supervisor.evaluate(frames, left, right)
            except Exception as error:  # noqa: BLE001
                self._fault("supervisor_error", error)
                return self.poll_s
            if decision.reason != self._last_control_reason:
                evidence = {}
                frame = getattr(self.supervisor, "last_frame", None)
                if frame is not None:
                    evidence = {
                        "model_sha256": frame.model_sha256,
                        "detector_latency_ms": frame.latency_ms,
                        "detection_age_ms": (self.clock() - frame.grabbed) * 1000,
                        "classes": [item.class_name for item in frame.result.detections],
                    }
                self.log("control_decision", reason=decision.reason, stop=decision.stop,
                         left=decision.left, right=decision.right,
                         enter_wait_start=decision.enter_wait_start, **evidence)
                self._last_control_reason = decision.reason
            if decision.stop:
                try:
                    self._send(stop_frame())
                except Exception as error:  # noqa: BLE001
                    self._fault("serial_error", error)
                    return self.poll_s
                self.rejects = 0
                self.sent_speed = False
                self.stats["stop_frames"] += 1
                if decision.enter_wait_start:
                    self._enter(WAIT_START, reason=decision.reason)
                return max(0.0, self.period_s - (self.clock() - begin))
            left, right = decision.left, decision.right
        try:
            frame = encode_side_speed(left, right)
        except PacketRejected as error:
            self.rejects += 1
            self.stats["frames_rejected"] += 1
            self.log("frame_rejected", error=str(error), consecutive=self.rejects)
            if self.rejects >= self.max_rejects:
                self.send_stop(f"{self.rejects} consecutive rejected frames")
            return max(0.0, self.period_s - (self.clock() - begin))
        try:
            self._send(frame)
        except Exception as error:  # noqa: BLE001
            self._fault("serial_error", error)
            return self.poll_s
        self.rejects = 0
        self.sent_speed = True
        self.stats["frames_sent"] += 1
        return max(0.0, self.period_s - (self.clock() - begin))

    def run(self, should_stop=lambda: False):
        """一直跑到 should_stop() 为真。无论怎么退出，都先补停车帧再记退出事件。"""
        exit_reason = "requested"
        try:
            while not should_stop():
                self.sleep(self.step())
        except BaseException as error:
            exit_reason = f"{type(error).__name__}: {error}"
            raise
        finally:
            self.send_stop(f"exit: {exit_reason}")
            self.log("exit", reason=exit_reason, **self.stats)
