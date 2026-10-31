"""Build the latest integrated-simulation delivery whitelist and manifest."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT.parent / "pynq_ffn"
BASE_COMMIT = "a23661e70a2d9da1206ddb32ecf93f045f32b4ad"
COPIES = {
    "LICENSE": "LICENSE",
    "fpga/build/conv_front_int8_20261021.bit": "artifacts/route_b_quant_20261020/system_cfs1/conv_front_int8_20261021.bit",
    "fpga/build/conv_front_int8_20261021.hwh": "artifacts/route_b_quant_20261020/system_cfs1/conv_front_int8_20261021.hwh",
    "fpga/report/hls_fixed_acceptance01.json": "artifacts/route_b_quant_20261020/hls_fixed_acceptance01.json",
    "fpga/report/ooc_timing_routed01.rpt": "artifacts/route_b_quant_20261020/ooc_timing_routed01.rpt",
    "host/model/s16_j1c2_cut2int8_ps.onnx": "artifacts/route_b_quant_20261020/cut2_01_board/s16_j1c2_cut2int8_ps.onnx",
    "host/model/params.json": "hls/conv_front_int8/params.json",
    "host/model/ref_int8.py": "hls/conv_front_int8/ref_int8.py",
    "host/deploy/requirements-prepare.txt": "requirements-prepare.txt",
    "host/deploy/requirements-runtime.txt": "requirements-runtime.txt",
    "host/deploy/requirements-test.txt": "requirements-test.txt",
    "metrics/evidence/system_artifact_verification.json": "artifacts/route_b_quant_20261020/system_cfs1/artifact_verification.json",
    "metrics/evidence/integrated_sim/summary.json": "artifacts/integrated_sim_20261031/full01/summary.json",
    "metrics/evidence/integrated_sim/input_contract.json": "artifacts/field_capture_analysis_20261026/replay_identity02/contract.json",
    "metrics/evidence/integrated_sim/policy_scope.json": "artifacts/roadsign_control_v2_20261030/run01/saved_replay_scope.json",
    "metrics/evidence/integrated_sim/independent_verification.json": "artifacts/integrated_sim_20261031/full01/independent_verification.json",
    "metrics/evidence/integrated_sim/direction_metrics.json": "artifacts/integrated_sim_20261031/full01/direction_metrics.json",
    "metrics/evidence/integrated_sim/fault_samples.json": "artifacts/integrated_sim_20261031/full01/fault_samples.json",
    "metrics/logs/integrated_sim_predictions.jsonl": "artifacts/integrated_sim_20261031/full01/predictions.jsonl",
    "metrics/evidence/control_v2/test_counts.json": "artifacts/roadsign_control_v2_20261030/run01/implementation01/revision02/final_test_counts.json",
    "metrics/evidence/control_v2/interface.json": "artifacts/roadsign_control_v2_20261030/run01/implementation01/interface.json",
    "metrics/evidence/fixed_left/final_status.json": "artifacts/fixed_left_fallback_20261031/run01/final_status.json",
    "metrics/evidence/fixed_left/final_results.json": "artifacts/fixed_left_fallback_20261031/run01/implementation01/revision01/final_results.json",
    "metrics/evidence/fixed_left/demo_15hz_summary.json": "artifacts/fixed_left_fallback_20261031/run01/implementation01/revision01/demo_complete_15hz/summary.json",
    "metrics/evidence/fixed_left/independent_check.json": "artifacts/fixed_left_fallback_20261031/run01/integration01/independent_check.json",
    "metrics/evidence/direction_closeout/statistics.json": "artifacts/direction_closeout_20261031/run01/results/statistics.json",
    "metrics/evidence/direction_closeout/final_status.json": "artifacts/direction_closeout_20261031/run01/final_status.json",
    "host/app/tools/integrated_sim_replay.py": "tools/integrated_sim_replay.py",
    "host/app/tools/control_v2_replay.py": "tools/control_v2_replay.py",
    "host/app/tools/fixed_left_replay.py": "tools/fixed_left_replay.py",
    "host/app/tests/test_integrated_sim_replay.py": "tests/test_integrated_sim_replay.py",
    "host/app/tests/test_roadsign.py": "tests/test_roadsign.py",
    "host/app/tests/test_control_v2_fixed_left.py": "tests/test_control_v2_fixed_left.py",
    "host/app/tests/test_packet.py": "tests/test_packet.py",
    "host/app/tests/golden/side_speed_golden.txt": "tests/golden/side_speed_golden.txt",
    "host/app/artifacts/roadsign_migration_20261027/run01/fixtures/constant_pedestrian.onnx": "artifacts/roadsign_migration_20261027/run01/fixtures/constant_pedestrian.onnx",
    "host/app/assets/fixed_left/left.csv": "ros_craic_car-main/src/base_control/resource/left.csv",
}
for name in ("conv_front_int8.cpp", "conv_front_int8.hpp", "testbench.cpp", "weights_gen.hpp", "ref_int8.py", "params.json"):
    COPIES[f"fpga/src/{name}"] = f"hls/conv_front_int8/{name}"
COPIES["fpga/src/build/hls.cfg"] = "hls/conv_front_int8/build/hls.cfg"
for index in range(4):
    for kind in ("input", "output"):
        name = f"{kind}_{index:03d}.bin"
        COPIES[f"fpga/src/vectors/{name}"] = f"hls/conv_front_int8/vectors/{name}"
COPIES["fpga/src/vectors/cases.txt"] = "hls/conv_front_int8/vectors/cases.txt"
for name in ("__init__.py", "packet.py", "car_loop.py", "pl_infer.py", "preprocess.py", "cpu_infer.py", "roadsign.py"):
    COPIES[f"host/app/ps/{name}"] = f"ps/{name}"
for name in ("__init__.py", "audit.py", "controller.py", "policy.py", "sender.py", "snapshots.py", "workers.py", "fixed_left.py"):
    COPIES[f"host/app/ps/control_v2/{name}"] = f"ps/control_v2/{name}"
for name in (
    "test_control_v2_controller.py", "test_control_v2_policy.py", "test_control_v2_replay.py",
    "test_control_v2_sender.py", "test_control_v2_workers.py",
):
    COPIES[f"host/app/tests/{name}"] = f"tests/{name}"


def source_path(source):
    return SOURCE.parent / source if source.startswith("ros_craic_car-main/") else SOURCE / source


def manifest_source(source):
    return source if source.startswith("ros_craic_car-main/") else f"pynq_ffn/{source}"


def copy_whitelist():
    for destination, source in sorted(COPIES.items()):
        data = source_path(source).read_bytes()
        if destination.startswith("metrics/evidence/") and destination.endswith(".json"):
            text = data.decode("utf-8")
            text = re.sub(r"C:[\\/]+Users[\\/]+BeFortuneEX[\\/]+Source[\\/]+Repos[\\/]+FPGA[\\/]+", "", text)
            text = re.sub(r"D:[\\/]+Programs[\\/]+TDM-GCC-64[\\/]+bin[\\/]+g\+\+\.exe", "g++.exe", text)
            data = text.encode("utf-8")
        if destination == "host/app/ps/control_v2/fixed_left.py":
            text = data.decode("utf-8")
            old = "LEFT_SOURCE = Path(__file__).resolve().parents[3] / 'ros_craic_car-main/src/base_control/resource/left.csv'"
            new = "LEFT_SOURCE = Path(__file__).resolve().parents[2] / 'assets/fixed_left/left.csv'"
            if old not in text:
                raise RuntimeError("fixed_left source path changed; review delivery mapping")
            data = text.replace(old, new, 1).encode("utf-8")
        target = ROOT / destination
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def write_manifest():
    files = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT).as_posix()
        if relative == "delivery_manifest.json" or relative.startswith((".git/", "host/deploy/dist/", "build_runs/")):
            continue
        if "__pycache__" in path.parts or ".pytest_cache" in path.parts or ".mimosa" in path.parts:
            continue
        data = path.read_bytes()
        source = COPIES.get(relative)
        files.append({
            "path": relative,
            "origin": "copied" if source else "generated",
            "source": manifest_source(source) if source else None,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        })
    manifest = {
        "schema": 2,
        "scheme": "conv_front_int8 + PS partB + real YOLO + control v2; fixed-left appendix",
        "repository": "BeFortune/kv260-smartcar",
        "base_commit": BASE_COMMIT,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "files": files,
    }
    (ROOT / "delivery_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(json.dumps({"files": len(files), "copied": sum(x["origin"] == "copied" for x in files),
                      "bytes": sum(x["bytes"] for x in files)}, ensure_ascii=False))


if __name__ == "__main__":
    copy_whitelist()
    write_manifest()
