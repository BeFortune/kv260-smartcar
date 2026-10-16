"""Validate the host-compiled C++ reference with real model weights (not PL)."""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
from pathlib import Path
import platform
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ps.runtime import Runtime
from ps.preprocess import preprocess_record, read_records


class HostFFN:
    def __init__(self, library):
        self.library = ctypes.CDLL(str(Path(library).resolve()))
        self.function = self.library.ffn_accel_host
        self.function.argtypes = [ctypes.POINTER(ctypes.c_float)] * 6 + [ctypes.c_uint]
        self.function.restype = None

    def forward(self, layer_name, x, weights):
        x = np.ascontiguousarray(x, dtype=np.float32)
        result = np.empty_like(x)
        arrays = [x, result] + [np.ascontiguousarray(weights[name], dtype=np.float32)
                               for name in ("w1", "b1", "w2", "b2")]
        pointers = [array.ctypes.data_as(ctypes.POINTER(ctypes.c_float)) for array in arrays]
        self.function(*pointers, ctypes.c_uint(x.size // 128))
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--library", required=True, type=Path,
                        help="Trusted locally compiled DLL/shared library; loads native code")
    parser.add_argument("--recording", type=Path)
    parser.add_argument("--first-input", type=Path)
    parser.add_argument("--random-cases", type=int, default=2)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Refusing to overwrite report")
    if args.random_cases < 0:
        raise ValueError("random-cases must be nonnegative")
    rng = np.random.default_rng(20261014)
    cases = [(f"random_{i}", rng.uniform(-0.5, 0.5, (1, 3, 3, 66, 200)).astype(np.float32))
             for i in range(args.random_cases)]
    if args.first_input:
        cases.append(("historical_first_input", np.load(args.first_input, allow_pickle=False)))
    if args.recording:
        record = read_records([args.recording], limit=1)[0]
        cases.append(("recorded_three_camera_frame", preprocess_record(record)))
    if not cases:
        raise ValueError("At least one test input is required")
    backend = HostFFN(args.library)
    golden = Runtime(args.bundle, mode="golden", threads=1)
    split = Runtime(args.bundle, mode="split-ort", threads=1)
    hybrid = Runtime(args.bundle, mode="split-numpy", threads=1, backend=backend)
    report = {"status": "pass", "hardware_execution": False,
              "implementation": "Host C++ ffn_accel with std::erf; not hls::erff, RTL, or PL",
              "library_sha256": hashlib.sha256(args.library.read_bytes()).hexdigest(),
              "python": platform.python_version(), "machine": platform.machine(),
              "numpy": np.__version__, "cases": [], "layer_atol": 1e-4, "layer_rtol": 1e-4,
              "output_atol": 1e-3, "output_rtol": 0}
    for name, images in cases:
        expected = golden.run(images)
        split.run(images)
        layer_errors = {}
        layer_pass = True
        for spec in split.abi["layer_specs"]:
            layer_name = spec["name"]
            actual = backend.forward(layer_name, split.last_ffn_inputs[layer_name], split.weights[layer_name])
            reference = split.last_ffn_outputs[layer_name]
            layer_errors[layer_name] = float(np.max(np.abs(actual - reference)))
            layer_pass &= bool(np.isfinite(actual).all() and np.allclose(actual, reference, rtol=1e-4, atol=1e-4))
        outputs = hybrid.run(images)
        errors = {key: float(np.max(np.abs(outputs[key] - expected[key]))) for key in expected}
        integer_mismatches = int(np.count_nonzero(np.trunc(outputs["speed_raw"]) != np.trunc(expected["speed_raw"])))
        passed = layer_pass and all(value <= 1e-3 for value in errors.values()) and integer_mismatches == 0
        report["cases"].append({"name": name, "pass": passed, "layer_max_abs_error": layer_errors,
                                  "output_max_abs_error": errors, "speed_integer_mismatches": integer_mismatches})
        if not passed:
            report["status"] = "failed"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf8") as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
