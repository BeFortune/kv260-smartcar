from pathlib import Path
import hashlib
import multiprocessing as mp
import sys
import time

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ps.control_v2.workers import (CPUFactory, FixtureFactory, LatestSlot,
                                   OfflineArraySource, SpawnWorker)


def wait_for(predicate, timeout=12.):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.005)
    raise AssertionError("bounded wait expired")


def arrays(value=10):
    return tuple(np.full((480, 640, 3), value, dtype=np.uint8) for _ in range(3))


def test_latest_slot_retains_only_newest_and_reads_never_wait_on_writer():
    slot = LatestSlot(mp.get_context("spawn"), capacity_bytes=4096)
    for seq in range(100):
        assert slot.publish(seq)
    assert slot.read_after(0)[1] == 99
    version = slot.read_after(0)[0]
    assert slot.read_after(version) is None
    slot.lock.acquire()
    try:
        started = time.monotonic()
        assert slot.read_after(0) is None
        assert time.monotonic() - started < .2
    finally:
        slot.lock.release()


def test_real_spawn_blocking_inference_latest_frame_and_bounded_shutdown():
    ctx = mp.get_context("spawn")
    entered, release = ctx.Event(), ctx.Event()
    worker = SpawnWorker("driver", FixtureFactory(entered=entered, release=release), generation=1)
    worker.start()
    source = OfflineArraySource(1, (worker,))
    images = arrays(10)
    try:
        first = source.publish(images, grabbed=time.monotonic())
        assert entered.wait(12.)
        images[0][:] = 99
        assert first.images[0][0, 0, 0] == 10
        with pytest.raises(ValueError):
            first.images[0].setflags(write=True)
        started = time.monotonic()
        for _ in range(100):
            assert worker.snapshot() is None
        assert time.monotonic() - started < .5
        for _ in range(4):
            last = source.publish(arrays(), grabbed=time.monotonic())
        release.set()
        result = wait_for(lambda: (s if (s := worker.snapshot()) is not None and s.frame_seq == last.frame_seq else None))
        assert result.healthy and result.frame_seq == 5
        assert result.generation == 1
    finally:
        release.set()
        worker.request_stop()
        assert worker.join(12.).stopped
    assert not worker.is_alive


def test_join_timeout_is_not_claimed_as_stopped_and_does_not_kill():
    ctx = mp.get_context("spawn")
    entered, release = ctx.Event(), ctx.Event()
    worker = SpawnWorker("signs", FixtureFactory(entered=entered, release=release), generation=3)
    worker.start()
    try:
        OfflineArraySource(3, (worker,)).publish(arrays(), grabbed=time.monotonic())
        assert entered.wait(12.)
        worker.request_stop()
        outcome = worker.join(.02)
        assert not outcome.stopped and outcome.alive
        assert worker.is_alive
        assert "not_cancelled" in outcome.reason
    finally:
        release.set()
        assert worker.join(12.).stopped


def test_real_cpu_onnx_factory_initializes_in_child_and_uses_old_constant_fixture():
    root = Path(__file__).resolve().parents[1]
    model = root / "artifacts/roadsign_migration_20261027/run01/fixtures/constant_pedestrian.onnx"
    factory = CPUFactory(str(model), hashlib.sha256(model.read_bytes()).hexdigest(), threads=1)
    worker = SpawnWorker("signs", factory, generation=1)
    worker.start()
    try:
        source = OfflineArraySource(1, (worker,))
        source.publish(arrays(), grabbed=time.monotonic())
        result = wait_for(worker.snapshot)
        assert result.healthy, result.error
        assert {d.class_id for d in result.detections} == {9}
        assert worker.pid != __import__("os").getpid()
    finally:
        worker.request_stop()
        assert worker.join(12.).stopped


def test_spawn_worker_error_remains_visible_instead_of_being_overwritten():
    worker = SpawnWorker("driver", FixtureFactory(fail="fixture inference failed"), generation=1)
    worker.start()
    try:
        source = OfflineArraySource(1, (worker,))
        source.publish(arrays(), grabbed=time.monotonic())
        result = wait_for(worker.snapshot)
        assert not result.healthy and "fixture inference failed" in result.error
        source.publish(arrays(), grabbed=time.monotonic())
        time.sleep(.05)
        assert worker.snapshot() == result
    finally:
        worker.request_stop()
        assert worker.join(12.).stopped


def test_cpu_factory_initialization_failure_becomes_unhealthy_on_first_frame(tmp_path):
    factory = CPUFactory(str(tmp_path / "missing.onnx"), "0" * 64, threads=1)
    worker = SpawnWorker("signs", factory, generation=1)
    worker.start()
    try:
        assert worker.ready.wait(12.)
        assert worker.snapshot() is None  # No frame identity exists before submit.
        OfflineArraySource(1, (worker,)).publish(arrays(), grabbed=time.monotonic())
        result = wait_for(worker.snapshot)
        assert not result.healthy and "FileNotFoundError" in result.error
    finally:
        worker.request_stop()
        assert worker.join(12.).stopped


def test_no_live_session_factory_or_cross_generation_frame_is_accepted():
    with pytest.raises(ValueError):
        SpawnWorker("driver", object(), generation=1)
    worker = SpawnWorker("driver", FixtureFactory(), generation=1)
    with pytest.raises(ValueError):
        OfflineArraySource(2, (worker,)).publish(arrays(), grabbed=time.monotonic())
