"""Copy the reviewed delivery whitelist and record per-file hashes.

Run from any directory with Python 3. The development tree is only read.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT.parent / "pynq_ffn"
BASE_COMMIT = "4a00d4acdf382991b947c4f1c4b8bd89e9674414"

COPIES = {
    "LICENSE": "LICENSE",
    "fpga/src/conv_front_int8.cpp": "hls/conv_front_int8/conv_front_int8.cpp",
    "fpga/src/conv_front_int8.hpp": "hls/conv_front_int8/conv_front_int8.hpp",
    "fpga/src/testbench.cpp": "hls/conv_front_int8/testbench.cpp",
    "fpga/src/weights_gen.hpp": "hls/conv_front_int8/weights_gen.hpp",
    "fpga/src/ref_int8.py": "hls/conv_front_int8/ref_int8.py",
    "fpga/src/params.json": "hls/conv_front_int8/params.json",
    "fpga/src/build/hls.cfg": "hls/conv_front_int8/build/hls.cfg",
    "fpga/src/vectors/cases.txt": "hls/conv_front_int8/vectors/cases.txt",
    "fpga/build/conv_front_int8_20261021.bit": "artifacts/route_b_quant_20261020/system_cfs1/conv_front_int8_20261021.bit",
    "fpga/build/conv_front_int8_20261021.hwh": "artifacts/route_b_quant_20261020/system_cfs1/conv_front_int8_20261021.hwh",
    "fpga/report/hls_fixed_acceptance01.json": "artifacts/route_b_quant_20261020/hls_fixed_acceptance01.json",
    "fpga/report/ooc_timing_routed01.rpt": "artifacts/route_b_quant_20261020/ooc_timing_routed01.rpt",
    "fpga/report/pl_board_20261021.md": "artifacts/route_b_quant_20261020/pl_board_20261021.md",
    "host/model/s16_j1c2_cut2int8_ps.onnx": "artifacts/route_b_quant_20261020/cut2_01_board/s16_j1c2_cut2int8_ps.onnx",
    "host/model/params.json": "hls/conv_front_int8/params.json",
    "host/model/ref_int8.py": "hls/conv_front_int8/ref_int8.py",
    "host/deploy/requirements-prepare.txt": "requirements-prepare.txt",
    "host/deploy/requirements-runtime.txt": "requirements-runtime.txt",
    "host/deploy/requirements-test.txt": "requirements-test.txt",
    "metrics/evidence/system_artifact_verification.json": "artifacts/route_b_quant_20261020/system_cfs1/artifact_verification.json",
    "metrics/evidence/cpu_timing_run01.json": "artifacts/route_b_quant_20261020/cut2_timing_run01_summary.json",
    "metrics/evidence/ps_overhead_run01.json": "artifacts/route_b_quant_20261020/ps_overhead_run01_report.json",
    "metrics/evidence/car_loop_board_run01.json": "artifacts/pl_infer_20261022/board_run01_report.json",
    "metrics/evidence/host_replay01.json": "artifacts/pl_infer_20261022/replay01_report.json",
    "metrics/evidence/field_turning_diagnosis.md": "artifacts/field_turning_diagnosis_20261027/run01/REPORT.md",
    "metrics/logs/pl_board_run01_report.json": "artifacts/route_b_quant_20261020/pl_board_run01_extract/run01/report.json",
}

COPIES.update({
    "host/app/ps/control_arbiter.py": "ps/control_arbiter.py",
    "host/app/ps/roadsign.py": "ps/roadsign.py",
    "host/app/ps/roadsign_supervisor.py": "ps/roadsign_supervisor.py",
    "host/app/tools/roadsign_replay.py": "tools/roadsign_replay.py",
    "host/app/tools/field_replay.py": "tools/field_replay.py",
    "host/app/tools/control_v2_replay.py": "tools/control_v2_replay.py",
    "host/app/artifacts/roadsign_migration_20261027/run01/fixtures/constant_pedestrian.onnx": "artifacts/roadsign_migration_20261027/run01/fixtures/constant_pedestrian.onnx",
    "host/app/candidates/original_model_pspl/attention01/attention_rewrite.py": "artifacts/original_model_pspl_15hz_20261029/run01/attention01/attention_rewrite.py",
    "host/app/candidates/original_model_pspl/attention01/runtime_cpu.py": "artifacts/original_model_pspl_15hz_20261029/run01/attention01/runtime_cpu.py",
    "host/app/candidates/original_model_pspl/attention01/replay_data.py": "artifacts/original_model_pspl_15hz_20261029/run01/attention01/replay_data.py",
    "host/app/candidates/original_model_pspl/attention01/validate_attention.py": "artifacts/original_model_pspl_15hz_20261029/run01/attention01/validate_attention.py",
    "host/app/candidates/original_model_pspl/backend01/prepared_backend.py": "artifacts/original_model_pspl_15hz_20261029/run01/backend01/prepared_backend.py",
    "host/app/candidates/original_model_pspl/backend01/runtime_adapter.py": "artifacts/original_model_pspl_15hz_20261029/run01/backend01/runtime_adapter.py",
    "fpga/src/candidates/original_model_ffn128/ffn_accel.cpp": "artifacts/original_model_pspl_15hz_20261029/run01/kernel128_01/ffn_accel.cpp",
    "fpga/src/candidates/original_model_ffn128/ffn_accel.hpp": "artifacts/original_model_pspl_15hz_20261029/run01/kernel128_01/ffn_accel.hpp",
    "fpga/src/candidates/original_model_ffn128/host_bridge.cpp": "artifacts/original_model_pspl_15hz_20261029/run01/kernel128_01/host_bridge.cpp",
    "fpga/src/candidates/original_model_ffn128/build_hls.cfg": "artifacts/original_model_pspl_15hz_20261029/run01/kernel128_01/build_hls.cfg",
    "metrics/evidence/original_model/final_status.json": "artifacts/original_model_pspl_15hz_20261029/run01/final_status.json",
    "metrics/evidence/original_model/runtime_recheck_summary.json": "artifacts/original_model_pspl_15hz_20261029/run01/integration01/final_runtime_recheck01/summary.json",
    "metrics/evidence/original_model/benchmark_report.json": "artifacts/original_model_pspl_15hz_20261029/run01/attention01/benchmark01/report.json",
    "metrics/evidence/original_model/host12_summary.json": "artifacts/original_model_pspl_15hz_20261029/run01/integration01/host12_02/summary.json",
    "metrics/evidence/original_model/kernel128_test_results.json": "artifacts/original_model_pspl_15hz_20261029/run01/kernel128_01/test_results.json",
    "metrics/evidence/original_model/historical_budget.json": "artifacts/original_model_pspl_15hz_20261029/run01/integration01/historical_budget.json",
    "metrics/evidence/roadsign/final_status.json": "artifacts/roadsign_real_validation_20261028/run01/delivery01/final_status.json",
    "metrics/evidence/roadsign/full_replay_summary.json": "artifacts/roadsign_real_validation_20261028/run01/results/full_replay02/summary.json",
    "metrics/evidence/roadsign/final_verify_summary.json": "artifacts/roadsign_real_validation_20261028/run01/final_verify01/summary.json",
    "metrics/evidence/roadsign/preboard_gates.json": "artifacts/roadsign_real_validation_20261028/run01/delivery01/preboard_gates.json",
    "metrics/evidence/control_v2/final_status.json": "artifacts/roadsign_control_v2_20261030/run01/final_status.json",
    "metrics/evidence/control_v2/saved_replay_summary.json": "artifacts/roadsign_control_v2_20261030/run01/integration01/saved_replay02/summary.json",
    "metrics/evidence/control_v2/test_counts.json": "artifacts/roadsign_control_v2_20261030/run01/implementation01/revision02/final_test_counts.json",
    "metrics/evidence/control_v2/interface.json": "artifacts/roadsign_control_v2_20261030/run01/implementation01/interface.json",
    "metrics/evidence/control_v2/fixture_summary.json": "artifacts/roadsign_control_v2_20261030/run01/implementation01/revision02/fixture02/summary.json",
})

for module in ("__init__.py", "audit.py", "controller.py", "policy.py", "sender.py", "snapshots.py", "workers.py"):
    COPIES[f"host/app/ps/control_v2/{module}"] = f"ps/control_v2/{module}"

for test in (
    "test_control_arbiter.py", "test_car_loop.py", "test_car_main.py", "test_car_main_auto.py",
    "test_roadsign.py", "test_roadsign_config.py", "test_roadsign_replay.py", "test_roadsign_supervisor.py",
    "test_control_v2_controller.py", "test_control_v2_policy.py", "test_control_v2_replay.py",
    "test_control_v2_sender.py", "test_control_v2_workers.py",
):
    COPIES[f"host/app/tests/{test}"] = f"tests/{test}"

BUNDLE = "artifacts/original_model_pspl_15hz_20261029/run01/attention01/bundle01"
for name in (
    "ABI.json", "camera_0.onnx", "camera_1.onnx", "full.onnx", "golden.onnx", "rewrites.json",
    "single_camera_full.onnx", "single_camera_rewrites.json", "stage_0.onnx", "stage_1.onnx",
    "stage_2.onnx", "weights.npz",
):
    COPIES[f"host/model/candidates/original_model_pspl/bundle01/{name}"] = f"{BUNDLE}/{name}"

for index in range(4):
    for kind in ("input", "output"):
        name = f"{kind}_{index:03d}.bin"
        COPIES[f"fpga/src/vectors/{name}"] = f"hls/conv_front_int8/vectors/{name}"

APP_FILES = (
    "ps/__init__.py", "ps/packet.py", "ps/car_loop.py", "ps/pl_infer.py", "ps/capture.py",
    "ps/preprocess.py", "ps/cpu_infer.py", "ps/frame_capture.py", "ps/serial_link.py",
    "ps/serial_port.py", "ps/serial_port.json", "ps/camera_ports.py", "ps/camera_ports.json",
    "ps/devices.py", "ps/joystick.py", "ps/joystick.json", "tools/car/car_main.py",
    "tools/car/carwatch.py", "tools/car/cpu_check.py", "tools/car/frame_capture_replay.py",
    "tools/car/FRAME_CAPTURE.md", "tools/car/pl_load.py", "tools/car/xpad_bind.sh",
    "tools/car/install.sh", "tools/car/carctl.sh", "tools/car/README.md",
    "tools/car/board_setup.sh", "tools/car/systemd/car-main.service",
    "tools/car/systemd/car-pl-load.service", "tools/car/systemd/car-xpad-bind.service",
)
for relative in APP_FILES:
    COPIES[f"host/app/{relative}"] = relative


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def copy_whitelist():
    for destination, source in sorted(COPIES.items()):
        data = (SOURCE / source).read_bytes()
        if destination.startswith("metrics/evidence/") and destination.endswith(".json"):
            text = data.decode("utf-8")
            text = re.sub(r"C:[\\/]+Users[\\/]+BeFortuneEX[\\/]+Source[\\/]+Repos[\\/]+FPGA[\\/]+", "", text)
            text = re.sub(r"D:[\\/]+Programs[\\/]+TDM-GCC-64[\\/]+bin[\\/]+g\+\+\.exe", "g++.exe", text)
            data = text.encode("utf-8")
        if destination == "host/app/tests/test_car_main.py":
            text = data.decode("utf-8")
            original = '''MODELS = {
    "bit": ROOT / "artifacts/route_b_quant_20261020/system_cfs1/conv_front_int8_20261021.bit",
    "ref": ROOT / "hls/conv_front_int8/ref_int8.py",
    "params": ROOT / "hls/conv_front_int8/params.json",
    "partb": ROOT / "artifacts/route_b_quant_20261020/cut2_01_board/s16_j1c2_cut2int8_ps.onnx",
}'''
            relocated = '''MODELS = {
    "bit": ROOT.parent.parent / "fpga/build/conv_front_int8_20261021.bit",
    "ref": ROOT.parent.parent / "host/model/ref_int8.py",
    "params": ROOT.parent.parent / "host/model/params.json",
    "partb": ROOT.parent.parent / "host/model/s16_j1c2_cut2int8_ps.onnx",
}'''
            if original not in text:
                raise RuntimeError("test_car_main.py model path block changed; review delivery mapping")
            data = text.replace(original, relocated, 1).encode("utf-8")
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
        if "__pycache__" in path.parts or ".pytest_cache" in path.parts:
            continue
        data = path.read_bytes()
        source = COPIES.get(relative)
        item = {
            "path": relative,
            "origin": "copied" if source else "generated",
            "source": f"pynq_ffn/{source}" if source else None,
            "bytes": len(data),
            "sha256": sha256(data),
        }
        files.append(item)
    manifest = {
        "schema": 1,
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
