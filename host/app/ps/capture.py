"""三路相机采集层。只开相机，不碰串口和 PL。

每台相机一个后台线程不停取帧，只保留最新一帧和取到它的时间。时间用 time.monotonic，
和 car_loop 判帧龄用的是同一个时钟。

取帧走 OpenCV 的 V4L2 后端，不走 ffmpeg：2026-10-22 在扩展坞上 ffmpeg 抓不到帧，
v4l2-ctl 正常，原因还没查清。

为了省 CPU，线程里只拿 MJPEG 原始字节（CONVERT_RGB=0），主循环要帧时才解码，三路并行。
驱动如果直接给了解码好的 640x480 图，也照样接受。

相机按 USB 口认：resolve() 每次（重）启动都重新把口解析成设备路径，重插后 videoN 变了也能找到。
"""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

WIDTH, HEIGHT, FPS = 640, 480, 30
FRAME_SHAPE = (HEIGHT, WIDTH, 3)
ROLES = ("left", "front", "right")


def open_v4l2(device):
    """打开一台 UVC 相机，设成 MJPG 640x480 30fps，要原始 JPEG 字节。设不上就报错。"""
    import cv2
    cap = cv2.VideoCapture(device, cv2.CAP_V4L2)
    if not cap.isOpened():
        raise IOError(f"cannot open {device}")
    mjpg = cv2.VideoWriter_fourcc(*"MJPG")
    cap.set(cv2.CAP_PROP_FOURCC, mjpg)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
    cap.set(cv2.CAP_PROP_FPS, FPS)
    cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)
    size = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    fourcc = int(cap.get(cv2.CAP_PROP_FOURCC)) & 0xFFFFFFFF
    if size != (WIDTH, HEIGHT) or fourcc != mjpg:
        cap.release()
        raise IOError(f"{device} did not accept MJPG {WIDTH}x{HEIGHT}: got {size} fourcc={fourcc:#x}")
    return cap


def decode(payload):
    """原始 JPEG 字节或已解码的图 -> 640x480 BGR uint8。"""
    if payload.shape == FRAME_SHAPE and payload.dtype == np.uint8:
        return payload
    import cv2
    image = cv2.imdecode(np.ascontiguousarray(payload, dtype=np.uint8).reshape(-1), cv2.IMREAD_COLOR)
    if image is None or image.shape != FRAME_SHAPE:
        raise ValueError(f"frame did not decode to {FRAME_SHAPE}: {None if image is None else image.shape}")
    return image


def resolver_from_config(camera_config):
    """camera_ports.json -> resolve()，按左、前、右返回 by-path 指向的真实设备路径。"""
    root = Path(camera_config["by_path_dir"])

    def resolve():
        return [os.path.realpath(root / camera["by_path"]) for camera in camera_config["cameras"]]
    return resolve


class CameraStream:
    """一台相机的取帧线程。出错后线程结束，error 里是原因，由 TriCamera 决定重启。"""

    def __init__(self, role, device, opener, clock, max_failures):
        self.role, self.device, self.clock = role, device, clock
        self.started = clock()
        self.error = None
        self.frames = 0
        self.failures = 0
        self._item = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(opener, max_failures),
                                        name=f"cam-{role}", daemon=True)
        self._thread.start()

    def _run(self, opener, max_failures):
        cap = None
        try:
            cap = opener(self.device)
            misses = 0
            while not self._stop.is_set():
                ok, payload = cap.read()
                if not ok or payload is None or payload.size == 0:
                    misses += 1
                    self.failures += 1
                    if misses >= max_failures:
                        raise IOError(f"{misses} consecutive failed reads")
                    continue
                misses = 0
                grabbed = self.clock()
                copy = np.array(payload, copy=True)  # 驱动缓冲区会被复用，必须复制
                with self._lock:
                    self.frames += 1
                    self._item = (copy, grabbed, self.frames)
        except Exception as error:  # noqa: BLE001
            self.error = f"{type(error).__name__}: {error}"
        finally:
            if cap is not None:
                try:
                    cap.release()
                except Exception:  # noqa: BLE001
                    pass

    @property
    def alive(self):
        return self._thread.is_alive() and self.error is None

    def snapshot(self):
        with self._lock:
            return self._item

    def stop(self, timeout=2.0):
        self._stop.set()
        self._thread.join(timeout)


class TriCamera:
    """car_loop 的 cameras：latest() -> [(左图, 时间), (前图, 时间), (右图, 时间)]。

    ensure_running() 由设备检查调用：没启动的、线程死了的、超过 restart_age_s 没出新帧的，
    都重新解析口位后重启。返回 None 表示三路都在出帧，否则返回原因。
    """

    def __init__(self, resolve, opener=open_v4l2, clock=time.monotonic,
                 restart_age_s=1.0, start_timeout_s=5.0, max_failures=30):
        self.resolve, self.opener, self.clock = resolve, opener, clock
        self.restart_age_s, self.start_timeout_s, self.max_failures = restart_age_s, start_timeout_s, max_failures
        self.streams = [None, None, None]
        self.restarts = 0
        self._cache = [None, None, None]          # (seq, 图, 抓帧时间)
        self._bad_seq = [None, None, None]
        self._last_decode_error = [None, None, None]
        self.decode_failures = [0, 0, 0]
        self._pool = ThreadPoolExecutor(3, thread_name_prefix="decode")

    def ensure_running(self):
        problems, devices = [], None
        now = self.clock()
        for i, role in enumerate(ROLES):
            stream = self.streams[i]
            if stream is not None and stream.alive:
                item = stream.snapshot()
                if item is not None and now - item[1] <= self.restart_age_s:
                    continue
                if item is None and now - stream.started < self.start_timeout_s:
                    problems.append(f"{role} camera starting")
                    continue
            if stream is not None:
                why = stream.error or ("no frame since start" if stream.snapshot() is None else "frames stopped")
                stream.stop(timeout=0.5)
                self.restarts += 1
                problems.append(f"{role} camera restarted ({why})")
            else:
                problems.append(f"{role} camera starting")
            if devices is None:
                devices = self.resolve()
            self.streams[i] = CameraStream(role, devices[i], self.opener, self.clock, self.max_failures)
            self._cache[i] = None
            self._bad_seq[i] = None
        return "; ".join(problems) or None

    def _decode(self, i, item):
        """返回 (图, 抓帧时间)。

        单帧解不开时不抛错：沿用这一路上一次解好的图和它原来的抓帧时间，所以
        car_loop 的帧龄检查照样有效，坏帧一直来就会过期、补停车帧。
        这一路还从没解出过图时才抛错，报错里带上是哪一路。
        """
        payload, grabbed, seq = item
        cached = self._cache[i]
        if cached is not None and cached[0] == seq:
            return cached[1], cached[2]
        if self._bad_seq[i] == seq:  # 同一帧坏图不重复解、不重复计数
            if cached is None:
                raise IOError(f"{ROLES[i]} camera: {self._last_decode_error[i]}")
            return cached[1], cached[2]
        try:
            image = decode(payload)
        except Exception as error:  # noqa: BLE001
            self._bad_seq[i] = seq
            self.decode_failures[i] += 1
            self._last_decode_error[i] = f"{type(error).__name__}: {error}"
            if cached is None:
                raise IOError(f"{ROLES[i]} camera: {self._last_decode_error[i]}") from error
            return cached[1], cached[2]
        self._cache[i] = (seq, image, grabbed)
        return image, grabbed

    def latest(self):
        items = []
        for role, stream in zip(ROLES, self.streams):
            if stream is None:
                raise IOError(f"{role} camera not started")
            if not stream.alive:
                raise IOError(f"{role} camera stopped: {stream.error}")
            item = stream.snapshot()
            if item is None:
                raise IOError(f"{role} camera has no frame yet")
            items.append(item)
        return list(self._pool.map(self._decode, range(3), items))

    def stats(self):
        return {"restarts": self.restarts, **{
            role: (None if s is None else {"device": s.device, "frames": s.frames,
                                            "failed_reads": s.failures, "error": s.error,
                                            "decode_failures": self.decode_failures[i],
                                            "last_decode_error": self._last_decode_error[i]})
            for i, (role, s) in enumerate(zip(ROLES, self.streams))}}

    def close(self):
        for stream in self.streams:
            if stream is not None:
                stream.stop()
        self._pool.shutdown(wait=True)
