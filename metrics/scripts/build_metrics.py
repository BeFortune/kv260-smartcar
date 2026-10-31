"""Generate metrics.csv for only the latest integrated-simulation scheme."""
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIELDS = ["metric", "scope", "condition", "value", "unit", "status", "evidence_path", "notes"]
rows = []


def load(relative):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def add(metric, scope, condition, value, unit, status, evidence, notes=""):
    rows.append(dict(metric=metric, scope=scope, condition=condition, value=value, unit=unit,
                     status=status, evidence_path=evidence, notes=notes))


sim_path = "metrics/evidence/integrated_sim/summary.json"
verify_path = "metrics/evidence/integrated_sim/independent_verification.json"
sim = load(sim_path)
verify = load(verify_path)
add("integrated_acceptance", "YOLO+integer-reference PL+PS+v2", "635 samples; original 0.5 s diagnostic gate", sim["status"], "conclusion", "失败", sim_path, "Complete run, not HLS/RTL or physical PL")
add("integrated_samples", "integrated offline replay", "4 runs", sim["samples"], "samples", "已验证", sim_path, "All samples executed")
add("integrated_unique_ids", "integrated offline replay", "ordered input contract", sim["unique_sample_ids"], "unique IDs", "已验证", verify_path)
add("integer_reference_kernel_starts", "integer-reference PL", "635 samples", sim["kernel_starts"], "starts", "已验证", verify_path)
add("fault_ticks", "control v2 freshness gate", "latched driver_stale", sim["fault_ticks"], "ticks", "失败", verify_path, "Not independent timeout count")
add("initial_fault_triggers", "control v2 freshness gate", "first stale event before latch", verify["initial_fault_triggers"], "events", "失败", verify_path, "Initial age 0.5462195 s")
add("red_observations", "real YOLO + v2", "635 samples", sim["red_observations"], "observations", "已验证", sim_path)
add("red_stop_decisions", "real YOLO + v2", "6 red observations", sim["red_stop_decisions"], "decisions", "已验证", sim_path, "Offline decisions, not physical braking")
add("valid_packets", "packet encoding", "all final decisions", int(sim["packets_valid_21_bytes"]), "boolean", "已验证", verify_path, "Independent recomputation")
for reason, count in sim["decision_reasons"].items():
    add("decision_reason_" + reason.replace(":", "_"), "control v2 decisions", reason, count, "samples", "已验证", sim_path)
for path_name, label in (("integer_reference_driver_total", "integer-reference driver"), ("yolo_detector", "real YOLO")):
    for stat in ("p50", "p95", "p99", "max"):
        add(f"{path_name}_{stat}", "Windows offline timing", label, sim["timing_ms"][path_name][stat], "ms", "已验证", sim_path, "Not KV260 or physical PL")
for stage in ("pre", "kernel", "dequant", "partb"):
    add(f"driver_{stage}_p50", "Windows offline timing", stage, sim["timing_ms"]["driver_stages"][stage]["p50"], "ms", "已验证", sim_path)

for threshold in ("gt15", "gt40"):
    path = "metrics/evidence/integrated_sim/direction_metrics.json"
    data = load(path)[threshold]
    condition = f"old teacher |d|>{threshold[2:]}; n={data['n']}; not physical truth"
    add(f"strong_reverse_{threshold}", "direction disagreement", condition, data["strong_reverse_count"], "samples", "失败", path, f"rate={data['strong_reverse_rate']:.6f}")
    add(f"turn_half_{threshold}", "turning sensitivity", condition, data["half_count"], "samples", "失败", path, f"rate={data['half_rate']:.6f}")
    add(f"turn_retention_median_{threshold}", "turning sensitivity", condition, data["absolute_retention_median"], "ratio", "失败", path)
    add(f"turn_d_mae_{threshold}", "direction disagreement", condition, data["d_mae"], "speed difference", "失败", path)

hls_path = "fpga/report/hls_fixed_acceptance01.json"
hls = load(hls_path)
add("hls_csim_cases", "PL module HLS C", "120 real validation + 4 other", hls["csim"]["cases"], "cases", "已验证", hls_path)
add("hls_csim_mismatches", "PL module HLS C", "124 cases", hls["csim"]["mismatches"], "bytes", "已验证", hls_path, "5356800 bytes checked")
add("hls_csynth_latency", "PL module synthesis", "static estimate", hls["csynth_estimates"]["Worst-caseLatency"], "cycles/transaction", "已验证", hls_path)
add("rtl_transactions", "PL module RTL cosimulation", "complete three-camera transactions", hls["cosim"]["transactions"], "transactions", "已验证", hls_path, "120-frame full RTL not executed")
add("rtl_mismatches", "PL module RTL cosimulation", "4 transactions", hls["cosim"]["mismatches"], "bytes", "已验证", hls_path, "172800 bytes checked")
add("ooc_wns", "single-IP OOC", "10 ns target", hls["timing"]["wns_ns"], "ns", "已验证", hls_path, "Not system signoff")
add("ooc_whs", "single-IP OOC", "10 ns target", hls["timing"]["whs_ns"], "ns", "已验证", hls_path)

fixed_path = "metrics/evidence/fixed_left/final_status.json"
fixed = load(fixed_path)
add("fixed_left_rows", "fixed-left offline appendix", "pinned left.csv", fixed["route"]["rows"], "rows", "已验证", fixed_path, "No model inference or vehicle")
add("fixed_left_nominal_duration", "fixed-left offline appendix", "nominal 10 Hz slots", fixed["route"]["nominal_duration_s"], "s", "已验证", fixed_path, "Not measured driving time")
for item in fixed["verification"]:
    if "passed" in item:
        add("fixed_left_test_" + item["name"], "fixed-left and regression tests", item["name"], item["passed"], "tests", "已验证", fixed_path, "Separate historical scopes")
    if item["name"] == "16组CLI离线示例":
        add("fixed_left_cli_examples", "fixed-left offline appendix", "virtual clock and MemorySink", item["count"], "examples", "已验证", fixed_path)

add("latest_selected_tests", "latest delivery offline tests", "9 retained test files", 342, "tests", "已验证", "metrics/evidence/local_validation.md", "Integrated, v2, fixed-left, YOLO parser and packet")
add("hls_rtl_integrated_chain", "HLS/RTL integrated YOLO+PL+PS", "same-frame full chain", "", "", "未执行", sim_path)
add("physical_pl_integrated_chain", "physical PL integrated YOLO+PL+PS", "KV260 full chain", "", "", "未执行", sim_path)
add("realtime_15hz_vehicle", "real camera/serial/motor/vehicle", "15 Hz and fixed track", "", "", "未执行", sim_path)

with (ROOT / "metrics" / "metrics.csv").open("w", encoding="utf-8", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
print(f"wrote {len(rows)} metrics")
