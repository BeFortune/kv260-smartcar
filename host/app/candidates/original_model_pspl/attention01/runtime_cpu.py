"""Independent, source-verified three-PS/two-camera runtime; no device imports.

Injected backend protocol: prepare(layer, weights) at construction, run(layer, x)
per inference. ORTFFNAdapter implements the protocol on CPU, never PL.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import time
import numpy as np
import onnxruntime as ort
from attention_rewrite import SOURCE_PATH, SOURCE_SHA256, OUTPUT_NAMES, convert, split_camera


def options(optimization="extended", *, optimized_path=None, profile_prefix=None):
    if optimization not in ("disabled", "extended", "all"):
        raise ValueError("Unsupported optimization setting")
    value = ort.SessionOptions()
    value.intra_op_num_threads = 4
    value.inter_op_num_threads = 1
    value.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    value.graph_optimization_level = {"disabled": ort.GraphOptimizationLevel.ORT_DISABLE_ALL,
                                      "extended": ort.GraphOptimizationLevel.ORT_ENABLE_EXTENDED,
                                      "all": ort.GraphOptimizationLevel.ORT_ENABLE_ALL}[optimization]
    value.add_session_config_entry("session.intra_op.allow_spinning", "0")
    value.add_session_config_entry("session.inter_op.allow_spinning", "0")
    value.log_severity_level = 3
    if optimized_path is not None:
        path = Path(optimized_path)
        if path.exists():
            raise FileExistsError("Refusing existing optimized graph")
        value.optimized_model_filepath = str(path)
    if profile_prefix is not None:
        value.enable_profiling = True
        value.profile_file_prefix = str(profile_prefix)
    return value


def session(path, optimization="extended", **kwargs):
    result = ort.InferenceSession(str(path), sess_options=options(optimization, **kwargs),
                                 providers=["CPUExecutionProvider"])
    if result.get_providers() != ["CPUExecutionProvider"]:
        raise ValueError("CPU provider contract changed")
    return result


def verify_bundle(directory):
    directory = Path(directory).resolve()
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    expected = {"golden.onnx", "single_camera_full.onnx", "full.onnx", "stage_0.onnx", "stage_1.onnx", "stage_2.onnx",
                "camera_0.onnx", "camera_1.onnx", "weights.npz", "ABI.json", "rewrites.json", "single_camera_rewrites.json"}
    if manifest.get("schema") != "pynq_ffn.camera_only_prepared.bundle.v1" or manifest.get("source_sha256") != SOURCE_SHA256 or set(manifest["files"]) != expected:
        raise ValueError("Unexpected independent bundle source/schema/members")
    for name, identity in manifest["files"].items():
        path = directory / name
        if path.resolve().parent != directory or path.stat().st_size != identity["bytes"] or hashlib.sha256(path.read_bytes()).hexdigest() != identity["sha256"]:
            raise ValueError("Bundle member identity changed: " + name)
    # Manifest cannot bless an arbitrary candidate even if its own hashes agree.
    candidate, audit = convert(SOURCE_PATH.read_bytes(), blocks=(0, 1))
    if manifest["candidate_sha256"] != audit["candidate_sha256"] or manifest["files"]["full.onnx"]["sha256"] != audit["candidate_sha256"] or manifest["files"]["golden.onnx"]["sha256"] != SOURCE_SHA256:
        raise ValueError("Candidate is not derived from the pinned layout rewrite")
    abi = json.loads((directory / "ABI.json").read_text(encoding="utf-8"))
    if abi["schema"] != "pynq_ffn.camera_only_prepared.abi.v1" or abi["outputs"] != OUTPUT_NAMES or abi["candidate_sha256"] != audit["candidate_sha256"]:
        raise ValueError("Independent ABI contract changed")
    if [item["name"] for item in abi["schedule"]] != ["stage_0", "camera_0", "stage_1", "camera_1", "stage_2"]:
        raise ValueError("Camera-only schedule changed")
    expected_stages, expected_weights, expected_abi = split_camera(candidate)
    if abi != expected_abi:
        raise ValueError("ABI differs from fixed original camera-only split")
    for filename, content in expected_stages.items():
        if (directory / filename).read_bytes() != content:
            raise ValueError("Split graph differs from fixed source-derived candidate")
    with np.load(directory / "weights.npz", allow_pickle=False) as archive:
        if set(archive.files) != set(expected_weights):
            raise ValueError("Original weight archive keys changed")
        for key, expected_array in expected_weights.items():
            actual = archive[key]
            if actual.dtype != expected_array.dtype or actual.shape != expected_array.shape or actual.tobytes() != expected_array.tobytes():
                raise ValueError("Original weights byte identity changed: " + key)
    return manifest, abi


class ORTFFNAdapter:
    hardware_execution = False

    def __init__(self, directory, abi, optimization="extended"):
        self.directory = Path(directory)
        self.abi = abi
        self.sessions = {spec["name"]: session(self.directory / (spec["name"] + ".onnx"), optimization)
                         for spec in abi["layer_specs"]}
        self.specs = {spec["name"]: spec for spec in abi["layer_specs"]}
        self.frozen = {}

    def prepare(self, layer, weights):
        if layer in self.frozen or layer not in self.specs or set(weights) != {"w1", "b1", "w2", "b2"}:
            raise ValueError("Invalid or duplicate CPU FFN preparation")
        snapshot = {}
        with np.load(self.directory / "weights.npz", allow_pickle=False) as archive:
            for key in self.abi["weight_order"]:
                array = weights[key]
                if array.dtype != np.float32 or list(array.shape) != self.abi["weight_shapes"][key] or not np.isfinite(array).all() or array.tobytes() != archive[layer + "." + key].tobytes():
                    raise ValueError("CPU adapter weights do not match original initializer")
                snapshot[key] = np.array(array, dtype=np.float32, order="C", copy=True)
                snapshot[key].flags.writeable = False
        self.frozen[layer] = snapshot

    def run(self, layer, x):
        if layer not in self.frozen:
            raise ValueError("FFN layer was not prepared")
        spec = self.specs[layer]
        return self.sessions[layer].run([spec["output"]], {spec["input"]: x})[0]


class BundleRuntime:
    def __init__(self, directory, *, backend=None, optimization="extended", trace=False):
        self.directory = Path(directory).resolve()
        self.manifest, self.abi = verify_bundle(self.directory)
        self.trace = bool(trace)
        self.sessions = {item["name"]: session(self.directory / item["file"], optimization)
                         for item in self.abi["schedule"] if item["kind"] == "non_ffn"}
        self.backend = backend if backend is not None else ORTFFNAdapter(self.directory, self.abi, optimization)
        self.weights = {}
        with np.load(self.directory / "weights.npz", allow_pickle=False) as archive:
            expected = {f"camera_{i}.{k}" for i in (0, 1) for k in self.abi["weight_order"]}
            if set(archive.files) != expected:
                raise ValueError("Unexpected weight archive")
            for spec in self.abi["layer_specs"]:
                layer = spec["name"]
                weights = {key: np.ascontiguousarray(archive[layer + "." + key]) for key in self.abi["weight_order"]}
                self.backend.prepare(layer, weights)
                for array in weights.values():
                    array.flags.writeable = False
                self.weights[layer] = weights
        self.last_timings_ms = {}
        self.last_ffn_inputs = {}
        self.last_ffn_outputs = {}
        self.hardware_execution = bool(getattr(self.backend, "hardware_execution", False))
        self.execution_label = "injected_backend_requires_independent_evidence" if backend is not None else "ORT_CPU_only"

    def run(self, images):
        if images.dtype != np.float32 or images.shape != (1, 3, 3, 66, 200) or not np.isfinite(images).all():
            raise ValueError("Original image contract changed")
        live = {"images": np.ascontiguousarray(images)}
        self.last_timings_ms, self.last_ffn_inputs, self.last_ffn_outputs = {}, {}, {}
        start = time.perf_counter()
        schedule = self.abi["schedule"]
        for index, item in enumerate(schedule):
            before = time.perf_counter()
            feeds = {name: live[name] for name in item["inputs"]}
            if item["kind"] == "ffn":
                x = feeds[item["inputs"][0]]
                array = self.backend.run(item["name"], x)
                if not isinstance(array, np.ndarray) or array.dtype != np.float32 or array.shape != x.shape or not np.isfinite(array).all():
                    raise ValueError("Prepared backend output contract changed")
                result = [np.ascontiguousarray(array)]
                if self.trace:
                    self.last_ffn_inputs[item["name"]] = x.copy()
                    self.last_ffn_outputs[item["name"]] = result[0].copy()
            else:
                result = self.sessions[item["name"]].run(item["outputs"], feeds)
            live.update(zip(item["outputs"], result))
            self.last_timings_ms[item["name"]] = (time.perf_counter() - before) * 1000
            needed = set(OUTPUT_NAMES)
            needed.update(name for future in schedule[index + 1:] for name in future["inputs"])
            live = {name: value for name, value in live.items() if name in needed}
        outputs = {name: live[name] for name in OUTPUT_NAMES}
        self.last_timings_ms["total"] = (time.perf_counter() - start) * 1000
        return outputs
