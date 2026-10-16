"""Read-only local board inspection; never imports PYNQ or opens hardware."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import sys


def read_optional(path: str):
    target = Path(path)
    try:
        return target.read_text(encoding="utf-8", errors="replace").strip("\x00\n ")
    except OSError:
        return None


def inspect():
    memory = read_optional("/proc/meminfo")
    mem_fields = {}
    if memory:
        for line in memory.splitlines():
            key, _, value = line.partition(":")
            if key in {"MemTotal", "MemAvailable", "CmaTotal", "CmaFree"}:
                mem_fields[key] = value.strip()
    packages = {}
    for name in ("pynq", "numpy", "onnxruntime", "onnx", "opencv-python-headless", "opencv-python"):
        try:
            distribution = importlib.metadata.distribution(name)
            packages[name] = {"version": distribution.version,
                              "location": str(distribution.locate_file(""))}
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    tools = {name: shutil.which(name) for name in ("xclbinutil", "vivado", "vitis", "vitis_hls", "v++", "vitis-run")}
    cma = mem_fields.get("CmaTotal", "0 kB")
    cma_nonzero = bool(cma.split() and cma.split()[0].isdigit() and int(cma.split()[0]) > 0)
    warnings = []
    if platform.machine().lower() not in ("aarch64", "arm64"):
        warnings.append("This is not the expected KV260 AArch64 runtime; host-only inspection.")
    if not cma_nonzero:
        warnings.append("A nonzero CmaTotal was not observed. Do not infer usable PL allocation from free RAM.")
    if tools["xclbinutil"] and "pynq-venv" not in tools["xclbinutil"]:
        warnings.append("xclbinutil is outside the historical PYNQ venv; check its provenance before overlay use.")
    return {"timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "mode": "read_only_files_and_package_metadata", "python": sys.version,
            "executable": sys.executable, "machine": platform.machine(),
            "system": platform.system(), "kernel": platform.release(),
            "board_model": read_optional("/proc/device-tree/model"),
            "os_release": read_optional("/etc/os-release"),
            "cmdline": read_optional("/proc/cmdline"), "memory": mem_fields,
            "cma_nonzero_observed": cma_nonzero, "packages": packages,
            "tools_on_path": tools, "effective_uid": os.geteuid() if hasattr(os, "geteuid") else None,
            "warnings": warnings, "overlay_loaded_by_this_tool": False,
            "pynq_imported": False, "pl_execution_tested": False,
            "hardware_ready_certified": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Optional new JSON file; refuses overwrite")
    args = parser.parse_args()
    report = inspect()
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
