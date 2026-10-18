"""Compare the archived serial FFN and four-lane host DLLs, never FPGA hardware.

Build both DLLs with identical FP32 flags (no fast-math, -ffp-contract=off).
Optional replay reads only the payload manifest, declared recording CSVs and
referenced JPEGs; it never extracts or executes archive members.
"""
from __future__ import annotations

import argparse
import csv
import ctypes
import hashlib
import io
import json
from pathlib import Path
import platform
import sys
import traceback
import zipfile

import numpy as np


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class HostFFN:
    def __init__(self, path):
        self.library = ctypes.CDLL(str(Path(path).resolve()))
        self.function = self.library.ffn_accel_host
        self.function.argtypes = [ctypes.POINTER(ctypes.c_float)] * 6 + [ctypes.c_uint]
        self.function.restype = None

    def forward(self, layer_name, x, weights):
        x = np.ascontiguousarray(x, dtype=np.float32)
        count = x.size // 128
        if x.size % 128 or not 1 <= count <= 660:
            raise ValueError("Host validation accepts only 1..660 complete tokens")
        guard = np.float32(12345.25)
        storage = np.full(660 * 128 + 32, guard, dtype=np.float32)
        output = storage[16:16 + x.size].reshape(x.shape)
        arrays = [x, output] + [np.ascontiguousarray(weights[key], dtype=np.float32)
                               for key in ("w1", "b1", "w2", "b2")]
        pointers = [a.ctypes.data_as(ctypes.POINTER(ctypes.c_float)) for a in arrays]
        self.function(*pointers, count)
        if not (np.all(storage[:16] == guard) and np.all(storage[16 + x.size:] == guard)):
            raise AssertionError("Output guard or inactive token overwritten")
        return output.copy()


def compare(baseline, candidate):
    if baseline.shape != candidate.shape:
        raise AssertionError("Shape mismatch")
    finite = bool(np.isfinite(baseline).all() and np.isfinite(candidate).all())
    if not finite:
        raise AssertionError("Nonfinite baseline or candidate output")
    mismatches = int(np.count_nonzero(baseline.view(np.uint32) != candidate.view(np.uint32)))
    return {"pass": finite and mismatches == 0, "finite": finite,
            "elements": int(candidate.size), "bit_mismatches": mismatches,
            "max_abs_error": float(np.max(np.abs(baseline.astype(np.float64) - candidate)))}


def synthetic(baseline, candidate):
    rng = np.random.default_rng(20261017)

    def weights():
        return {key: rng.uniform(-0.2, 0.2, shape).astype(np.float32)
                for key, shape in (("w1", (128, 256)), ("b1", (256,)),
                                   ("w2", (256, 128)), ("b2", (128,)))}

    first = weights()
    x = rng.uniform(-1, 1, (660, 128)).astype(np.float32)
    x[-1] = (np.arange(128, dtype=np.float32) % 11 - 5) * np.float32(0.13)
    cases = [(f"boundary_{n}", x[:n], first) for n in (1, 2, 3, 4, 5, 7, 8, 9, 659, 660)]
    cases += [("reload_B", x, weights()), ("reload_C", x[:4], weights()),
              ("reload_D", x[:4], weights()), ("revisit_A", x[:1], first)]
    zero = {key: np.zeros_like(value) for key, value in first.items()}
    cases.append(("zero", x[:4], zero))
    bias = {key: value.copy() for key, value in first.items()}
    bias["w1"].fill(0)
    bias["b1"] = np.linspace(-6, 6, 256, dtype=np.float32)
    cases.append(("bias_exact_erf", x[:4], bias))

    # These terms lose the unit at 2**24 in sequential FP32, unlike a tree or
    # multiple interleaved partial sums. The oracle is the archived actual DLL.
    terms = np.array([2**24, 1, -(2**24), 1], dtype=np.float32)
    serial = np.float32(0)
    for term in terms:
        serial = np.float32(serial + term)
    regrouped = np.float32(np.float32(terms[0] + terms[2]) + np.float32(terms[1] + terms[3]))
    if serial == regrouped:
        raise AssertionError("Cancellation fixture is not order-sensitive")
    cancel1 = {key: value.copy() for key, value in zero.items()}
    cancel1["w1"][:] = np.tile(terms, 32)[:, None]
    cancel1["w2"][:] = np.float32(1 / 256)
    cases.append(("cancellation_fc1", np.ones((4, 128), np.float32), cancel1))
    cancel2 = {key: value.copy() for key, value in zero.items()}
    cancel2["b1"].fill(1)
    cancel2["w2"][:] = np.tile(terms, 64)[:, None]
    cases.append(("cancellation_fc2", x[:4], cancel2))
    dynamic = weights()
    for value in dynamic.values():
        value[:] = np.ldexp(value, rng.integers(-20, 21, value.shape, dtype=np.int32)).astype(np.float32)
    dynamic_x = np.ldexp(x[:9], rng.integers(-20, 21, x[:9].shape, dtype=np.int32)).astype(np.float32)
    cases.append(("large_dynamic_range_no_overflow", dynamic_x, dynamic))
    results = []
    for name, value, w in cases:
        result = compare(baseline.forward(name, value, w), candidate.forward(name, value, w))
        result.update(name=name, tokens=int(value.shape[0]))
        results.append(result)
    return results


def payload_replay(baseline, candidate, bundle, zip_path):
    import cv2
    import onnxruntime as ort
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from ps.preprocess import preprocess_arrays
    from ps.runtime import MODEL_SHA256, OUTPUT_NAMES, Runtime
    from validate_payload import confined_member

    cv2.setNumThreads(1)
    cv2.ocl.setUseOpenCL(False)
    golden = Runtime(bundle, mode="golden", threads=1)
    split = Runtime(bundle, mode="split-ort", threads=1)
    hybrid = Runtime(bundle, mode="split-numpy", threads=1, backend=candidate)
    result = {"status": "pass", "versions": {"opencv": cv2.__version__, "onnxruntime": ort.__version__},
              "model_sha256": MODEL_SHA256, "zip_sha256": sha256(zip_path), "frames": []}
    if sha256(Path(bundle) / "model.onnx") != MODEL_SHA256:
        raise ValueError("Bundle differs from reviewed golden model")
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Duplicate archive members are not allowed")
        manifest = json.loads(archive.read("payload_manifest.json"))
        if manifest["model_sha256"] != MODEL_SHA256:
            raise ValueError("Payload manifest differs from reviewed golden model")
        with archive.open("model/model_current.onnx") as stream:
            source_sha = hashlib.file_digest(stream, "sha256").hexdigest()
        result["payload_model_sha256"] = source_sha
        if source_sha != MODEL_SHA256:
            raise ValueError("Actual payload model differs from reviewed golden model")
        recordings = manifest["recordings"]
        if not recordings:
            raise ValueError("Empty recording manifest")
        segments = [str(item["segment"]) for item in recordings]
        if len(set(segments)) != len(segments):
            raise ValueError("Duplicate recording in manifest")
        expected_frames = sum(int(item["frames"]) for item in recordings)
        if any(int(item["frames"]) < 1 for item in recordings):
            raise ValueError("Every recording must contain positive frame count")
        for recording in recordings:
            segment = recording["segment"]
            if not str(segment).isdigit():
                raise ValueError("Expected numeric recording directory")
            member = f"data/{segment}/auto_data.csv"
            rows = list(csv.reader(io.StringIO(archive.read(member).decode("utf-8-sig"))))
            rows = [row for row in rows if row and any(value.strip() for value in row)]
            if len(rows) != int(recording["frames"]):
                raise ValueError("Recording count differs from manifest")
            for index, row in enumerate(rows, 1):
                if len(row) not in (5, 6):
                    raise ValueError("Unexpected recording CSV shape")
                images = []
                for column in (1, 0, 2):  # left/front/right, unchanged preprocessing
                    raw = archive.read(confined_member(member, row[column]))
                    image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
                    if image is None:
                        raise ValueError("Unreadable referenced JPEG")
                    images.append(image)
                value = preprocess_arrays(*images)
                expected = golden.run(value)
                split.run(value)
                actual = hybrid.run(value)
                layers = {}
                passed = True
                for spec in split.abi["layer_specs"]:
                    name = spec["name"]
                    layer_input = split.last_ffn_inputs[name]
                    w = split.weights[name]
                    old = baseline.forward(name, layer_input, w)
                    new = candidate.forward(name, layer_input, w)
                    layer_result = compare(old, new)
                    ref = split.last_ffn_outputs[name]
                    layer_result["ort_max_abs_error"] = float(np.max(np.abs(new - ref)))
                    layer_result["ort_pass"] = bool(np.isfinite(new).all() and np.allclose(new, ref, atol=1e-4, rtol=1e-4))
                    if not np.isfinite(ref).all():
                        raise AssertionError("Nonfinite ORT cut point")
                    pipeline = hybrid.last_ffn_outputs[name]
                    if not np.isfinite(pipeline).all():
                        raise AssertionError("Nonfinite hybrid cut point")
                    pipeline_error = float(np.max(np.abs(pipeline - ref)))
                    layer_result["pipeline_max_abs_error"] = pipeline_error
                    layer_result["pipeline_pass"] = pipeline_error <= 1e-3
                    layers[name] = layer_result
                    passed &= (layer_result["pass"] and layer_result["ort_pass"]
                               and layer_result["pipeline_pass"])
                errors = {name: float(np.max(np.abs(actual[name] - expected[name]))) for name in OUTPUT_NAMES}
                if not all(np.isfinite(e) for e in errors.values()):
                    raise AssertionError("Nonfinite model output error")
                mismatch = int(np.count_nonzero(np.trunc(actual["speed_raw"]) != np.trunc(expected["speed_raw"])))
                passed &= all(np.isfinite(e) and e <= 1e-3 for e in errors.values()) and mismatch == 0
                result["frames"].append({"segment": segment, "frame": index, "pass": bool(passed),
                                         "layers": layers, "output_max_abs_error": errors,
                                         "speed_integer_mismatches": mismatch})
                if not passed:
                    result["status"] = "failed"
                if len(result["frames"]) % 10 == 0:
                    print(f"payload frames={len(result['frames'])} status={result['status']}", flush=True)
    result["frames_processed"] = len(result["frames"])
    result["expected_frames"] = expected_frames
    if result["frames_processed"] != expected_frames or not expected_frames:
        raise ValueError("Processed frame count differs from positive manifest total")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-library", required=True, type=Path)
    parser.add_argument("--candidate-library", required=True, type=Path)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--zip", type=Path, dest="zip_path")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if bool(args.bundle) != bool(args.zip_path):
        parser.error("--bundle and --zip must be supplied together")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        report = {"status": "failed", "hardware_execution": False,
                  "scope": "Host std::erf only; no HLS/RTL/PL or PL performance claim",
                  "command": sys.argv, "cwd": str(Path.cwd()), "python": sys.version,
                  "executable": sys.executable, "platform": platform.platform(), "numpy": np.__version__}
        try:
            report["libraries"] = {name: {"path": str(path.resolve()), "sha256": sha256(path)}
                                   for name, path in (("baseline", args.baseline_library), ("candidate", args.candidate_library))}
            baseline, candidate = HostFFN(args.baseline_library), HostFFN(args.candidate_library)
            report["synthetic"] = synthetic(baseline, candidate)
            report["payload"] = (payload_replay(baseline, candidate, args.bundle, args.zip_path)
                                 if args.bundle else {"status": "not_executed"})
            passed = all(case["pass"] for case in report["synthetic"])
            passed &= report["payload"]["status"] in ("pass", "not_executed")
            report["status"] = "pass" if passed else "failed"
        except Exception:
            report["error"] = traceback.format_exc()
            print(report["error"], file=sys.stderr)
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"status": report["status"], "output": str(args.output),
                      "synthetic_cases": len(report.get("synthetic", [])),
                      "payload_frames": report.get("payload", {}).get("frames_processed", 0)}))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
