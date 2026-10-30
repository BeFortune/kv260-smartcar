"""Bounded in-memory audit. Disk writing is explicitly post-run, never inside tick."""
from collections import deque
from dataclasses import asdict, dataclass
import json
from pathlib import Path

from .snapshots import Decision, integer
from .sender import WriteFeedback


@dataclass(frozen=True)
class SourceAudit:
    generation: int | None
    source_seq: int | None
    grabbed: float | None
    completed: float | None
    age: float | None
    reused: bool
    healthy: bool | None
    error: str | None


@dataclass(frozen=True)
class TickAudit:
    tick_seq: int
    now: float | None
    generation: int
    driver_raw: tuple
    final: tuple
    driver: SourceAudit
    signs: SourceAudit
    decision: Decision
    encoded_hex: str
    write_result: str
    feedback: WriteFeedback | None
    write_events: tuple[WriteFeedback, ...]


class AuditChannel:
    """Single control producer, offline consumer; deque operations do not block.

    Overflow is a latched fault, with a separate first rejected row and count.
    It is impossible to retain unlimited audit in bounded memory: completeness is
    explicitly failed on overflow rather than silently discarding old records.
    """
    def __init__(self, capacity):
        integer(capacity, "capacity", 1)
        self.capacity = capacity
        self._rows = deque()
        self.fault = None
        self.first_rejected = None
        self.rejected_ticks = 0

    def preflight(self):
        if len(self._rows) >= self.capacity:
            self.fail("audit_overflow")
        return self.fault

    def fail(self, reason):
        self.fault = self.fault or str(reason)

    def append(self, row):
        if len(self._rows) >= self.capacity:
            self.fail("audit_overflow")
            self.rejected_ticks += 1
            if self.first_rejected is None:
                self.first_rejected = row
            return False
        self._rows.append(row)
        return True

    def drain(self):
        rows = []
        for _ in range(self.capacity):
            try:
                rows.append(self._rows.popleft())
            except IndexError:
                break
        return tuple(rows)


def write_audit(path, rows, *, channel=None):
    """Exclusive-create final writer; caller must not execute it on the control thread."""
    try:
        with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
            for row in rows:
                stream.write(json.dumps(asdict(row), ensure_ascii=False, allow_nan=False) + "\n")
    except Exception as error:
        if channel is not None:
            channel.fail(f"offline_writer_error:{type(error).__name__}:{error}")
        raise
