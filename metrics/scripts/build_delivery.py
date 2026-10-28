"""Copy the reviewed delivery whitelist and record per-file hashes.

Run from any directory with Python 3. The development tree is only read.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

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
