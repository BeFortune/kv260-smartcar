from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time

import numpy as np
import onnxruntime as ort

from .backends import NumpyFFN, validate_ffn

MODEL_SHA256 = "ad58d1fc9168da94b9ce076ba8cc9754c371ead87974da8bc9cc601ad1650d5b"
OUTPUT_NAMES = ("speed_norm", "speed_raw", "region_logits", "region_probs")


class Runtime:
    def __init__(self, bundle, mode="split-ort", threads=1, backend=None, *, spinning=None):
        if mode not in ("golden", "split-ort", "split-numpy") or threads not in (1, 2, 3, 4):
            raise ValueError("Invalid runtime mode or thread count")
        if spinning is not None and type(spinning) is not bool:
            raise ValueError("spinning must be None or bool")
        if backend is not None and mode != "split-numpy":
            raise ValueError("Injected backend requires split-numpy mode")
        self.bundle = Path(bundle).resolve()
        manifest = json.loads((self.bundle / "manifest.json").read_text(encoding="utf8"))
        if manifest["schema"] != "pynq_ffn.bundle.v1" or manifest["source_sha256"] != MODEL_SHA256:
            raise ValueError("Unexpected bundle source")
        required = {"model.onnx", "weights.npz", "ABI.json", *(f"stage_{i}.onnx" for i in range(5)),
                    "camera_0.onnx", "camera_1.onnx", "cross_0.onnx", "cross_1.onnx"}
        if set(manifest["files"]) != required:
            raise ValueError("Unexpected bundle members")
        for name, expected in manifest["files"].items():
            path = self.bundle / name
            if path.resolve().parent != self.bundle or path.stat().st_size != expected["bytes"] or hashlib.sha256(path.read_bytes()).hexdigest() != expected["sha256"]:
                raise ValueError("Bundle member failed verification")
        if manifest["files"]["model.onnx"]["sha256"] != MODEL_SHA256:
            raise ValueError("Golden model changed")
        self.abi = json.loads((self.bundle / "ABI.json").read_text(encoding="utf8"))
        if self.abi["schema"] != "pynq_ffn.abi.v1" or self.abi["outputs"] != list(OUTPUT_NAMES):
            raise ValueError("Unexpected bundle ABI")
        self.mode = mode
        self.backend = backend if backend is not None else NumpyFFN()
        self.last_timings_ms = {}
        self.last_ffn_outputs = {}
        self.last_ffn_inputs = {}
        self.weights = {}
        with np.load(self.bundle / "weights.npz", allow_pickle=False) as archive:
            expected_keys = {spec["name"] + "." + key for spec in self.abi["layer_specs"] for key in ("w1", "b1", "w2", "b2")}
            if set(archive.files) != expected_keys:
                raise ValueError("Unexpected weight archive")
            for spec in self.abi["layer_specs"]:
                self.weights[spec["name"]] = {key: np.ascontiguousarray(archive[spec["name"] + "." + key]) for key in ("w1", "b1", "w2", "b2")}
                validate_ffn(spec["name"], np.zeros(spec["shape"], dtype=np.float32), self.weights[spec["name"]])
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_EXTENDED
        options.log_severity_level = 3
        if spinning is not None:
            value = "1" if spinning else "0"
            options.add_session_config_entry("session.intra_op.allow_spinning", value)
            options.add_session_config_entry("session.inter_op.allow_spinning", value)
        def session(name):
            return ort.InferenceSession(str(self.bundle / name), sess_options=options, providers=["CPUExecutionProvider"])
        self.sessions = {}
        if mode == "golden":
            self.sessions["golden"] = session("model.onnx")
        else:
            for stage in self.abi["schedule"]:
                if stage["kind"] == "non_ffn" or mode == "split-ort":
                    current = session(stage["file"])
                    if [v.name for v in current.get_inputs()] != stage["inputs"] or [v.name for v in current.get_outputs()] != stage["outputs"]:
                        raise ValueError("Stage contract mismatch")
                    self.sessions[stage["name"]] = current

    def run(self, images):
        if not isinstance(images, np.ndarray) or images.dtype != np.float32 or images.shape != (1, 3, 3, 66, 200) or not np.isfinite(images).all():
            raise ValueError("Invalid model input")
        images = np.ascontiguousarray(images)
        self.last_timings_ms = {}
        self.last_ffn_outputs = {}
        self.last_ffn_inputs = {}
        start = time.perf_counter()
        if self.mode == "golden":
            arrays = self.sessions["golden"].run(list(OUTPUT_NAMES), {"images": images})
            outputs = dict(zip(OUTPUT_NAMES, arrays))
            self.last_timings_ms["golden"] = (time.perf_counter() - start) * 1000
        else:
            live = {"images": images}
            schedule = self.abi["schedule"]
            for index, stage in enumerate(schedule):
                before = time.perf_counter()
                feeds = {name: live[name] for name in stage["inputs"]}
                if stage["kind"] == "ffn" and self.mode == "split-numpy":
                    x = feeds[stage["inputs"][0]]
                    array = self.backend.forward(stage["name"], x, self.weights[stage["name"]])
                    if not isinstance(array, np.ndarray) or array.dtype != np.float32 or array.shape != x.shape or not np.isfinite(array).all():
                        raise ValueError("FFN backend violated output contract")
                    arrays = [np.ascontiguousarray(array)]
                else:
                    arrays = self.sessions[stage["name"]].run(stage["outputs"], feeds)
                live.update(zip(stage["outputs"], arrays))
                self.last_timings_ms[stage["name"]] = (time.perf_counter() - before) * 1000
                if stage["kind"] == "ffn":
                    self.last_ffn_inputs[stage["name"]] = feeds[stage["inputs"][0]].copy()
                    self.last_ffn_outputs[stage["name"]] = arrays[0].copy()
                needed = set(OUTPUT_NAMES)
                needed.update(name for future in schedule[index+1:] for name in future["inputs"])
                live = {name: value for name, value in live.items() if name in needed}
            outputs = {name: live[name] for name in OUTPUT_NAMES}
        self.last_timings_ms["total"] = (time.perf_counter() - start) * 1000
        for name, expected in [("speed_norm", (1, 2)), ("speed_raw", (1, 2)), ("region_logits", (1, 15)), ("region_probs", (1, 15))]:
            if outputs[name].shape != expected or outputs[name].dtype != np.float32 or not np.isfinite(outputs[name]).all():
                raise ValueError("Invalid model output")
        if np.max(np.abs(outputs["speed_raw"])) > 500.001:
            raise ValueError("Speed outside reviewed range")
        return outputs
