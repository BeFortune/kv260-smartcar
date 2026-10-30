"""Build a versioned KV260 car bundle from the official delivery tree.

No hardware is accessed. Existing bundles are never overwritten.
"""
import ast
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "host" / "app"
HWH_SHA256 = "c2b5c8138087c8e398d509df40e1c854311ebcca249f7a0e232c107d8db6d067"
SOURCES = [
    "ps/__init__.py", "ps/packet.py", "ps/car_loop.py", "ps/pl_infer.py", "ps/capture.py",
    "ps/preprocess.py", "ps/cpu_infer.py", "ps/frame_capture.py", "ps/control_arbiter.py",
    "ps/roadsign.py", "ps/roadsign_supervisor.py",
    "ps/serial_link.py", "ps/serial_port.py", "ps/serial_port.json", "ps/camera_ports.py",
    "ps/camera_ports.json", "ps/devices.py", "ps/joystick.py", "ps/joystick.json",
    "tools/car/car_main.py", "tools/car/carwatch.py", "tools/car/cpu_check.py",
    "tools/car/frame_capture_replay.py", "tools/car/FRAME_CAPTURE.md",
    "tools/car/pl_load.py", "tools/car/xpad_bind.sh", "tools/car/install.sh",
    "tools/car/carctl.sh", "tools/car/README.md", "tools/car/board_setup.sh",
    "tools/car/systemd/car-main.service", "tools/car/systemd/car-pl-load.service",
    "tools/car/systemd/car-xpad-bind.service",
]
EXECUTABLE = {"tools/car/xpad_bind.sh", "tools/car/install.sh", "tools/car/carctl.sh"}


def load_pinned():
    tree = ast.parse((APP / "ps" / "pl_infer.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "PINNED_SHA256" for t in node.targets):
            return ast.literal_eval(node.value)
    raise RuntimeError("PINNED_SHA256 not found")


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def main():
    pinned = load_pinned()
    models = {
        "tools/car/models/conv_front_int8_20261021.bit": (ROOT / "fpga/build/conv_front_int8_20261021.bit", pinned["bit"]),
        "tools/car/models/conv_front_int8_20261021.hwh": (ROOT / "fpga/build/conv_front_int8_20261021.hwh", HWH_SHA256),
        "tools/car/models/ref_int8.py": (ROOT / "host/model/ref_int8.py", pinned["ref"]),
        "tools/car/models/params.json": (ROOT / "host/model/params.json", pinned["params"]),
        "tools/car/models/s16_j1c2_cut2int8_ps.onnx": (ROOT / "host/model/s16_j1c2_cut2int8_ps.onnx", pinned["partb"]),
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    version = f"kv260car_{stamp}"
    files = {}
    for rel in SOURCES:
        data = (APP / rel).read_bytes()
        if rel.endswith((".sh", ".service")):
            data = data.replace(b"\r\n", b"\n")
        files[rel] = data
    for rel, (source, expected) in models.items():
        data = source.read_bytes()
        got = sha256_bytes(data)
        if got != expected:
            raise SystemExit(f"{source.name}: sha256 mismatch; not packing")
        files[rel] = data
    manifest = {"version": version, "created_utc": datetime.now(timezone.utc).isoformat(),
                "files": {rel: {"bytes": len(data), "sha256": sha256_bytes(data)}
                          for rel, data in sorted(files.items())}}
    files["MANIFEST.json"] = json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8")

    dist = ROOT / "host" / "deploy" / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    out = dist / f"{version}.tar.gz"
    with tarfile.open(out, "x:gz") as tar:
        for rel, data in sorted(files.items()):
            info = tarfile.TarInfo(f"{version}/{rel}")
            info.size = len(data)
            info.mode = 0o755 if rel in EXECUTABLE else 0o644
            info.mtime = int(datetime.now(timezone.utc).timestamp())
            tar.addfile(info, io.BytesIO(data))
    digest = sha256_bytes(out.read_bytes())
    (dist / f"{version}.tar.gz.sha256").write_bytes(f"{digest}  {out.name}\n".encode("ascii"))
    print(json.dumps({"bundle": str(out), "sha256": digest, "files": len(files)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
