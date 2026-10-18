"""Collect non-privileged KV260 preflight facts without importing PYNQ."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    bundle = Path("/home/ubuntu/ffn_migrate/bundle")
    recording = Path("/home/ubuntu/ffn_migrate/data/1778852165")
    packages = {}
    for name in ("pynq", "numpy", "onnxruntime", "opencv-python", "opencv-python-headless"):
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            packages[name] = None
    process = subprocess.run(["ps", "-eo", "pid,ppid,stat,comm,args"],
                             text=True, capture_output=True, check=True)
    relevant = []
    for line in process.stdout.splitlines()[1:]:
        fields = line.split(None, 4)
        if len(fields) == 5:
            arguments = fields[4].lower()
            if fields[3].lower().startswith(("python", "ros")) and any(
                    word in arguments for word in ("ffn", "retest", "validate", "roslaunch", "rosrun")):
                relevant.append({"pid": int(fields[0]), "ppid": int(fields[1]),
                                 "stat": fields[2], "comm": fields[3]})
    readable_sysfs = {}
    for path in (Path("/sys/class/fpga_manager/fpga0/state"),
                 Path("/sys/class/fpga_manager/fpga0/name"),
                 Path("/sys/class/thermal/thermal_zone0/temp")):
        try:
            readable_sysfs[str(path)] = path.read_text().strip()
        except OSError as error:
            readable_sysfs[str(path)] = type(error).__name__
    data = {"status": "已验证", "date_utc": datetime.now(timezone.utc).isoformat(),
            "scope": "Non-privileged preflight only; no PYNQ import/MMIO/clock/overlay/kernel/camera/serial",
            "hostname": platform.node(), "platform": platform.platform(),
            "python": sys.version, "executable": sys.executable,
            "packages": packages,
            "bundle_manifest_exists": (bundle / "manifest.json").is_file(),
            "bundle_manifest_sha256": sha(bundle / "manifest.json") if (bundle / "manifest.json").is_file() else None,
            "recording_exists": recording.is_dir(),
            "disk_free_bytes": shutil.disk_usage("/home/ubuntu").free,
            "memory": [line for line in Path("/proc/meminfo").read_text().splitlines()
                       if line.startswith(("MemAvailable:", "CmaTotal:", "CmaFree:"))],
            "relevant_processes": relevant, "sysfs": readable_sysfs,
            "self_sha256": sha(Path(__file__))}
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"status": data["status"], "output": str(args.output),
                      "hostname": data["hostname"], "relevant_processes": relevant}, ensure_ascii=False))


if __name__ == "__main__":
    main()
