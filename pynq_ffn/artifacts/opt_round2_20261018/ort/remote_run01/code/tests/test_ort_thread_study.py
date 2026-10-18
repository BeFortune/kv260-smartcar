"""CPU-only checks for the isolated ORT thread experiment."""
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import ort_thread_study as study
from ps.runtime import Runtime

BUNDLE = ROOT / "artifacts/bundle_20261014"
FIRST_INPUT = ROOT.parent / "kv260_v1/artifacts/results/single_100/first_input.npy"


@pytest.mark.parametrize("name", study.ALL)
def test_policy_coverage(name):
    spec = study.policy(name)
    assert spec["graph_optimization"] == "ORT_ENABLE_EXTENDED"
    assert spec["execution_mode"] == "ORT_SEQUENTIAL"
    assert spec["inter_op_threads"] == 1
    assert study.policy(study.reference_name(spec))["mode"] == "golden"
    assert spec["allow_spinning"] is (not name.endswith("_off"))


def test_six_condition_first_pass():
    assert study.QUICK == ("golden4", "split1", "split4", "split4_off", "mixed4_1", "mixed4_1_off")
    assert study.policy("golden_default")["heavy_threads"] == 0
    assert study.policy("split_default")["small_threads"] == 0
    with pytest.raises(ValueError):
        study.policy("split8")
    with pytest.raises(ValueError):
        study.policy("invalid")


def test_options_and_mixed_assignment(monkeypatch):
    captured = []
    class Session:
        def get_providers(self):
            return ["CPUExecutionProvider"]
    def create(path, sess_options, providers):
        captured.append(sess_options)
        assert providers == ["CPUExecutionProvider"]
        return Session()
    monkeypatch.setattr(study.ort, "InferenceSession", create)
    factory = study.SessionFactory(study.policy("mixed4_1_off"))
    for name in ("stage_0", "stage_1", "camera_0", "camera_1", "stage_2", "stage_3", "stage_4", "cross_0", "cross_1"):
        factory.InferenceSession(name + ".onnx", providers=["CPUExecutionProvider"])
    assert [o.intra_op_num_threads for o in captured] == [4, 4, 4, 4, 1, 1, 1, 1, 1]
    for option in captured:
        assert option.inter_op_num_threads == 1
        assert option.execution_mode == study.ort.ExecutionMode.ORT_SEQUENTIAL
        assert option.graph_optimization_level == study.ort.GraphOptimizationLevel.ORT_ENABLE_EXTENDED
        assert option.get_session_config_entry("session.intra_op.allow_spinning") == "0"
        assert option.get_session_config_entry("session.inter_op.allow_spinning") == "0"
        assert not option.enable_profiling
    factory = study.SessionFactory(study.policy("golden_default"))
    factory.InferenceSession("model.onnx", providers=["CPUExecutionProvider"])
    assert captured[-1].intra_op_num_threads == 0
    assert captured[-1].get_session_config_entry("session.intra_op.allow_spinning") == "1"
    with pytest.raises(ValueError):
        factory.InferenceSession("model.onnx", providers=["CUDAExecutionProvider"])


def test_comparison_gates():
    arrays = {key: np.ones((1, 2), np.float32) for key in study.production.OUTPUT_NAMES}
    assert study.compare_outputs(arrays, arrays)["pass"]
    changed = {key: value.copy() for key, value in arrays.items()}
    changed["speed_raw"][0, 0] -= .0001
    assert not study.compare_outputs(changed, arrays)["pass"]  # float tolerance passes, truncation fails
    assert study.compare_outputs(changed, arrays)["speed_integer_mismatches"] == 1
    changed["speed_raw"][0, 0] = np.nan
    with pytest.raises(ValueError):
        study.compare_outputs(changed, arrays)
    with pytest.raises(ValueError):
        study.compare_outputs({"speed_raw": arrays["speed_raw"]}, arrays)


def test_distribution_and_context():
    stats = study.distribution([1, 2, 3, 4])
    assert stats["p50"] == 2.5 and stats["n"] == 4 and stats["max"] == 4
    with pytest.raises(ValueError):
        study.distribution([])
    before = {"process_ctx": {"voluntary": 2, "involuntary": 4}}
    after = {"process_ctx": {"voluntary": 3, "involuntary": 7}}
    assert study.context_delta(before, after) == {"voluntary": 1, "involuntary": 3}
    assert study.context_delta({"process_ctx": None}, after) is None


def test_profile_parser(tmp_path):
    path = tmp_path / "session.json"
    events = [{"name": "matmul_kernel_time", "dur": 10, "args": {"op_name": "MatMul", "thread_scheduling_stats": {"main_thread": {"num_run": 1}}}},
              {"name": "matmul_fence_before", "dur": 2}]
    path.write_text(json.dumps(events), encoding="utf8")
    report = study.parse_profiles({"stage_0": path})["stage_0"]
    assert report["kernel_events"] == 1 and report["kernel_total_us"] == 10
    assert report["fence_events"] == 1 and report["fence_total_us"] == 2
    assert report["thread_scheduling_stats"]


@pytest.mark.skipif(not BUNDLE.is_dir() or not FIRST_INPUT.is_file(), reason="Reviewed local bundle/input unavailable")
@pytest.mark.parametrize("condition", ["golden1", "split1", "split4_off", "mixed4_1_off"])
def test_real_adapter_matches_production(condition):
    x = np.load(FIRST_INPUT, allow_pickle=False)
    spec = study.policy(condition)
    original_factory = study.ort.InferenceSession
    original_init = Runtime.__init__
    baseline = Runtime(BUNDLE, mode=spec["mode"], threads=1)
    expected = baseline.run(x)
    baseline.sessions.clear()
    adapter = study.PolicyRuntime(BUNDLE, spec)
    actual = adapter.run(x)
    assert study.compare_outputs(actual, expected)["pass"]
    assert study.ort.InferenceSession is original_factory
    assert Runtime.__init__ is original_init
    assert study.PolicyRuntime.run is Runtime.run
    if spec["mode"] != "golden":
        assert len(adapter.last_ffn_inputs) == len(adapter.last_ffn_outputs) == 4
        assert len(adapter.last_timings_ms) == 10
    with pytest.raises(ValueError):
        adapter.run(x.astype(np.float64))


def test_runtime_freeze_gate(monkeypatch):
    monkeypatch.setattr(study, "FROZEN_RUNTIME_SHA256", "0" * 64)
    with pytest.raises(ValueError, match="changed"):
        study.PolicyRuntime(BUNDLE, study.policy("golden1"))


@pytest.mark.skipif(not BUNDLE.is_dir() or not FIRST_INPUT.is_file(), reason="Reviewed local bundle/input unavailable")
def test_cli_subprocess_reverse_profile_and_immutable(tmp_path):
    output = tmp_path / "trial"
    command = [sys.executable, str(ROOT / "tools/ort_thread_study.py"), "--mode", "one", "--bundle", str(BUNDLE),
               "--inputs", str(FIRST_INPUT), "--frames", "1", "--warmup", "1", "--rounds", "2",
               "--condition", "split4_off", "--profile-condition", "split4_off", "--output", str(output)]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    report = json.loads((output / "study.json").read_text(encoding="utf8"))
    assert report["status"] == "已验证"
    paths = [p for p in output.glob("*/report.json")]
    reports = [json.loads(p.read_text(encoding="utf8")) for p in paths]
    assert len({r["pid"] for r in reports}) == 4
    assert len(report["analysis"]["rows"]) == 2
    assert all(r["comparison"]["pass"] for r in reports if "comparison" in r)
    assert sum(r["profiling"] for r in reports) == 1
    profile = next(r for r in reports if r["profiling"])
    assert len(profile["profile_summary"]) == 9
    assert len(list((output / "profile_01_split4_off/profiles").glob("*.json"))) == 9
    assert report["orders"] == [["split4_off"], ["split4_off"]]
    repeat = subprocess.run(command, capture_output=True, text=True)
    assert repeat.returncode != 0
    assert "FileExistsError" in repeat.stderr
    assert report == json.loads((output / "study.json").read_text(encoding="utf8"))


def test_failed_worker_writes_nonzero_report(tmp_path):
    inputs = tmp_path / "bad.npy"
    np.save(inputs, np.zeros((1, 1), dtype=np.float32))
    directory = tmp_path / "worker"
    directory.mkdir()
    # Deliberately fails before opening a bundle/session.
    job = {"directory": str(directory), "condition": "golden1", "profile": False,
           "inputs": str(inputs), "inputs_sha256": "bad"}
    path = tmp_path / "job.json"
    path.write_text(json.dumps(job), encoding="utf8")
    assert study.worker(path) == 1
    report = json.loads((directory / "report.json").read_text(encoding="utf8"))
    assert report["status"] == "失败" and report["exit_code"] == 1
    assert report["first_error"] == "Input snapshot hash mismatch"
