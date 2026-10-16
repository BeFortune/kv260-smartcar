from pathlib import Path
import json
import sys
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ps.backends import NumpyFFN
from ps.partition import build_bundle, load_source, MODEL_SHA256
from ps.runtime import Runtime

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = ROOT.parent / "kv260_v1" / "v1_payload.zip"
FIRST_INPUT = ROOT.parent / "kv260_v1" / "artifacts" / "results" / "single_100" / "first_input.npy"


@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    if not PAYLOAD.is_file():
        pytest.skip("Reviewed model payload not present")
    target = tmp_path_factory.mktemp("model") / "bundle"
    build_bundle(target, payload=PAYLOAD)
    return target


def test_preparation_refuses_output_and_unreviewed_model(bundle, tmp_path):
    with pytest.raises(FileExistsError):
        build_bundle(bundle, payload=PAYLOAD)
    bad = tmp_path / "bad.onnx"
    bad.write_bytes(b"not the reviewed model")
    with pytest.raises(ValueError):
        load_source(model=bad)
    with pytest.raises(ValueError):
        load_source()
    with pytest.raises(ValueError):
        load_source(model=bad, payload=PAYLOAD)
    manifest = json.loads((bundle / "manifest.json").read_text())
    assert manifest["source_sha256"] == MODEL_SHA256
    assert len(manifest["files"]) == 12


def test_schedule_and_weights(bundle):
    abi = json.loads((bundle / "ABI.json").read_text())
    assert [s["name"] for s in abi["schedule"]] == ["stage_0", "camera_0", "stage_1", "camera_1", "stage_2", "cross_0", "stage_3", "cross_1", "stage_4"]
    assert "/model/Reshape_4_output_0" in abi["schedule"][4]["outputs"]
    assert "/model/Reshape_4_output_0" in abi["schedule"][6]["inputs"]
    for spec in abi["layer_specs"]:
        stage = next(s for s in abi["schedule"] if s["name"] == spec["name"])
        assert stage["inputs"] == [spec["input"]] and stage["outputs"] == [spec["output"]]
        assert spec["residual"] not in stage["inputs"]
    with np.load(bundle / "weights.npz", allow_pickle=False) as weights:
        assert len(weights.files) == 16
        assert weights["camera_0.w1"].shape == (128, 256)
        assert weights["cross_1.w2"].shape == (256, 128)


@pytest.mark.parametrize("historical", [False, True])
def test_real_model_modes(bundle, historical):
    if historical and not FIRST_INPUT.is_file():
        pytest.skip("Historical input not present")
    x = np.load(FIRST_INPUT, allow_pickle=False) if historical else np.random.default_rng(42).uniform(-.5, .5, (1, 3, 3, 66, 200)).astype(np.float32)
    golden = Runtime(bundle, mode="golden").run(x)
    for mode in ("split-ort", "split-numpy"):
        runtime = Runtime(bundle, mode=mode)
        measured = runtime.run(x)
        assert set(measured) == {"speed_norm", "speed_raw", "region_logits", "region_probs"}
        for key in golden:
            np.testing.assert_allclose(measured[key], golden[key], atol=1e-3, rtol=0)
        np.testing.assert_array_equal(np.trunc(measured["speed_raw"]), np.trunc(golden["speed_raw"]))
        assert len(runtime.last_timings_ms) == 10


def test_injected_backend_and_bad_contract(bundle):
    class Backend:
        def __init__(self):
            self.calls = []
        def forward(self, layer_name, x, weights):
            self.calls.append(layer_name)
            return NumpyFFN().forward(layer_name, x, weights)
    backend = Backend()
    runtime = Runtime(bundle, mode="split-numpy", backend=backend)
    x = np.zeros((1, 3, 3, 66, 200), dtype=np.float32)
    runtime.run(x)
    assert backend.calls == ["camera_0", "camera_1", "cross_0", "cross_1"]
    with pytest.raises(ValueError):
        runtime.run(x.astype(np.float64))
    class Bad:
        def forward(self, layer_name, x, weights):
            return x.astype(np.float64)
    with pytest.raises(ValueError):
        Runtime(bundle, mode="split-numpy", backend=Bad()).run(x)
    with pytest.raises(ValueError):
        Runtime(bundle, mode="golden", backend=backend)


def test_numpy_shape_and_dtype():
    backend = NumpyFFN()
    weights = {"w1": np.zeros((128, 256), np.float32), "b1": np.ones(256, np.float32),
               "w2": np.zeros((256, 128), np.float32), "b2": np.ones(128, np.float32)}
    x = np.zeros((1, 4, 128), np.float32)
    result = backend.forward("cross_0", x, weights)
    np.testing.assert_array_equal(result, np.ones_like(x))
    assert result.flags.c_contiguous
    with pytest.raises(ValueError):
        backend.forward("unknown", x, weights)
    with pytest.raises(ValueError):
        backend.forward("camera_0", x, weights)


def test_numpy_exact_erf_activation():
    import math
    values = np.asarray([-3, -1, -.1, 0, .1, 1, 3], np.float32)
    x = np.zeros((1, 4, 128), np.float32)
    weights = {"w1": np.zeros((128, 256), np.float32), "b1": np.zeros(256, np.float32),
               "w2": np.zeros((256, 128), np.float32), "b2": np.zeros(128, np.float32)}
    weights["b1"][:len(values)] = values
    weights["w2"][:len(values), :len(values)] = np.eye(len(values), dtype=np.float32)
    actual = NumpyFFN().forward("cross_0", x, weights)
    expected = (values * (np.asarray([math.erf(float(v / np.float32(np.sqrt(2)))) for v in values], dtype=np.float32) + np.float32(1))) * np.float32(.5)
    np.testing.assert_array_equal(actual[0, 0, :len(values)], expected)


def test_replay_contract_and_existing_output(bundle, tmp_path):
    import cv2
    from ps.replay import replay
    recording = tmp_path / "recording"
    recording.mkdir()
    for name in ("front", "left", "right"):
        (recording / name).mkdir()
        assert cv2.imwrite(str(recording / name / "1.jpeg"), np.full((480, 640, 3), 90, dtype=np.uint8))
    (recording / "auto_data.csv").write_text("front/1.jpeg,left/1.jpeg,right/1.jpeg,200,250\n")
    output = tmp_path / "results"
    summary = replay([recording], bundle, output, backend="split-numpy", limit=1)
    assert summary["status"] == "complete" and summary["frames_completed"] == 1
    assert summary["hardware_execution"] is False
    with pytest.raises(FileExistsError):
        replay([recording], bundle, output)


def test_manifest_hash_failure(bundle, tmp_path):
    import shutil
    clone = tmp_path / "tampered"
    shutil.copytree(bundle, clone)
    with (clone / "weights.npz").open("ab") as stream:
        stream.write(b"tamper")
    with pytest.raises(ValueError):
        Runtime(clone)
