"""Read-only analysis of archived ORT study reports; numpy only, no ORT/PYNQ.

Formal latency comes exclusively from perf_* raw samples. Profile durations are
reported separately as diagnostic observations, never as benchmark speedups.
Remote absolute paths are relocated only to the corresponding archived child.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
from pathlib import Path
import sys
import time
import traceback

import numpy as np

OUTPUTS = ("speed_norm", "speed_raw", "region_logits", "region_probs")
QUICK = ("golden4", "split1", "split4", "split4_off", "mixed4_1", "mixed4_1_off")


def load(path):
    return json.loads(Path(path).read_text(encoding="utf8"))


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def stats(values):
    a = np.asarray(values, dtype=np.float64)
    require(a.size > 0 and bool(np.isfinite(a).all()), "Empty/nonfinite samples")
    return {"n": int(a.size), "mean": float(a.mean()), "p50": float(np.percentile(a, 50)),
            "p95": float(np.percentile(a, 95)), "p99": float(np.percentile(a, 99)),
            "min": float(a.min()), "max": float(a.max())}


def basename(path):
    return str(path).replace("\\", "/").rsplit("/", 1)[-1]


def check_digest(path, expected):
    require(Path(path).is_file(), "Missing archived evidence: " + str(path))
    require(digest(path) == expected, "SHA256 mismatch: " + str(path))


def compare_npz(actual, reference):
    with np.load(actual, allow_pickle=False) as a, np.load(reference, allow_pickle=False) as b:
        require(set(a.files) == set(b.files) == set(OUTPUTS), "Output archive members differ")
        errors = {}
        for name in OUTPUTS:
            require(a[name].shape == b[name].shape and a[name].dtype == b[name].dtype == np.float32,
                    "Output shape/dtype differs: " + name)
            require(bool(np.isfinite(a[name]).all()) and bool(np.isfinite(b[name]).all()), "Nonfinite outputs")
            errors[name] = float(np.max(np.abs(a[name] - b[name])))
        mismatches = int(np.count_nonzero(np.trunc(a["speed_raw"]) != np.trunc(b["speed_raw"])))
    require(max(errors.values()) <= 1e-3 and mismatches == 0, "Raw output equivalence gate failed")
    return {"max_abs_error": errors, "speed_integer_mismatches": mismatches, "atol": 1e-3, "rtol": 0}


def validate_policy(report):
    spec = report["policy"]
    require(spec["provider"] == "CPUExecutionProvider" and spec["execution_mode"] == "ORT_SEQUENTIAL" and
            spec["graph_optimization"] == "ORT_ENABLE_EXTENDED" and spec["inter_op_threads"] == 1,
            "Unexpected execution/provider/graph policy")
    require(type(spec["allow_spinning"]) is bool, "Spinning policy must be boolean")
    sessions = report["session_policies"]
    expected_names = {"golden"} if spec["mode"] == "golden" else {
        "stage_0", "stage_1", "stage_2", "stage_3", "stage_4", "camera_0", "camera_1", "cross_0", "cross_1"}
    require(len(sessions) == len(expected_names) and {s["name"] for s in sessions} == expected_names,
            "Unexpected or duplicate session inventory")
    for session in sessions:
        heavy = session["name"] in {"stage_0", "stage_1", "camera_0", "camera_1", "golden"}
        count = spec["heavy_threads"] if heavy else spec["small_threads"]
        require(session["intra_op_threads"] == count and session["inter_op_threads"] == 1 and
                session["allow_spinning"] is spec["allow_spinning"], "Session policy differs from condition")


def formal_row(directory, report, expected_frames):
    require(not report["profiling"], "Profile must never enter formal latency analysis")
    require(report["n"] == expected_frames, "Unexpected formal frame count: " + str(directory))
    require(report["warmup"] == 3, "Formal warmup must be 3")
    samples = report["samples"]
    require(len(samples) == expected_frames and [s["index"] for s in samples] == list(range(expected_frames)),
            "Formal raw sample order/count differs")
    total = stats([s["run_total_ms"] for s in samples])
    for key, value in total.items():
        require(np.isclose(value, report["run_total_ms"][key], rtol=1e-9, atol=1e-9), "Reported total disagrees with raw samples")
    stages = {key: stats([s["stages_ms"][key] for s in samples]) for key in samples[0]["stages_ms"]}
    for key, values in stages.items():
        for metric, value in values.items():
            require(np.isclose(value, report["perstage_ms"][key][metric], rtol=1e-9, atol=1e-9),
                    "Reported perstage disagrees with raw samples: " + key)
    cpu_s, wall_s = report["measurement_loop_cpu_s"], report["measurement_loop_wall_s"]
    require(wall_s > 0 and cpu_s >= 0, "Invalid wall/CPU counters")
    cpu_wall = cpu_s / wall_s
    require(np.isclose(cpu_wall, report["measurement_loop_cpu_wall_ratio"]), "CPU/wall ratio disagrees")
    name_parts = directory.name.split("_", 3)
    return {"run": directory.name, "round": int(name_parts[1][1:]), "ordinal": int(name_parts[2]),
            "condition": report["policy"]["name"], "n": expected_frames, "run_total_ms": total,
            "run_only_rate_hz": 1000 / total["mean"], "process_cpu_ms": stats([s["process_cpu_ms"] for s in samples]),
            "loop_cpu_wall_ratio": cpu_wall,
            "threads_before": report["snapshot_before_measurement"]["task_threads"],
            "threads_after": report["snapshot_after_measurement"]["task_threads"],
            "context_switches": report["process_context_switch_delta"], "perstage_ms": stages,
            "start_utc": report.get("start_utc"), "end_utc": report.get("end_utc"),
            "session_policies": report["session_policies"], "evidence": str(directory / "report.json")}


def profile_session(path):
    events = load(path)
    kernels, fences, scheduling, signatures = [], [], [], collections.Counter()
    pool_names, main_threads, workers = set(), set(), set()
    worker_nonzero_events, main_fields = 0, collections.defaultdict(list)
    by_operator = collections.defaultdict(list)
    for event in events:
        name, arguments = event.get("name", ""), event.get("args", {})
        duration = float(event.get("dur", 0))
        require(np.isfinite(duration) and duration >= 0, "Invalid profile duration")
        if "fence" in name.lower():
            fences.append(duration)
        if "kernel_time" not in name:
            continue
        kernels.append(duration)
        by_operator[arguments.get("op_name", "unknown")].append(duration)
        # Excludes tids, pool names and times; detects identical observed operator
        # inventory/counts, not full graph/topology or graph-fusion equivalence.
        signature = json.dumps({"name": name, "op": arguments.get("op_name"),
                                "provider": arguments.get("provider"),
                                "inputs": arguments.get("input_type_shape"),
                                "outputs": arguments.get("output_type_shape")}, sort_keys=True)
        signatures[signature] += 1
        schedule = arguments.get("thread_scheduling_stats")
        if not schedule:
            continue
        scheduling.append(schedule)
        main = schedule.get("main_thread", {})
        if main.get("thread_pool_name"):
            pool_names.add(str(main["thread_pool_name"]))
        if main.get("thread_id") is not None:
            main_threads.add(str(main["thread_id"]))
        for key, value in main.items():
            if isinstance(value, (int, float)) and key not in ("thread_id", "core"):
                main_fields[key].append(float(value))
        subs = schedule.get("sub_threads", {})
        workers.update(str(key) for key in subs)
        worker_nonzero_events += int(any(value.get("num_run", 0) > 0 for value in subs.values()))
    return {"event_count": len(events), "kernel_event_count": len(kernels),
            "profile_only_kernel_duration_us": stats(kernels) if kernels else None,
            "profile_only_kernel_total_us": sum(kernels), "fence_event_count": len(fences),
            "profile_only_fence_total_us": sum(fences), "scheduling_event_count": len(scheduling),
            "pool_names": sorted(pool_names), "main_thread_ids": sorted(main_threads),
            "worker_thread_ids": sorted(workers), "events_with_nonzero_worker_num_run": worker_nonzero_events,
            "main_numeric_fields_observed": {key: stats(values) for key, values in main_fields.items()},
            "profile_only_operators": {key: {"count": len(values), "total_us": sum(values)} for key, values in by_operator.items()},
            "operator_inventory_sha256": hashlib.sha256(json.dumps(sorted(signatures.items())).encode()).hexdigest(),
            "evidence": str(path), "sha256": digest(path)}


def paired_rows(rows):
    pairs = []
    for round_number in sorted({r["round"] for r in rows}):
        conditions = {r["condition"]: r for r in rows if r["round"] == round_number}
        for on_name, off_name in (("split4", "split4_off"), ("mixed4_1", "mixed4_1_off")):
            if on_name not in conditions or off_name not in conditions:
                continue
            on, off = conditions[on_name], conditions[off_name]
            pairs.append({"round": round_number, "on": on_name, "off": off_name,
                          "p50_ratio_on_over_off": on["run_total_ms"]["p50"] / off["run_total_ms"]["p50"],
                          "mean_ratio_on_over_off": on["run_total_ms"]["mean"] / off["run_total_ms"]["mean"],
                          "p95_ratio_on_over_off": on["run_total_ms"]["p95"] / off["run_total_ms"]["p95"],
                          "cpu_wall_on": on["loop_cpu_wall_ratio"], "cpu_wall_off": off["loop_cpu_wall_ratio"],
                          "threads_on": on["threads_after"], "threads_off": off["threads_after"],
                          "thread_count_equal": on["threads_after"] is not None and on["threads_after"] == off["threads_after"],
                          "perstage_mean_reduction_ms": {key: values["mean"] - off["perstage_ms"][key]["mean"]
                                                        for key, values in on["perstage_ms"].items()}})
    return pairs


def analyze(directory, expected_frames=117, require_quick=True):
    directory = Path(directory).resolve()
    manifest = load(directory / "study.json")
    require(manifest["status"] == "已验证" and manifest["exit_code"] == 0, "Top-level study did not pass")
    require(not manifest["hardware_execution"], "Not a CPU-only study")
    source_hashes = {key.replace("\\", "/"): value for key, value in manifest["source_hashes"].items()}
    checks, rows, profiles, reports = [], [], [], []
    if (directory / "inputs.npy").is_file():
        check_digest(directory / "inputs.npy", load(directory / "inputs.json")["snapshot_sha256"])
        checks.append("archived input snapshot SHA256")
    else:
        checks.append("input snapshot absent; only cross-report digest equality checked")
    children = sorted(p for p in directory.iterdir() if p.is_dir() and (p / "report.json").is_file())
    require(bool(children), "No worker reports")
    output_paths = []
    for child in children:
        report, process, job = load(child / "report.json"), load(child / "process.json"), load(child / "job.json")
        reports.append(report)
        validate_policy(report)
        require(report["status"] == "已验证" and report["exit_code"] == 0 and process["exit_code"] == 0,
                "Nonzero/failed worker: " + str(child))
        require(report["manifest_sha256"] == manifest["manifest_sha256"], "Cross-worker manifest changed")
        require(report["manifest_sha256"] == job["manifest_sha256"] and report["input_sha256"] == job["inputs_sha256"],
                "Worker/job hashes differ")
        require(report["runtime_sha256"] == source_hashes["ps/runtime.py"], "Runtime digest differs")
        require(not report["hardware_execution"], "Worker is not CPU-only")
        comparison = report.get("comparison")
        if job.get("reference"):
            require(comparison and comparison["pass"] and comparison["speed_integer_mismatches"] == 0,
                    "Worker numerical/integer comparison failed")
            require(comparison["atol"] == 1e-3 and comparison["rtol"] == 0 and
                    max(comparison["max_abs_error"].values()) <= 1e-3, "Wrong numerical tolerance/error")
        output = child / "outputs.npz"
        if output.is_file():
            check_digest(output, report["output_sha256"])
            output_paths.append((child, output, job))
        else:
            checks.append("output archive absent: " + child.name + "; comparison report only")
        if child.name.startswith("perf_"):
            rows.append(formal_row(child, report, expected_frames))
        elif report["profiling"]:
            sessions = {}
            for name, summary in report["profile_summary"].items():
                path = child / "profiles" / basename(summary["path"])
                check_digest(path, summary["sha256"])
                sessions[name] = profile_session(path)
            worker_ids = {tid for s in sessions.values() for tid in s["worker_thread_ids"]}
            profiles.append({"condition": report["policy"]["name"], "n": report["n"], "warmup": report["warmup"],
                             "includes_warmup": True, "sessions": sessions,
                             "distinct_worker_threads_across_sessions": len(worker_ids),
                             "session_count": len(sessions), "evidence": str(child / "report.json")})
    for child, output, job in output_paths:
        if job.get("reference"):
            parts = str(job["reference"]).replace("\\", "/").split("/")
            reference = directory / parts[-2] / parts[-1]
            if reference.is_file():
                check_digest(reference, job["reference_sha256"])
                compare_npz(output, reference)
                checks.append("raw four-output and integer recheck: " + child.name)
    golden_paths = [path for child, path, job in output_paths if child.name.startswith("reference_")]
    if golden_paths:
        for path in golden_paths[1:]:
            compare_npz(path, golden_paths[0])
        checks.append("available raw golden references crosschecked")
    require(len({r["input_sha256"] for r in reports}) == 1, "Worker input snapshot hashes differ")
    require(len({json.dumps(r["versions"], sort_keys=True) for r in reports}) == 1, "Worker environments differ")
    ordered = sorted(rows, key=lambda r: (r["round"], r["ordinal"]))
    require(len(ordered) == sum(len(order) for order in manifest["orders"]), "Missing/extra formal reports")
    for number, order in enumerate(manifest["orders"], 1):
        observed = [r["condition"] for r in ordered if r["round"] == number]
        require(observed == order, "Observed formal order differs from declared order")
    if require_quick:
        require(manifest["orders"] == [list(QUICK), list(reversed(QUICK))], "Expected six conditions in forward/reverse order")
    graph_comparison = {}
    by_condition = {p["condition"]: p for p in profiles}
    if "split4" in by_condition and "split4_off" in by_condition:
        on, off = by_condition["split4"]["sessions"], by_condition["split4_off"]["sessions"]
        require(set(on) == set(off), "Profile session inventories differ")
        graph_comparison = {name: on[name]["operator_inventory_sha256"] == off[name]["operator_inventory_sha256"] for name in on}
    pairs = paired_rows(ordered)
    split_pairs = [p for p in pairs if p["on"] == "split4"]
    strong_spin_evidence = len(split_pairs) >= 2 and all(p["p50_ratio_on_over_off"] > 2 and
                              p["cpu_wall_on"] > p["cpu_wall_off"] and p["thread_count_equal"] for p in split_pairs)
    return {"status": "已验证", "analysis_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "study_directory": str(directory), "study_json_sha256": digest(directory / "study.json"),
            "versions": reports[0]["versions"], "checks": checks, "formal_runs": ordered,
            "spin_pairs": pairs, "profiles_diagnostic_only": profiles,
            "split4_on_off_observed_operator_inventory_equal": graph_comparison,
            "interpretation": {
                "strong_repeated_spin_intervention_evidence": strong_spin_evidence,
                "meaning": "若为true，同图/输入/线程数下仅关闭忙等，两种顺序都显著变快且CPU占用下降：强力支持多session空闲线程忙等争抢CPU是主要可消除开销；不是减少线程总数带来的收益。",
                "limits": ["不宣称唯一根因，不把profile时长当正式速度；没有独立温度/频率控制就不能完全排除热/频率因素。",
                           "ORT operator inventory相同只证明观察到的算子类型/数量/shape一致，不证明完整优化图拓扑一致。",
                           "Fence事件为0或调度统计缺失不证明没有等待；main字段和num_run仅按原profile观测解释，不推断未记录的spin周期。",
                           "run_only_rate_hz排除解码/预处理与实时采集发送，不能冒充整机15Hz或实车验收。",
                           "FFN输入/输出捕获复制保留，结论仅覆盖本研究相同run合同；没有验证split-PL。"]},
            "production_proposal_not_implemented": {
                "minimal": "Runtime.__init__(bundle, mode='split-ort', threads=1, backend=None, *, spinning=None)",
                "contract": "spinning仅接受None或精确bool；None完全保留旧SessionOptions行为，不写config；False写session.intra_op.allow_spinning=0和session.inter_op.allow_spinning=0；True显式写1。四线程仍由threads=4指定，不改变golden默认、模型、图优化或run。",
                "usage": "Runtime(bundle, mode='split-ort', threads=4, spinning=False)",
                "optional_deferred": "per_stage_threads可选mapping先不落地；若将来采用，仅允许当前schedule stage name、整数1..4，拒绝bool/未知key，未覆盖stage沿用threads；golden非空mapping拒绝。",
                "acceptance": "生产参数需主代理批准后实现；旧调用和golden默认回归、非法参数拒绝、两config精确核对，再用生产构造在板上独立CPU复验。"}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study-dir", type=Path, required=True)
    parser.add_argument("--expected-frames", type=int, default=117)
    parser.add_argument("--allow-partial-matrix", action="store_true", help="Offline smoke tests only")
    parser.add_argument("--output", type=Path, required=True, help="New analysis directory, never overwrite")
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=False)
    try:
        report = analyze(args.study_dir, args.expected_frames, not args.allow_partial_matrix)
        exit_code = 0
    except Exception as error:
        report = {"status": "失败", "first_error": str(error), "traceback": traceback.format_exc()}
        traceback.print_exc()
        exit_code = 1
    report["actual_command"], report["exit_code"] = sys.argv, exit_code
    with (args.output / "analysis.json").open("x", encoding="utf8") as stream:
        json.dump(report, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"status": report["status"], "exit_code": exit_code, "output": str(args.output / "analysis.json")}, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
