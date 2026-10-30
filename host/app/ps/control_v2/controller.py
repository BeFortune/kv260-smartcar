"""Control tick only reads snapshots, runs policy and offers to one sender mailbox."""
import time

from ps.packet import stop_frame
from .audit import SourceAudit, TickAudit
from .sender import Command
from .snapshots import DriverSnapshot, SignSnapshot, finite


class SnapshotSource:
    """In-process injected source; no inference is ever invoked by snapshot()."""
    def __init__(self):
        self._value = None

    def publish(self, value):
        self._value = value

    def snapshot(self):
        return self._value


class Controller:
    def __init__(self, policy, driver_source, sign_source, sender, audit, *, clock=time.monotonic):
        if policy.generation != sender.generation:
            raise ValueError("controller and sender must share one generation")
        self.policy, self.driver_source, self.sign_source = policy, driver_source, sign_source
        self.sender, self.audit, self.clock = sender, audit, clock
        self.seq = 0
        self._seen = {}

    def _source_audit(self, name, result, now):
        if result is None:
            return SourceAudit(None, None, None, None, None, False, None, None)
        if not isinstance(result, DriverSnapshot if name == "driver" else SignSnapshot):
            return SourceAudit(None, None, None, None, None, False, False, "invalid_snapshot_type:" + type(result).__name__)
        key = (result.generation, result.frame_seq)
        reused = self._seen.get(name) == key
        self._seen[name] = key
        return SourceAudit(result.generation, result.frame_seq, result.grabbed, result.completed,
                           None if now is None else now - result.grabbed,
                           reused, result.healthy, result.error)

    def tick(self, *, emergency_stop=False):
        self.seq += 1
        error = self.audit.preflight() or self.sender.fault
        try:
            driver = self.driver_source.snapshot()
            signs = self.sign_source.snapshot()
        except Exception as caught:
            driver, signs = None, None
            error = error or f"snapshot_error:{type(caught).__name__}:{caught}"
        # Observe time after immutable results: a worker may publish while read runs.
        try:
            now = finite(self.clock(), "control.clock")
        except (ValueError, TypeError) as caught:
            now = None
            error = error or str(caught)
        decision = self.policy.step(now, driver, signs, emergency_stop=emergency_stop, external_fault=error)
        events = self.sender.drain_feedback()
        if decision.state.fault or decision.state.emergency:
            self.sender.request_stop(reason=decision.reason)
        write_result = "hard_stop_requested"
        encoded = stop_frame().hex()
        if now is not None and not (decision.state.fault or decision.state.emergency):
            command = Command(self.policy.generation, self.seq, now,
                              now + self.policy.config.command_ttl_s,
                              decision.left, decision.right, decision.stop, decision.reason)
            encoded = command.frame.hex()
            accepted = self.sender.offer(command)
            write_result = "queued" if accepted else "rejected"
            if not accepted:
                decision = self.policy.step(now, driver, signs, external_fault=self.sender.fault or "sender_rejected")
                self.sender.request_stop(reason=decision.reason)
                encoded = stop_frame().hex()
        row = TickAudit(self.seq, now, self.policy.generation,
                        (driver.left, driver.right) if isinstance(driver, DriverSnapshot) else (None, None),
                        (decision.left, decision.right),
                        self._source_audit("driver", driver, now), self._source_audit("signs", signs, now),
                        decision, encoded, write_result, self.sender.feedback, events)
        if not self.audit.append(row):
            self.sender.request_stop(reason=self.audit.fault)
        return row
