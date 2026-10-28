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

add("current_pl_realtime_camera_serial", "current PL route", "real camera + serial + motor", "", "", "未执行", "STATUS.md")
add("current_pl_vehicle_task", "current PL route", "fixed-track vehicle run", "", "", "未执行", "STATUS.md")

with (ROOT / "metrics" / "metrics.csv").open("w", encoding="utf-8", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
print(f"wrote {len(rows)} metrics")
