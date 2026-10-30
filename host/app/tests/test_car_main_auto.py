"""临时自动开跑模式（不用手柄）的离线测试。复用 test_car_main 的假硬件。"""
from pathlib import Path
import hashlib
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_car_main import FakeHw, args_for, car_main, events_of, models_dir, stop_when  # noqa: E402,F401
from ps.packet import stop_frame  # noqa: E402


def test_auto_start_drives_without_joystick_and_stops_on_sigterm(tmp_path, models_dir):  # noqa: F811
    hw = FakeHw(tmp_path, js_script=[])

    def no_js(config):
        raise AssertionError("joystick must not be opened in auto-start mode")
    hw.make_js = no_js
    stop = stop_when(hw, hw.clock.t + 10.0)
    code = car_main.run(args_for(tmp_path, models_dir, auto_start=True), hw, stop)
    assert code == car_main.EXIT_OK
    _, events = events_of(tmp_path)
    kinds = [e["kind"] for e in events]
    assert "auto_start" in kinds
    assert next(e for e in events if e["kind"] == "pl_self_test")["mismatch_bytes"] == 0
    speed = [f for f in hw.link.frames if f != stop_frame()]
    assert len(speed) > 10                       # 设备齐了直接开跑
    assert hw.link.frames[-1] == stop_frame()   # 停服务时补停车帧
    assert hw.cams.closed and hw.link.closed


def test_default_still_needs_joystick(tmp_path, models_dir):  # noqa: F811
    args = car_main.build_cli().parse_args(["--runs-dir", str(tmp_path / "r")])
    assert args.auto_start is False
    assert args.roadsign_config is None


def test_roadsign_config_drives_only_stop_frames_with_fake_hardware(tmp_path, models_dir):  # noqa: F811
    root = Path(__file__).resolve().parents[1]
    model = root / "artifacts/roadsign_migration_20261027/run01/fixtures/constant_pedestrian.onnx"
    sha = hashlib.sha256(model.read_bytes()).hexdigest()
    config = tmp_path / "roadsign.json"
    config.write_text(json.dumps({"schema": "roadsign_supervisor_config_v1",
                                  "model": str(model), "sha256": sha,
                                  "max_result_age_s": 0.5, "threads": 1}), encoding="utf-8")
    hw = FakeHw(tmp_path, js_script=[])
    stop = stop_when(hw, hw.clock.t + 10.0)

    code = car_main.run(args_for(tmp_path, models_dir, auto_start=True,
                                 roadsign_config=config), hw, stop)

    assert code == car_main.EXIT_OK
    assert hw.link.frames and set(hw.link.frames) == {stop_frame()}
    _, events = events_of(tmp_path)
    assert any(e["kind"] == "roadsign_config" for e in events)
    decision = next(e for e in events if e["kind"] == "control_decision")
    assert decision["reason"] == "pedestrian" and decision["stop"] is True
