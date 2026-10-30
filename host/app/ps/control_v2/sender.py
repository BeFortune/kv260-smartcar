"""Single write owner with an in-process latest mailbox; no SerialLink construction.

A write already in progress cannot be cancelled. A latched stop supersedes queued
speed, but a blocked sink means physical stopping cannot be promised.
"""
from dataclasses import dataclass
from collections import deque
import threading
import time

from ps.packet import encode_side_speed, stop_frame
from .snapshots import finite, integer
from .workers import JoinOutcome


@dataclass(frozen=True)
class Command:
    generation: int
    seq: int
    issued: float
    expires: float
    left: float
    right: float
    stop: bool
    reason: str

    def __post_init__(self):
        integer(self.generation, "generation", 1)
        integer(self.seq, "seq", 1)
        if finite(self.issued, "issued") < 0 or finite(self.expires, "expires") <= self.issued:
            raise ValueError("command requires 0 <= issued < expires")
        for name in ("left", "right"):
            if not 0 <= finite(getattr(self, name), name) <= 999:
                raise ValueError("raw wheel output outside encoding range")
        if type(self.stop) is not bool or (self.stop and (self.left != 0 or self.right != 0)):
            raise ValueError("stop requires zero wheel output")
        if not isinstance(self.reason, str) or not self.reason:
            raise ValueError("command requires a reason")

    @property
    def frame(self):
        return stop_frame() if self.stop else encode_side_speed(self.left, self.right)


@dataclass(frozen=True)
class WriteFeedback:
    generation: int
    seq: int | None
    encoded_hex: str
    status: str
    written: int | None
    completed: float
    error: str | None


class MemorySink:
    """Offline fixture only; successful write does not mean STM32 reception."""
    def __init__(self):
        self._frames = []
        self.owner = None

    @property
    def frames(self):
        return tuple(self._frames)

    def write(self, frame):
        owner = threading.get_ident()
        if self.owner is not None and self.owner != owner:
            raise RuntimeError("sink has more than one write owner")
        self.owner = owner
        self._frames.append(bytes(frame))
        return len(frame)


class Sender:
    """CPython thread mailbox: assignments/deque operations never wait for sink I/O.

    offer(stop=True) is a policy hold: it must be written before a later speed can
    pass. request_stop() is a hard latch, cleared only by a stopped/new generation.
    Mailbox selection and I/O commit use one short lock. offer() tries once and
    fails closed on contention; write() is always outside that lock. A committed
    I/O cannot be cancelled, even in the small gap before the sink call begins.
    """
    def __init__(self, sink, *, generation, stop_ttl_s, clock=time.monotonic, feedback_capacity=1024):
        integer(generation, "generation", 1)
        integer(feedback_capacity, "feedback_capacity", 1)
        if finite(stop_ttl_s, "stop_ttl_s") <= 0:
            raise ValueError("stop_ttl_s must be positive")
        self.sink, self.generation = sink, generation
        self.stop_ttl_s, self.clock = stop_ttl_s, clock
        self.feedback_capacity = feedback_capacity
        self._pending = None
        self._pending_stop = None
        self._mailbox_lock = threading.Lock()
        self._committed = None
        self._last_seq = 0
        self._last_issued = None
        self._active_expires = None
        self._stop_latched = False
        self._fault = None
        self._feedback = None
        self._events = deque()
        self._event = threading.Event()
        self._shutdown = False
        self._started = False
        self.thread = threading.Thread(target=self._run, name="control_v2_sender", daemon=False)

    @property
    def feedback(self):
        return self._feedback

    @property
    def fault(self):
        return self._fault

    @property
    def stop_latched(self):
        return self._stop_latched

    @property
    def is_alive(self):
        return self._started and self.thread.is_alive()

    def _fail(self, reason):
        self._fault = self._fault or reason
        self.request_stop(reason=reason)

    def offer(self, command):
        if not isinstance(command, Command):
            self._fail("invalid_command")
            return False
        try:
            now = finite(self.clock(), "sender.clock")
            if command.generation != self.generation:
                raise ValueError("command_generation")
            if command.seq <= self._last_seq or (self._last_issued is not None and command.issued < self._last_issued):
                raise ValueError("command_ordering")
            if command.issued > now or now >= command.expires:
                raise ValueError("command_expired_or_future")
        except ValueError as error:
            self._fail(str(error))
            return False
        if not self._mailbox_lock.acquire(False):
            self._fail("sender_mailbox_contention")
            return False
        try:
            if command.seq <= self._last_seq or (self._last_issued is not None and command.issued < self._last_issued):
                self._fail("command_ordering")
                return False
            self._last_seq, self._last_issued = command.seq, command.issued
            if self._stop_latched:
                return False
            if command.stop:
                self._pending_stop = command
                self._pending = None
            else:
                self._pending = command
        finally:
            self._mailbox_lock.release()
        self._event.set()
        return True

    def request_stop(self, *, reason):
        self._stop_latched = True
        self._event.set()

    def start(self):
        if self._started:
            raise ValueError("sender cannot restart; use a new generation")
        self._started = True
        self.thread.start()

    def _record(self, value):
        self._feedback = value
        if len(self._events) >= self.feedback_capacity:
            self._fail("sender_feedback_overflow")
        else:
            self._events.append(value)

    def drain_feedback(self):
        values = []
        for _ in range(self.feedback_capacity):
            try:
                values.append(self._events.popleft())
            except IndexError:
                break
        return tuple(values)

    def _write(self, command):
        now = self.clock()
        # This lock acquisition is sender-thread only. Control never waits for it.
        # Commit is the linearization boundary, not the earlier mailbox selection.
        with self._mailbox_lock:
            if self._stop_latched:
                command = None
                self._pending = None
                self._pending_stop = None
            elif command is not None and not command.stop and self._pending_stop is not None:
                command = self._pending_stop
                self._pending_stop = None
            self._committed = command
        if command is not None and (command.generation != self.generation or command.issued > now or now >= command.expires):
            self._record(WriteFeedback(self.generation, command.seq, command.frame.hex(),
                                      "expired", None, now, "command_expired_or_future"))
            self._fail("command_expired_or_future")
            command = None
        frame = stop_frame() if command is None else command.frame
        status, written, error = "written", None, None
        try:
            written = self.sink.write(frame)
            if type(written) is not int or written != len(frame):
                status, error = "short_write", f"expected {len(frame)} bytes, got {written!r}"
        except Exception as caught:
            status, error = "error", f"{type(caught).__name__}: {caught}"
        if error:
            self._fail(status + ":" + error)
        self._record(WriteFeedback(self.generation, command.seq if command else None,
                                  frame.hex(), status, written, self.clock(), error))
        self._active_expires = command.expires if command is not None and not command.stop and error is None else None
        return frame == stop_frame()

    def _run(self):
        next_stop = 0.
        while True:
            self._event.wait(self.stop_ttl_s / 2)
            self._event.clear()
            now = self.clock()
            command = None
            have_command = False
            with self._mailbox_lock:
                if self._active_expires is not None and now >= self._active_expires and self._pending is None:
                    self._fail("active_command_expired")
                if self._stop_latched:
                    self._pending = None
                    self._pending_stop = None
                    if now >= next_stop or self._shutdown:
                        have_command = True
                elif self._pending_stop is not None:
                    command = self._pending_stop
                    self._pending_stop = None
                    have_command = True
                elif self._pending is not None:
                    command = self._pending
                    self._pending = None
                    have_command = True
            if have_command:
                stopped = self._write(command)
                if stopped:
                    next_stop = self.clock() + self.stop_ttl_s / 2
                if self._shutdown and stopped:
                    break
            # An ordinary speed queued behind a policy stop must not wait for a new offer.
            if self._pending is not None or self._pending_stop is not None:
                self._event.set()

    def request_shutdown(self):
        self._shutdown = True
        self.request_stop(reason="shutdown")

    def join(self, timeout_s):
        if finite(timeout_s, "timeout_s") < 0:
            raise ValueError("join timeout must be nonnegative")
        if not self._started:
            return JoinOutcome(True, False, "not_started")
        self.thread.join(timeout_s)
        alive = self.thread.is_alive()
        return JoinOutcome(not alive, alive, "write_not_cancelled_sender_not_stopped" if alive else "sender_exited")
