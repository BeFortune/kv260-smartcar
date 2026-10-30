from pathlib import Path
import sys
import threading
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ps.control_v2.sender import Command, MemorySink, Sender
from ps.packet import encode_side_speed, stop_frame


def wait_for(predicate, timeout=3.):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.002)
    raise AssertionError("bounded wait expired")


def command(seq=1, *, generation=1, now=None, left=200., right=100., stop=False):
    now = time.monotonic() if now is None else now
    return Command(generation, seq, now, now + .5, left, right, stop, "test")


def test_sender_stop_latch_covers_pending_speed_and_cannot_be_overwritten():
    sink = MemorySink()
    sender = Sender(sink, generation=1, stop_ttl_s=.2)
    assert sender.offer(command())
    sender.request_stop(reason="emergency")
    assert not sender.offer(command(2))
    sender.start()
    try:
        wait_for(lambda: sink.frames)
        assert set(sink.frames) == {stop_frame()}
        assert sender.feedback.written == 21
    finally:
        sender.request_shutdown()
        assert sender.join(2.).stopped


def test_sender_keeps_latest_speed_and_reuses_existing_protocol_bytes():
    sink = MemorySink()
    sender = Sender(sink, generation=1, stop_ttl_s=.2)
    for seq in range(1, 50):
        assert sender.offer(command(seq, left=seq + .9, right=seq))
    sender.start()
    try:
        wait_for(lambda: sink.frames)
        assert sink.frames[0] == encode_side_speed(49.9, 49)
    finally:
        sender.request_shutdown()
        assert sender.join(2.).stopped


@pytest.mark.parametrize("kind", ["expired", "generation", "ordering", "short", "exception"])
def test_sender_ttl_generation_ordering_and_write_failures_latch_fault_stop(kind):
    class BadSink(MemorySink):
        def write(self, frame):
            if frame != stop_frame():
                if kind == "short":
                    return 3
                if kind == "exception":
                    raise OSError("fixture write error")
            return super().write(frame)
    sink = BadSink()
    sender = Sender(sink, generation=1, stop_ttl_s=.2)
    if kind == "expired":
        sender.offer(command(now=time.monotonic() - 5))
    elif kind == "generation":
        assert not sender.offer(command(generation=2))
    elif kind == "ordering":
        sender.offer(command(2))
        assert not sender.offer(command(1))
    else:
        sender.offer(command())
    sender.start()
    try:
        wait_for(lambda: sender.fault)
        wait_for(lambda: stop_frame() in sink.frames)
        assert not sender.offer(command(3))
        assert sender.stop_latched
    finally:
        sender.request_shutdown()
        assert sender.join(2.).stopped


def test_blocked_write_is_not_a_physical_stop_and_join_is_bounded_without_kill():
    entered, release = threading.Event(), threading.Event()
    class ControlledSink(MemorySink):
        def write(self, frame):
            if frame != stop_frame():
                entered.set()
                assert release.wait(5.)
            return super().write(frame)
    sink = ControlledSink()
    sender = Sender(sink, generation=1, stop_ttl_s=.2)
    sender.offer(command())
    sender.start()
    try:
        assert entered.wait(2.)
        started = time.monotonic()
        sender.request_stop(reason="manual")
        assert time.monotonic() - started < .2
        sender.request_shutdown()
        assert not sender.join(.01).stopped
        assert not sink.frames
    finally:
        release.set()
        assert sender.join(2.).stopped
    assert sink.frames[-1] == stop_frame()


def test_policy_stop_arriving_before_io_commit_overrides_already_selected_speed():
    paused, release = threading.Event(), threading.Event()
    calls = 0
    def clock():
        nonlocal calls
        if threading.current_thread().name == "control_v2_sender":
            calls += 1
            if calls == 2:
                paused.set()
                assert release.wait(5.)
        return time.monotonic()
    sink = MemorySink()
    sender = Sender(sink, generation=1, stop_ttl_s=.2, clock=clock)
    sender.offer(command())
    sender.start()
    try:
        assert paused.wait(2.)
        assert sender.offer(command(2, left=0., right=0., stop=True))
        release.set()
        wait_for(lambda: sink.frames)
        assert sink.frames[0] == stop_frame()
        assert encode_side_speed(200., 100.) not in sink.frames
    finally:
        release.set()
        sender.request_shutdown()
        assert sender.join(2.).stopped


def test_policy_stop_is_transient_but_is_written_before_queued_new_speed():
    sink = MemorySink()
    sender = Sender(sink, generation=1, stop_ttl_s=.2)
    assert sender.offer(command(1, left=0., right=0., stop=True))
    assert sender.offer(command(2))
    sender.start()
    try:
        wait_for(lambda: len(sink.frames) >= 2)
        assert sink.frames[:2] == (stop_frame(), encode_side_speed(200., 100.))
        assert not sender.stop_latched
    finally:
        sender.request_shutdown()
        assert sender.join(2.).stopped


def test_mailbox_contention_never_blocks_offer_and_fails_closed():
    sender = Sender(MemorySink(), generation=1, stop_ttl_s=.2)
    # Deliberately pause the tiny mailbox critical section, not sink.write().
    sender._mailbox_lock.acquire()
    try:
        started = time.monotonic()
        assert not sender.offer(command())
        assert time.monotonic() - started < .2
        assert sender.fault == "sender_mailbox_contention" and sender.stop_latched
    finally:
        sender._mailbox_lock.release()


def test_successful_speed_does_not_outlive_ttl_when_control_tick_disappears():
    sink = MemorySink()
    sender = Sender(sink, generation=1, stop_ttl_s=.05)
    now = time.monotonic()
    sender.offer(Command(1, 1, now, now + .05, 200., 100., False, "test"))
    sender.start()
    try:
        wait_for(lambda: stop_frame() in sink.frames)
        assert sink.frames[0] != stop_frame()
        assert sender.fault == "active_command_expired"
    finally:
        sender.request_shutdown()
        assert sender.join(2.).stopped


@pytest.mark.parametrize("left", [-.1, 999.1, float("nan"), True])
def test_illegal_raw_speed_is_rejected_before_truncation(left):
    with pytest.raises(ValueError):
        command(left=left)
