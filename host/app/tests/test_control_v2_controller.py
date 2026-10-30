from pathlib import Path
import multiprocessing as mp
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ps.control_v2 import ControlPolicy
from ps.control_v2.audit import AuditChannel, write_audit
from ps.control_v2.controller import Controller, SnapshotSource
from ps.control_v2.sender import MemorySink, Sender
from ps.control_v2.workers import FixtureFactory, OfflineArraySource, SpawnWorker
from test_control_v2_policy import config, driver, signs
from test_control_v2_workers import arrays, wait_for
from ps.packet import stop_frame


class Clock:
    now = 10.
    def __call__(self):
        return self.now


def setup_controller(*, capacity=32, sink=None):
    clock = Clock()
    d, s = SnapshotSource(), SnapshotSource()
    sender = Sender(sink or MemorySink(), generation=1, stop_ttl_s=.2, clock=clock)
    audit = AuditChannel(capacity)
    controller = Controller(ControlPolicy(config(), generation=1), d, s, sender, audit, clock=clock)
    return controller, d, s, sender, audit, clock


def test_tick_records_wait_new_reused_raw_final_bytes_and_async_write_outcomes(tmp_path):
    controller, d, s, sender, audit, clock = setup_controller()
    first = controller.tick()
    assert first.decision.reason == "waiting_results"
    assert first.write_result == "queued" and first.encoded_hex == stop_frame().hex()
    d.publish(driver())
    s.publish(signs())
    clock.now += .01
    fresh = controller.tick()
    assert fresh.driver_raw == (200., 100.) and fresh.final == (200., 100.)
    assert not fresh.driver.reused and not fresh.signs.reused
    sender.start()
    try:
        wait_for(lambda: sender.feedback)
        clock.now += .01
        reused = controller.tick()
        assert reused.driver.reused and reused.signs.reused
        assert reused.driver.source_seq == 1 and reused.driver.age == pytest.approx(.02)
        assert reused.feedback is not None and reused.write_events
        assert reused.feedback.written == 21
    finally:
        sender.request_shutdown()
        assert sender.join(2.).stopped
    rows = audit.drain()
    assert len(rows) == 3
    path = tmp_path / "audit.jsonl"
    write_audit(path, rows)
    with pytest.raises(FileExistsError):
        write_audit(path, rows)


def test_full_audit_and_external_writer_error_are_visible_fail_closed():
    controller, d, s, sender, audit, clock = setup_controller(capacity=1)
    d.publish(driver())
    s.publish(signs())
    assert not controller.tick().decision.stop
    clock.now += .01
    full = controller.tick()
    assert full.decision.stop and "audit_overflow" in full.decision.reason
    assert sender.stop_latched and audit.fault == "audit_overflow"
    assert audit.first_rejected == full and audit.rejected_ticks == 1
    controller2, d2, s2, sender2, audit2, clock2 = setup_controller()
    audit2.fail("offline_writer_error")
    failed = controller2.tick()
    assert failed.decision.stop and "offline_writer_error" in failed.decision.reason


def test_sender_error_feedback_latches_policy_and_cannot_resume_from_driver():
    class ShortSink(MemorySink):
        def write(self, frame):
            return 1
    controller, d, s, sender, audit, clock = setup_controller(sink=ShortSink())
    d.publish(driver())
    s.publish(signs())
    controller.tick()
    sender.start()
    try:
        wait_for(lambda: sender.fault)
        clock.now += .01
        result = controller.tick()
        assert result.decision.stop and result.decision.state.fault
        assert result.feedback.status == "short_write"
    finally:
        sender.request_shutdown()
        assert sender.join(2.).stopped


def test_real_spawn_stalled_models_do_not_block_ticks_emergency_or_stale_checks():
    ctx = mp.get_context("spawn")
    release = ctx.Event()
    driver_entered, signs_entered = ctx.Event(), ctx.Event()
    dw = SpawnWorker("driver", FixtureFactory(entered=driver_entered, release=release), generation=1)
    sw = SpawnWorker("signs", FixtureFactory(entered=signs_entered, release=release), generation=1)
    sender = Sender(MemorySink(), generation=1, stop_ttl_s=.2)
    audit = AuditChannel(256)
    controller = Controller(ControlPolicy(config(), generation=1), dw, sw, sender, audit)
    dw.start()
    sw.start()
    sender.start()
    try:
        OfflineArraySource(1, (dw, sw)).publish(arrays(), grabbed=time.monotonic())
        assert driver_entered.wait(12.) and signs_entered.wait(12.)
        started = time.monotonic()
        for _ in range(100):
            assert controller.tick().decision.reason == "waiting_results"
        emergency = controller.tick(emergency_stop=True)
        assert time.monotonic() - started < 1.
        assert emergency.decision.reason == "emergency_stop"
        assert sender.stop_latched
        dw.request_stop()
        sw.request_stop()
        assert not dw.join(.01).stopped and not sw.join(.01).stopped
    finally:
        release.set()
        dw.request_stop()
        sw.request_stop()
        assert dw.join(12.).stopped and sw.join(12.).stopped
        sender.request_shutdown()
        assert sender.join(2.).stopped
    assert not dw.is_alive and not sw.is_alive


def test_result_published_during_snapshot_read_is_not_a_spurious_future_fault():
    controller, d, s, sender, audit, clock = setup_controller()
    class PublishingSource:
        def snapshot(self):
            clock.now = 10.1
            return driver(1, 10., completed=10.1)
    controller.driver_source = PublishingSource()
    s.publish(signs(1, 10., completed=10.1))
    row = controller.tick()
    assert not row.decision.stop and row.now == 10.1
    assert row.driver.age == pytest.approx(.1)


@pytest.mark.parametrize("source_kind", ["driver", "signs"])
def test_invalid_snapshot_type_still_returns_auditable_stop_row(source_kind):
    controller, d, s, sender, audit, clock = setup_controller()
    d.publish(driver())
    s.publish(signs())
    (d if source_kind == "driver" else s).publish(object())
    row = controller.tick()
    assert row.decision.stop and "snapshot_type" in row.decision.reason
    assert sender.stop_latched
    invalid = row.driver if source_kind == "driver" else row.signs
    assert invalid.source_seq is None and invalid.healthy is False
    assert "invalid_snapshot_type" in invalid.error
    assert len(audit.drain()) == 1


def test_offline_writer_exception_marks_channel_and_next_tick_is_stop(tmp_path):
    controller, d, s, sender, audit, clock = setup_controller()
    row = controller.tick()
    path = tmp_path / "exists.jsonl"
    path.write_text("preserve", encoding="utf-8")
    with pytest.raises(FileExistsError):
        write_audit(path, [row], channel=audit)
    assert path.read_text(encoding="utf-8") == "preserve"
    result = controller.tick()
    assert result.decision.stop and "offline_writer_error" in result.decision.reason
    assert sender.stop_latched


def test_cached_frame_aging_and_late_generation_failures_are_audited():
    controller, d, s, sender, audit, clock = setup_controller()
    d.publish(driver())
    s.publish(signs())
    controller.tick()
    clock.now += .6
    stale = controller.tick()
    assert stale.driver.reused and stale.driver.age == pytest.approx(.6)
    assert stale.decision.reason == "fault:driver_stale"
    controller.policy.reset(2)
    clock.now += .1
    late = controller.tick()
    assert late.decision.stop and "generation" in late.decision.reason
