"""Generate metrics.csv only from the evidence shipped in this repository."""
import csv
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "metrics" / "evidence"
FIELDS = ["metric", "scope", "condition", "value", "unit", "status", "evidence_path", "notes"]
rows = []


def load(name):
    return json.loads((EVIDENCE / name).read_text(encoding="utf-8"))


def add(metric, scope, condition, value, unit, status, evidence, notes=""):
    rows.append(dict(metric=metric, scope=scope, condition=condition, value=value, unit=unit,
                     status=status, evidence_path=evidence, notes=notes))


hls_path = "fpga/report/hls_fixed_acceptance01.json"
hls = json.loads((ROOT / hls_path).read_text(encoding="utf-8"))
add("csim_cases", "HLS C simulation", "120 real validation + 4 other", hls["csim"]["cases"], "cases", "已验证", hls_path)
add("csim_mismatches", "HLS C simulation", "124 cases", hls["csim"]["mismatches"], "bytes", "已验证", hls_path, "5356800 output bytes checked")
add("csynth_latency", "HLS C synthesis", "100 MHz target; static estimate", hls["csynth_estimates"]["Worst-caseLatency"], "cycles/transaction", "已验证", hls_path, "Not board latency")
add("rtl_transactions", "RTL cosimulation", "complete three-camera transactions", hls["cosim"]["transactions"], "transactions", "已验证", hls_path, "120-frame full RTL not executed")
add("rtl_mismatches", "RTL cosimulation", "4 transactions", hls["cosim"]["mismatches"], "bytes", "已验证", hls_path, "172800 output bytes checked")
add("rtl_latency_average", "RTL cosimulation", "4 transactions", hls["cosim"]["latency_cycles_avg"], "cycles/transaction", "已验证", hls_path)
add("ooc_wns", "single-IP OOC implementation", "10 ns target", hls["timing"]["wns_ns"], "ns", "已验证", hls_path, "External I/O delays incomplete; not system signoff")
add("ooc_whs", "single-IP OOC implementation", "10 ns target", hls["timing"]["whs_ns"], "ns", "已验证", hls_path)
for name, values in hls["postroute_resources"].items():
    add("ooc_resource_" + name.lower().replace(" ", "_"), "single-IP OOC implementation", "post-route", values["used"], f"of {values['available']} ({values['percent']}%)", "已验证", hls_path)

board_path = "metrics/logs/pl_board_run01_report.json"
board = load("../logs/pl_board_run01_report.json")
add("pl_numeric_frames", "KV260 real PL", "2026-10-21 recording replay", board["numeric"]["frames"], "frames", "已验证", board_path)
add("pl_numeric_mismatches", "KV260 real PL vs integer reference", "117 frames", board["numeric"]["pl_vs_reference_mismatch_bytes"], "bytes", "已验证", board_path, "5054400 bytes checked")
add("pl_speed_max_error_vs_int8", "KV260 real PL vs CPU INT8 ORT", "117 frames", board["numeric"]["speed_pl_vs_cpu_int8_ort"]["max_abs"], "speed_raw", "已验证", board_path)
add("pl_speed_max_error_vs_fp32", "KV260 real PL vs CPU FP32", "117 frames; includes quantization", board["numeric"]["speed_pl_vs_cpu_fp32"]["max_abs"], "speed_raw", "已验证", board_path)
for path_name, condition in (("pl", "PS+PL"), ("cpu_fp32", "CPU FP32")):
    for stat in ("p50", "p95", "p99", "max"):
        add(f"inference_total_{stat}", "KV260 same-process timing", f"{condition}; 4 threads; 300 iterations; warmup 20", board["timing_ms"][path_name]["total"][stat], "ms", "已验证", board_path, "JPEG decode excluded")
for part in ("pre_lut", "kernel", "partb"):
    add(f"pl_{part}_p50", "KV260 PS+PL component timing", "4 threads; 300 iterations", board["timing_ms"]["pl"][part]["p50"], "ms", "已验证", board_path)
add("speedup_vs_cpu_fp32_p50", "KV260 same-process timing", "PS+PL versus CPU FP32", board["speedup_p50"], "ratio", "已验证", board_path, "Not a speedup claim versus CPU INT8")
add("kernel_starts", "KV260 real PL", "2026-10-21 run", board["kernel_starts"], "starts", "已验证", board_path, "Final idle=true; clock writes=0")

cpu_path = "metrics/evidence/cpu_timing_run01.json"
cpu = load("cpu_timing_run01.json")
for condition in ("s16_j1c2_cut2int8_full_t4", "s16_j1c2_trained_full_t4"):
    for stat in ("p50", "p95", "p99"):
        add(f"cpu_total_{stat}", "KV260 pure CPU ORT", condition, cpu["conditions"][condition][stat], "ms", "已验证", cpu_path, "No PYNQ imported; separate run from PL timing")
add("acceleration_vs_cpu_int8", "end-to-end comparison", "PS+PL 19.6545 ms versus CPU INT8 17.9221 ms", "not_faster", "conclusion", "失败", cpu_path, "Do not claim acceleration over CPU INT8")

loop_path = "metrics/evidence/car_loop_board_run01.json"
loop = load("car_loop_board_run01.json")
add("scheduled_speed_frames", "recording-driven inference loop", "20 seconds; loaded PL; no camera/serial", loop["loop"]["speed_frames"], "frames", "已验证", loop_path)
for stat in ("p50", "p95", "p99", "max"):
    add(f"send_interval_{stat}", "recording-driven inference loop", "300 speed frames in 20 seconds", loop["loop"]["send_interval_ms"][stat], "ms", "已验证", loop_path, "Not physical serial or vehicle timing")
    add(f"loop_inference_total_{stat}", "recording-driven inference loop", "351 timed inferences", loop["timing_ms"]["total"][stat], "ms", "已验证", loop_path, "JPEG decode/camera/serial excluded")
add("loop_kernel_starts", "recording-driven inference loop", "2026-10-22 run", loop["kernel_starts"], "starts", "已验证", loop_path)

field_path = "metrics/evidence/field_turning_diagnosis.md"
field = (ROOT / field_path).read_text(encoding="utf-8")
match = re.search(r"新 FP32 保留率中位数 `([0-9.]+)`", field)
if not match:
    raise RuntimeError("field turning retention metric not found")
add("field_turning_retention_median", "offline field diagnosis", "198 old-model |d|>40 reference frames", match.group(1), "ratio", "失败", field_path, "New model cannot safely replace the old CPU fallback")

original_path = "metrics/evidence/original_model/final_status.json"
original = load("original_model/final_status.json")
runtime_path = "metrics/evidence/original_model/runtime_recheck_summary.json"
runtime = load("original_model/runtime_recheck_summary.json")
for dataset in runtime["datasets"]:
    max_abs = max(value for mode in dataset["max_abs"].values() for value in mode.values())
    integer_mismatch = sum(dataset["integer_mismatch_samples"].values())
    add("original_candidate_max_abs", "original-model CPU candidate", dataset["dataset"], max_abs, "absolute error", "已验证", runtime_path, "Full and split; four outputs; no PL")
    add("original_candidate_integer_mismatches", "original-model CPU candidate", dataset["dataset"], integer_mismatch, "samples/comparisons as recorded", "已验证", runtime_path)
add("original_candidate_host_mean", "desktop CPU benchmark", "golden; 4 threads; 346 calls", original["host_benchmark"]["mean_golden_ms"], "ms", "已验证", original_path, "Not KV260; preprocessing and PL excluded")
add("original_candidate_host_mean", "desktop CPU benchmark", "layout candidate; 4 threads; 346 calls", original["host_benchmark"]["mean_candidate_ms"], "ms", "已验证", original_path, "Mean improves; maximum regresses")
add("original_candidate_host_max", "desktop CPU benchmark", "layout candidate", original["host_benchmark"]["max_candidate_ms"], "ms", "已验证", original_path, "Worse than golden max 7.4283 ms")
add("original_candidate_15hz", "original-model real PS+PL goal", "new HLS/PL/board unavailable", "", "", "阻塞", original_path)

road_path = "metrics/evidence/roadsign/full_replay_summary.json"
road = load("roadsign/full_replay_summary.json")
for stat in ("p50", "p95", "p99", "max"):
    add(f"roadsign_detector_{stat}", "desktop CPU offline replay", "635 recorded samples; YOLO 1 thread", road["detector_latency_ms"][stat], "ms", "已验证", road_path, "Not KV260 or real camera")
road_status = load("roadsign/final_status.json")
road_timing = next(item for item in road_status["stages"] if "cpu_total_p50_ms" in item)
add("roadsign_cpu_total_p50", "desktop CPU offline replay", "driver 4 threads + YOLO 1 thread + arbiter", road_timing["cpu_total_p50_ms"], "ms", "已验证", "metrics/evidence/roadsign/final_status.json", "Exceeds 66.7 ms; not board performance")
add("roadsign_serial_15hz_budget", "desktop serial path versus 15 Hz", "offline diagnostic", "over_budget", "conclusion", "失败", "metrics/evidence/roadsign/final_status.json")

v2_counts_path = "metrics/evidence/control_v2/test_counts.json"
v2_counts = load("control_v2/test_counts.json")
for name in ("final_new", "final_legacy"):
    add(f"control_v2_{name}_passed", "offline directed pytest", name, v2_counts[name]["passed"], "tests", "已验证", v2_counts_path, "Separate counts; not added to historical totals")
v2_path = "metrics/evidence/control_v2/saved_replay_summary.json"
v2 = load("control_v2/saved_replay_summary.json")
add("control_v2_red_observations", "saved real detections through new policy", "635 samples; no new inference", v2["red_positive_frames"], "samples", "已验证", v2_path)
add("control_v2_red_stop_decisions", "saved real detections through new policy", "6 red observations", v2["red_positive_stop_decisions"], "decisions", "已验证", v2_path, "Old v1 was 0/6; not physical braking")
add("control_v2_confirmation_coverage", "saved sparse detections", "gaps above fixed 0.5 s confirmation window", v2["capture_gap_over_confirmation_limit"], "gaps", "未执行", v2_path, "No widening to force junction/crosswalk confirmations")
add("control_v2_vehicle_activation", "control v2 vehicle deployment", "no vehicle entry or junction executor", "", "", "阻塞", "metrics/evidence/control_v2/final_status.json")

add("current_pl_realtime_camera_serial", "current PL route", "real camera + serial + motor", "", "", "未执行", "STATUS.md")
add("current_pl_vehicle_task", "current PL route", "fixed-track vehicle run", "", "", "未执行", "STATUS.md")

with (ROOT / "metrics" / "metrics.csv").open("w", encoding="utf-8", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
print(f"wrote {len(rows)} metrics")
