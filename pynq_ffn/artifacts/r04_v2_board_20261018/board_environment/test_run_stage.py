"""Offline stage runner test: fake child, pinned argv, no hardware."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace


def test_static_runner_uses_pinned_argument_list_without_shell(monkeypatch, tmp_path):
    here = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location("pinned_stage", here / "run_stage.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    script = tmp_path / "board_validate.py"
    script.write_text("raise SystemExit(0)\n")
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    pins = {name: "value" for name in ("root", "deployment_manifest", "manifest_sha256", "bit", "bit_sha256",
                                       "hwh_sha256", "system_acceptance", "acceptance_sha256")}
    pins.update(validation_script=str(script), program_script=str(script), launch_files={str(script): sha(script)},
                output_dirs={"static": str(tmp_path / "result")})
    pinpath = tmp_path / "pins.json"
    pinpath.write_text(json.dumps(pins))
    observed = []
    def spawn(arguments, **options):
        observed.append((arguments, options))
        return SimpleNamespace(wait=lambda: 0)
    monkeypatch.setattr(runner.subprocess, "Popen", spawn)
    records = tmp_path / "records"
    monkeypatch.setattr(sys, "argv", [str(here / "run_stage.py"), "--pins", str(pinpath),
                                     "--pins-sha256", sha(pinpath), "--stage", "static", "--records", str(records)])
    assert runner.main() == 0
    assert observed[0][1]["shell"] is False
    assert observed[0][0][:3] == [sys.executable, "-B", str(script)]
    assert json.loads((records / "static.finished.json").read_text())["exit_code"] == 0
