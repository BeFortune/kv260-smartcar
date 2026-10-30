"""CPU-only spawn workers and a single-owner offline array source.

LatestSlot is one shared-memory cell, not a queue with a feeder backlog. Publishing
copies under a short lock outside control tick; reads try the lock once and retain
the previous snapshot on contention. No live inference session crosses processes.
"""
from dataclasses import dataclass
import hashlib
import multiprocessing as mp
import os
from pathlib import Path
import pickle
import threading
import time

import numpy as np

from .snapshots import DriverSnapshot, SignSnapshot, finite, integer


class LatestSlot:
    def __init__(self, context, *, capacity_bytes):
        integer(capacity_bytes, "capacity_bytes", 1)
        self.capacity = capacity_bytes
        self.buffer = context.RawArray("B", capacity_bytes)
        self.length = context.RawValue("Q", 0)
        self.version = context.RawValue("Q", 0)
        self.lock = context.Lock()

    def publish(self, value):
        payload = pickle.dumps(value, protocol=5)
        if len(payload) > self.capacity:
            raise ValueError("latest slot capacity exceeded")
        with self.lock:
            self.buffer[:len(payload)] = payload
            self.length.value = len(payload)
            self.version.value += 1
        return True

    def read_after(self, version):
        if not self.lock.acquire(False):
            return None
        try:
            if self.version.value <= version or not self.length.value:
                return None
            current = self.version.value
            payload = bytes(self.buffer[:self.length.value])
        finally:
            self.lock.release()
        return current, pickle.loads(payload)


@dataclass(frozen=True)
class ArrayFrame:
    generation: int
    frame_seq: int
    grabbed: float
    images: tuple

    def __post_init__(self):
        integer(self.generation, "generation", 1)
        integer(self.frame_seq, "frame_seq")
        if finite(self.grabbed, "grabbed") < 0:
            raise ValueError("grabbed must be nonnegative")
        if type(self.images) is not tuple or len(self.images) != 3:
            raise ValueError("expected an immutable left/front/right array triplet")
        for image in self.images:
            if not isinstance(image, np.ndarray) or image.shape != (480, 640, 3) or image.dtype != np.uint8:
                raise ValueError("offline frame must be three 640x480 uint8 BGR arrays")
            if image.flags.writeable:
                raise ValueError("offline source must own immutable array copies")


class OfflineArraySource:
    """One thread owns publication. grabbed is the oldest triplet capture timestamp."""
    def __init__(self, generation, workers):
        integer(generation, "generation", 1)
        self.generation = generation
        self.workers = tuple(workers)
        if any(worker.generation != generation for worker in self.workers):
            raise ValueError("source and workers must have the same generation")
        self.owner = threading.get_ident()
        self.seq = 0
        self.last_grabbed = None

    def publish(self, images, *, grabbed):
        if threading.get_ident() != self.owner:
            raise ValueError("offline source has a single owner")
        grabbed = finite(grabbed, "grabbed")
        if grabbed < 0 or (self.last_grabbed is not None and grabbed <= self.last_grabbed):
            raise ValueError("capture timestamps must increase")
        if len(images) != 3:
            raise ValueError("expected left/front/right arrays")
        copies = []
        for image in images:
            if not isinstance(image, np.ndarray) or image.shape != (480, 640, 3) or image.dtype != np.uint8:
                raise ValueError("expected 640x480 uint8 BGR")
            # A bytes owner prevents the caller from re-enabling write access.
            copies.append(np.frombuffer(image.tobytes(order="C"), dtype=np.uint8).reshape(image.shape))
        frame = ArrayFrame(self.generation, self.seq + 1, grabbed, tuple(copies))
        self.seq += 1
        self.last_grabbed = grabbed
        for worker in self.workers:
            worker.submit(frame)
        return frame


@dataclass(frozen=True)
class CPUFactory:
    model_path: str
    expected_sha256: str
    threads: int

    def __post_init__(self):
        if not isinstance(self.model_path, str) or not self.model_path:
            raise ValueError("model_path must be explicit")
        if len(self.expected_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.expected_sha256):
            raise ValueError("expected_sha256 must be lowercase SHA256")
        integer(self.threads, "threads", 1)


@dataclass(frozen=True)
class FixtureFactory:
    """Explicit synthetic fixture, never a trained YOLO result or a speed benchmark."""
    left: float = 200.0
    right: float = 100.0
    detections: tuple = ()
    entered: object = None
    release: object = None
    fail: str | None = None


@dataclass(frozen=True)
class JoinOutcome:
    stopped: bool
    alive: bool
    reason: str


def _build(factory, role):
    if isinstance(factory, FixtureFactory):
        return None
    digest = hashlib.sha256(Path(factory.model_path).read_bytes()).hexdigest()
    if digest != factory.expected_sha256:
        raise ValueError("CPU model SHA256 mismatch")
    if role == "driver":
        from ps.cpu_infer import CpuOriginalInfer
        return CpuOriginalInfer(factory.model_path, threads=factory.threads)
    from ps.roadsign import RoadSignONNX
    return RoadSignONNX(factory.model_path, expected_sha256=factory.expected_sha256, threads=factory.threads)


def _snapshot(role, frame, *, healthy, error, values=None):
    completed = time.monotonic()
    if role == "driver":
        return DriverSnapshot(frame.generation, frame.frame_seq, frame.grabbed,
                              completed, healthy, error, *(values if healthy else (None, None)))
    return SignSnapshot(frame.generation, frame.frame_seq, frame.grabbed,
                        completed, healthy, error, values if healthy else ())


def _worker_main(role, factory, generation, inputs, outputs, stop, ready):
    last_version = 0
    initialization_error = None
    try:
        model = _build(factory, role)
    except Exception as error:
        model = None
        initialization_error = f"{type(error).__name__}: {error}"
    ready.set()
    while not stop.is_set():
        incoming = inputs.read_after(last_version)
        if incoming is None:
            stop.wait(.005)
            continue
        last_version, frame = incoming
        if frame.generation != generation:
            continue
        try:
            if initialization_error:
                raise RuntimeError(initialization_error)
            if isinstance(factory, FixtureFactory):
                if factory.entered is not None:
                    factory.entered.set()
                if factory.release is not None:
                    # Stop cannot cancel an in-flight inference; test releases it explicitly.
                    factory.release.wait()
                if factory.fail:
                    raise RuntimeError(factory.fail)
                values = (factory.left, factory.right) if role == "driver" else factory.detections
            elif role == "driver":
                values = model(*frame.images)
            else:
                values = model.detect(frame.images[1], grabbed=frame.grabbed).result.detections
            result = _snapshot(role, frame, healthy=True, error=None, values=values)
        except Exception as error:
            result = _snapshot(role, frame, healthy=False, error=f"{type(error).__name__}: {error}")
        outputs.publish(result)
        if not result.healthy:
            # Do not hide an error behind a later successful latest-result replacement.
            stop.wait()
            break


class SpawnWorker:
    def __init__(self, role, factory, *, generation):
        if role not in ("driver", "signs"):
            raise ValueError("role must be driver or signs")
        if type(factory) not in (CPUFactory, FixtureFactory):
            raise ValueError("only inert CPUFactory/FixtureFactory configs; no live sessions or PYNQ objects")
        integer(generation, "generation", 1)
        context = mp.get_context("spawn")
        self.role, self.generation = role, generation
        self.inputs = LatestSlot(context, capacity_bytes=3 * 480 * 640 * 3 + 16384)
        self.outputs = LatestSlot(context, capacity_bytes=1 << 20)
        self.stop = context.Event()
        self.ready = context.Event()
        self.process = context.Process(target=_worker_main, args=(role, factory, generation,
                    self.inputs, self.outputs, self.stop, self.ready), name="control_v2_" + role)
        self.started = False
        self._version = 0
        self._cached = None
        self._last_frame = None

    def start(self):
        if self.started:
            raise ValueError("worker cannot be restarted; create a new generation")
        self.process.start()
        self.started = True

    @property
    def pid(self):
        return self.process.pid

    @property
    def is_alive(self):
        return self.started and self.process.is_alive()

    def submit(self, frame):
        if not isinstance(frame, ArrayFrame) or frame.generation != self.generation:
            raise ValueError("wrong generation or non-array frame")
        if not self.started or self.stop.is_set():
            raise ValueError("worker not running or stop requested")
        if self._last_frame is not None and (frame.frame_seq <= self._last_frame.frame_seq or frame.grabbed <= self._last_frame.grabbed):
            raise ValueError("frame publication must be ordered")
        self.inputs.publish(frame)
        self._last_frame = frame

    def snapshot(self):
        update = self.outputs.read_after(self._version)
        if update is not None:
            self._version, self._cached = update
        if self.started and self.process.exitcode is not None and not self.stop.is_set() and self._last_frame is not None:
            frame = self._last_frame
            if self._cached is None or self._cached.healthy:
                self._cached = _snapshot(self.role, frame, healthy=False, error="worker_exited")
        return self._cached

    def request_stop(self):
        self.stop.set()

    def join(self, timeout_s):
        if finite(timeout_s, "timeout_s") < 0:
            raise ValueError("join timeout must be nonnegative")
        if not self.started:
            return JoinOutcome(True, False, "not_started")
        self.process.join(timeout_s)
        alive = self.process.is_alive()
        return JoinOutcome(not alive, alive, "inference_not_cancelled_worker_not_stopped" if alive else "worker_exited")
