from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import validate_split


class FakeRuntime:
    pipeline_bad = False
    integer_bad = False
    single_bad = False

    def __init__(self, bundle, mode, threads):
        self.mode = mode
        self.last_timings_ms = {}
        self.last_ffn_inputs = {"cross_0": np.zeros((1, 4, 128), dtype=np.float32)}
        self.last_ffn_outputs = {"cross_0": np.zeros((1, 4, 128), dtype=np.float32)}
        self.weights = {"cross_0": {}}

    def run(self, x):
        if self.mode == "split-numpy" and self.pipeline_bad:
            self.last_ffn_outputs["cross_0"].fill(0.1)
        speed = np.array([[0.9999, 3]], dtype=np.float32)
        if self.mode == "split-numpy" and self.integer_bad:
            speed[0, 0] = 1.0001
        return {"speed_raw": speed, "speed_norm": np.zeros((1, 2), dtype=np.float32),
                "region_logits": np.zeros((1, 15), dtype=np.float32),
                "region_probs": np.full((1, 15), 1/15, dtype=np.float32)}


class FakeFFN:
    def forward(self, layer_name, value, weights):
        return np.full_like(value, np.nan) if FakeRuntime.single_bad else np.zeros_like(value)


def setup_fakes(monkeypatch):
    monkeypatch.setattr(validate_split, "Runtime", FakeRuntime)
    monkeypatch.setattr(validate_split, "NumpyFFN", FakeFFN)
    monkeypatch.setattr(FakeRuntime, "pipeline_bad", False)
    monkeypatch.setattr(FakeRuntime, "integer_bad", False)
    monkeypatch.setattr(FakeRuntime, "single_bad", False)


def test_intermediate_error_cannot_be_hidden_by_matching_final_outputs(monkeypatch):
    setup_fakes(monkeypatch)
    FakeRuntime.pipeline_bad = True
    result = validate_split.validate("unused", random_cases=1)
    assert result["status"] == "failed"
    assert not result["cases"][0]["ffn_pipeline_pass"]
    assert result["cases"][0]["comparisons"]["split-numpy"]["floats_pass"]


def test_small_float_error_crossing_integer_boundary_is_failure(monkeypatch):
    setup_fakes(monkeypatch)
    FakeRuntime.integer_bad = True
    result = validate_split.validate("unused", random_cases=1)
    assert result["status"] == "failed"
    comparison = result["cases"][0]["comparisons"]["split-numpy"]
    assert comparison["floats_pass"]
    assert not comparison["pass"]
    assert comparison["speed_integer_mismatches"] == 1


def test_single_layer_nan_cannot_report_pass(monkeypatch):
    setup_fakes(monkeypatch)
    FakeRuntime.single_bad = True
    result = validate_split.validate("unused", random_cases=1)
    assert result["status"] == "failed"
