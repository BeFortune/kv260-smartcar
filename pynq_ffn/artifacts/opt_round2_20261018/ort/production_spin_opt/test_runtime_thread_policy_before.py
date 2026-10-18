"""Tests for the optional per-session ORT spinning policy."""
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ps import runtime as runtime_module
from ps.runtime import Runtime

BUNDLE = ROOT / "artifacts/bundle_20261014"


@pytest.mark.skipif(not BUNDLE.is_dir(), reason="Reviewed bundle unavailable")
@pytest.mark.parametrize("spinning,expected", [(None, None), (False, "0"), (True, "1")])
def test_spinning_config_is_optional_and_applied_to_every_session(monkeypatch, spinning, expected):
    captured = []
    original = runtime_module.ort.InferenceSession
    def record(path, sess_options=None, providers=None):
        captured.append((Path(path).name, sess_options))
        return original(path, sess_options=sess_options, providers=providers)
    monkeypatch.setattr(runtime_module.ort, "InferenceSession", record)
    runtime = Runtime(BUNDLE, mode="split-ort", threads=4, spinning=spinning)
    assert len(captured) == 9
    assert len(runtime.sessions) == 9
    for _, options in captured:
        assert options.intra_op_num_threads == 4
        assert options.inter_op_num_threads == 1
        if expected is None:
            with pytest.raises(RuntimeError, match="does not have configuration"):
                options.get_session_config_entry("session.intra_op.allow_spinning")
            with pytest.raises(RuntimeError, match="does not have configuration"):
                options.get_session_config_entry("session.inter_op.allow_spinning")
        else:
            assert options.get_session_config_entry("session.intra_op.allow_spinning") == expected
            assert options.get_session_config_entry("session.inter_op.allow_spinning") == expected
    assert len({id(options) for _, options in captured}) == 1


@pytest.mark.skipif(not BUNDLE.is_dir(), reason="Reviewed bundle unavailable")
def test_golden_default_and_explicit_spin_values(monkeypatch):
    captured = []
    original = runtime_module.ort.InferenceSession
    def record(path, sess_options=None, providers=None):
        captured.append((Path(path).name, sess_options))
        return original(path, sess_options=sess_options, providers=providers)
    monkeypatch.setattr(runtime_module.ort, "InferenceSession", record)
    Runtime(BUNDLE, mode="golden")
    assert len(captured) == 1 and captured[-1][0] == "model.onnx"
    for key in ("session.intra_op.allow_spinning", "session.inter_op.allow_spinning"):
        with pytest.raises(RuntimeError, match="does not have configuration"):
            captured[-1][1].get_session_config_entry(key)
    captured.clear()
    Runtime(BUNDLE, mode="golden", spinning=False)
    assert len(captured) == 1
    for key in ("session.intra_op.allow_spinning", "session.inter_op.allow_spinning"):
        assert captured[-1][1].get_session_config_entry(key) == "0"


@pytest.mark.parametrize("value", [0, 1, "0", "1", object()])
def test_spinning_rejects_non_bool_values(value, tmp_path):
    with pytest.raises(ValueError, match="spinning"):
        Runtime(tmp_path / "missing", spinning=value)


def test_signature_keeps_old_positional_contract():
    import inspect
    signature = inspect.signature(Runtime)
    assert list(signature.parameters) == ["bundle", "mode", "threads", "backend", "spinning"]
    assert signature.parameters["spinning"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["spinning"].default is None
