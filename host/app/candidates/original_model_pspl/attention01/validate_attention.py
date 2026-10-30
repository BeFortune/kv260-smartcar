"""Fixed four-output gate and offline replay; thresholds never accepted from CLI."""
from __future__ import annotations
import numpy as np
from attention_rewrite import OUTPUT_NAMES

OUTPUT_SHAPES = {"speed_norm": (1, 2), "speed_raw": (1, 2),
                 "region_logits": (1, 15), "region_probs": (1, 15)}
ATOL = .001


def gate_outputs(reference, actual):
    if set(reference) != set(OUTPUT_NAMES) or set(actual) != set(OUTPUT_NAMES):
        raise ValueError("All four outputs are mandatory")
    finite = True
    maxima = {}
    for name in OUTPUT_NAMES:
        left, right = reference[name], actual[name]
        if (not isinstance(left, np.ndarray) or not isinstance(right, np.ndarray)
                or left.dtype != np.float32 or right.dtype != np.float32
                or left.shape != OUTPUT_SHAPES[name] or right.shape != OUTPUT_SHAPES[name]):
            raise ValueError(f"Output dtype/shape contract failed: {name}")
        if not (np.isfinite(left).all() and np.isfinite(right).all()):
            finite = False
            maxima[name] = None
        else:
            maxima[name] = float(np.max(np.abs(left.astype(np.float64) - right.astype(np.float64))))
    mismatches = int(np.count_nonzero(np.trunc(reference["speed_raw"]) != np.trunc(actual["speed_raw"])))
    passed = finite and all(v is not None and v <= ATOL for v in maxima.values()) and mismatches == 0
    return {"status": "已验证" if passed else "失败", "finite": finite, "max_abs": maxima,
            "speed_integer_mismatches": mismatches, "atol": ATOL}


def validate_dataset(bundle, data, output_dir):
    import csv
    from datetime import datetime, timezone
    import hashlib
    import json
    from pathlib import Path
    import platform
    import time
    import onnx
    import onnxruntime as ort
    from attention_rewrite import HERE, SOURCE_PATH, SOURCE_SHA256, _dump
    from runtime_cpu import BundleRuntime, session, verify_bundle
    output_dir = Path(output_dir).resolve()
    if output_dir.exists():
        raise FileExistsError("Refusing existing validation evidence")
    if HERE not in output_dir.parents:
        raise ValueError("Evidence must be confined to attention01")
    bundle = Path(bundle).resolve()
    manifest, abi = verify_bundle(bundle)
    source_before = hashlib.sha256(SOURCE_PATH.read_bytes()).hexdigest()
    output_dir.mkdir(parents=True, exist_ok=False)
    _dump(output_dir / "data_identity.json", data.provenance)
    checker = {}
    for name in ("golden.onnx", "single_camera_full.onnx", "full.onnx", "stage_0.onnx", "stage_1.onnx", "stage_2.onnx", "camera_0.onnx", "camera_1.onnx"):
        onnx.checker.check_model(onnx.load(bundle / name), full_check=True)
        checker[name] = "已验证"
    sessions = {"golden": session(bundle / "golden.onnx"),
                "single_camera": session(bundle / "single_camera_full.onnx"),
                "candidate": session(bundle / "full.onnx"),
                "golden_noopt": session(bundle / "golden.onnx", "disabled"),
                "candidate_noopt": session(bundle / "full.onnx", "disabled")}
    split = BundleRuntime(bundle)
    variants = ["golden", "single_camera", "candidate", "candidate_split", "golden_noopt", "candidate_noopt"]
    comparisons = {"single_camera": "golden", "candidate": "golden", "candidate_split": "golden",
                   "candidate_split_vs_full": "candidate", "candidate_noopt": "golden_noopt"}
    accumulated = {variant: {"status": "已验证", "max_abs": {name: 0.0 for name in OUTPUT_NAMES},
                             "worst_sample": {name: None for name in OUTPUT_NAMES},
                             "finite": True, "speed_integer_mismatches": 0, "failed_frames": 0}
                   for variant in comparisons}
    n = len(data.sample_ids)
    predictions = {name: np.empty((n, len(variants), *shape), dtype=np.float32) for name, shape in OUTPUT_SHAPES.items()}
    csv_path = output_dir / "per_sample.csv"
    failure_records = []
    total_started = time.perf_counter()
    with csv_path.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["index", "sample_id", "segment", "variant", "reference", "status", "finite", "speed_integer_mismatches", *[name + "_max_abs" for name in OUTPUT_NAMES]])
        writer.writeheader()
        for i in range(n):
            images = data.input(i)
            outputs = {variant: dict(zip(OUTPUT_NAMES, current.run(OUTPUT_NAMES, {"images": images})))
                       for variant, current in sessions.items()}
            outputs["candidate_split"] = split.run(images)
            for variant, reference_name in comparisons.items():
                actual_name = "candidate_split" if variant == "candidate_split_vs_full" else variant
                gate = gate_outputs(outputs[reference_name], outputs[actual_name])
                bucket = accumulated[variant]
                bucket["finite"] = bucket["finite"] and gate["finite"]
                bucket["speed_integer_mismatches"] += gate["speed_integer_mismatches"]
                if gate["status"] == "失败":
                    bucket["status"] = "失败"
                    bucket["failed_frames"] += 1
                    failure_records.append({"index": i, "sample_id": str(data.sample_ids[i]), "variant": variant,
                                            "reference": reference_name, **gate})
                for name, error in gate["max_abs"].items():
                    if error is None:
                        bucket["max_abs"][name] = None
                        bucket["worst_sample"][name] = str(data.sample_ids[i])
                    elif bucket["max_abs"][name] is not None and error >= bucket["max_abs"][name]:
                        bucket["max_abs"][name] = error
                        bucket["worst_sample"][name] = str(data.sample_ids[i])
                writer.writerow({"index": i, "sample_id": str(data.sample_ids[i]), "segment": str(data.segments[i]),
                                 "variant": variant, "reference": reference_name,
                                 "status": gate["status"], "finite": gate["finite"],
                                 "speed_integer_mismatches": gate["speed_integer_mismatches"],
                                 **{name + "_max_abs": value for name, value in gate["max_abs"].items()}})
            for vi, variant in enumerate(variants):
                for name in OUTPUT_NAMES:
                    predictions[name][i, vi] = outputs[variant][name]
            # Failed gates remain recorded; run every originally locked sample, never tune or resample.
            if (i + 1) % 50 == 0 or i + 1 == n:
                stream.flush()
                print(f"{data.name} {i+1}/{n} failures={len(failure_records)}", flush=True)
    with (output_dir / "predictions.npz").open("xb") as stream:
        np.savez_compressed(stream, sample_ids=data.sample_ids, segments=data.segments,
                            variant_names=np.array(variants), output_names=np.array(OUTPUT_NAMES), **predictions)
    _dump(output_dir / "failures.json", failure_records)
    after = hashlib.sha256(SOURCE_PATH.read_bytes()).hexdigest()
    status = "已验证" if all(item["status"] == "已验证" for item in accumulated.values()) and source_before == after == SOURCE_SHA256 else "失败"
    report = {"schema": "pynq_ffn.attention_equivalence.v1", "status": status,
              "date_utc": datetime.now(timezone.utc).isoformat(), "dataset": data.name,
              "frames_processed": n, "gate": {"max_abs_each_output": ATOL, "rtol": 0, "finite": True,
                                                "speed_integer_mismatches_allowed": 0},
              "source_sha256_before": source_before, "source_sha256_after": after,
              "candidate_sha256": manifest["candidate_sha256"], "onnx_checker": checker,
              "comparisons": accumulated, "variants": variants,
              "cpu_settings": {"threads": 4, "inter_threads": 1, "spinning": False, "provider": "CPUExecutionProvider",
                               "primary_optimization": "ORT_ENABLE_EXTENDED", "secondary_optimization": "ORT_DISABLE_ALL"},
              "host": {"platform": platform.platform(), "machine": platform.node(), "numpy": np.__version__, "onnx": onnx.__version__, "ort": ort.__version__},
              "elapsed_s_not_benchmark": time.perf_counter() - total_started,
              "predictions": str(output_dir / "predictions.npz"), "per_sample": str(csv_path),
              "failures": str(output_dir / "failures.json"), "hardware_execution": False,
              "not_executed": ["KV260", "PL", "hardware state changes", "vehicle", "training", "quantization"]}
    _dump(output_dir / "report.json", report)
    return report


def main():
    import argparse
    import json
    from pathlib import Path
    from replay_data import load_payload, load_field
    from attention_rewrite import _dump
    parser = argparse.ArgumentParser(description="Full 173 and 635 pinned camera candidate gates; no tolerance override")
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    from attention_rewrite import HERE
    args.output = args.output.resolve()
    if HERE not in args.output.parents:
        raise ValueError("Validation output must be confined to attention01")
    if args.output.exists():
        raise FileExistsError("Refusing an existing validation run")
    args.output.mkdir(parents=True, exist_ok=False)
    reports = []
    for name, loader in [("payload_173", load_payload), ("field_635", load_field)]:
        reports.append(validate_dataset(args.bundle, loader(), args.output / name))
    passed = all(report["status"] == "已验证" for report in reports)
    promotion = {"schema": "pynq_ffn.attention_promotion.v1", "status": "已验证" if passed else "失败",
                 "candidate_sha256": reports[0]["candidate_sha256"],
                 "promotion": "offline_numerical_gates_only" if passed else "blocked_by_locked_gate",
                 "datasets": {report["dataset"]: {"status": report["status"], "frames": report["frames_processed"],
                                                   "report": str(args.output / report["dataset"] / "report.json")}
                              for report in reports},
                 "board_15hz_status": "阻塞", "hardware_execution": False,
                 "stop_policy": "Failure preserves evidence and forbids candidate promotion; no threshold relaxation or numerical tuning"}
    _dump(args.output / "promotion.json", promotion)
    print(json.dumps(promotion, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
