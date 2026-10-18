"""Offline host-validation evidence harness for the fixed r03 sources.

Writes only under artifacts/ffn_opt_20261018/host_validation/.
Binds source snapshot -> compile commands -> DLL/exe -> regression report by
sha256, so the chain does not depend on file timestamps.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(r"C:\Users\BeFortuneEX\Source\Repos\FPGA")
HV = ROOT / "pynq_ffn" / "artifacts" / "ffn_opt_20261018" / "host_validation"
SRC = HV / "source"
BUILD = HV / "build"
LOGS = HV / "logs"
COMMANDS = HV / "commands"
RESULTS = HV / "results"

GXX = Path(r"D:/Programs/TDM-GCC-64/bin/g++.exe")
VENV_PY = Path(r"C:/Users/BeFortuneEX/.zcode/tmp/ffn_opt_20261017/venv/Scripts/python.exe")
BASE_DLL = ROOT / "pynq_ffn" / "artifacts" / "ffn_opt_20261017" / "baseline_host" / "ffn_host.dll"
BUNDLE = ROOT / "pynq_ffn" / "artifacts" / "bundle_20261014"
ZIP = ROOT / "kv260_v1" / "v1_payload.zip"

SOURCES = {
    "ffn_accel.cpp": ROOT / "pynq_ffn" / "hls" / "ffn_accel.cpp",
    "ffn_accel.hpp": ROOT / "pynq_ffn" / "hls" / "ffn_accel.hpp",
    "host_bridge.cpp": ROOT / "pynq_ffn" / "hls" / "host_bridge.cpp",
    "testbench.cpp": ROOT / "pynq_ffn" / "hls" / "testbench.cpp",
    "validate_cpp_parallel.py": ROOT / "pynq_ffn" / "tools" / "validate_cpp_parallel.py",
    "validate_payload.py": ROOT / "pynq_ffn" / "tools" / "validate_payload.py",
}

DLL = BUILD / "ffn_host_r03_20261018.dll"
TB_EXE = BUILD / "testbench_r03_20261018.exe"
REPORT = RESULTS / "host_payload_regression_r03_20261018_final2.json"


def now():
    return datetime.now().astimezone().isoformat()


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def run(name, command, cwd, log_name, env_overrides=None):
    started = now()
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    completed = subprocess.run(
        [str(c) for c in command], cwd=str(cwd), capture_output=True, env=env
    )
    ended = now()
    stdout = completed.stdout.decode("utf-8", "replace")
    stderr = completed.stderr.decode("utf-8", "replace")
    log = LOGS / log_name
    log.write_text(stdout + stderr, encoding="utf-8")
    record = {
        "stage": name,
        "command": [str(c) for c in command],
        "cwd": str(cwd),
        "started": started,
        "ended": ended,
        "exit_code": completed.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "combined_log": str(log),
        "combined_log_sha256": sha256(log),
        "environment_overrides": env_overrides or {},
    }
    (COMMANDS / f"{name}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if completed.returncode != 0:
        raise SystemExit(f"{name} failed with exit {completed.returncode}")
    return record


def main():
    for directory in (SRC, BUILD, LOGS, COMMANDS, RESULTS):
        directory.mkdir(parents=True, exist_ok=True)

    snapshot = {}
    for name, origin in SOURCES.items():
        target = SRC / name
        shutil.copy2(origin, target)
        snapshot[name] = {
            "original_absolute_path": str(origin),
            "snapshot_relative_path": f"source/{name}",
            "sha256": sha256(target),
            "bytes": target.stat().st_size,
        }
        if snapshot[name]["sha256"] != sha256(origin):
            raise SystemExit(f"snapshot hash mismatch for {name}")

    version_text = subprocess.run(
        [str(GXX), "--version"], capture_output=True
    ).stdout.decode("utf-8", "replace").strip()

    common = ["-std=c++11", "-O2", "-ffp-contract=off", "-Wno-unknown-pragmas"]
    dll_cmd = [GXX, *common, "-shared", "-static-libgcc", "-static-libstdc++",
               SRC / "ffn_accel.cpp", SRC / "host_bridge.cpp", "-o", DLL]
    tb_cmd = [GXX, *common, "-static-libgcc", "-static-libstdc++",
              SRC / "ffn_accel.cpp", SRC / "testbench.cpp", "-o", TB_EXE]

    compile_dll = run("compile_dll", dll_cmd, BUILD, "compile_dll.log")
    compile_tb = run("compile_tb", tb_cmd, BUILD, "compile_tb.log")
    testbench = run("testbench", [TB_EXE], BUILD, "testbench.log")
    regression = run(
        "regression",
        [VENV_PY, SRC / "validate_cpp_parallel.py",
         "--baseline-library", BASE_DLL,
         "--candidate-library", DLL,
         "--bundle", BUNDLE,
         "--zip", ZIP,
         "--output", REPORT],
        ROOT / "pynq_ffn",
        "regression.log",
        env_overrides={
            "PYTHONPATH": str(ROOT / "pynq_ffn") + os.pathsep
                          + os.environ.get("PYTHONPATH", "")
        },
    )

    report = json.loads(REPORT.read_text(encoding="utf-8"))
    synthetic = report["synthetic"]
    frames = report["payload"]["frames"]
    layer_bit_mismatches = sum(
        layer.get("bit_mismatches", 0)
        for frame in frames
        for layer in frame["layers"].values()
    )
    summary = {
        "report_status": report["status"],
        "synthetic_cases": len(synthetic),
        "synthetic_pass": sum(1 for case in synthetic if case.get("pass")),
        "synthetic_bit_mismatches": sum(case.get("bit_mismatches", 0) for case in synthetic),
        "synthetic_elements": sum(case.get("elements", 0) for case in synthetic),
        "real_frames": len(frames),
        "real_frames_pass": sum(1 for frame in frames if frame.get("pass")),
        "real_layer_bit_mismatches": layer_bit_mismatches,
        "real_layer_elements": sum(
            layer.get("elements", 0)
            for frame in frames
            for layer in frame["layers"].values()
        ),
        "real_integer_mismatches": sum(
            frame.get("speed_integer_mismatches", 0) for frame in frames
        ),
        "model_sha256": report.get("payload", {}).get("model_sha256"),
        "zip_sha256": report.get("payload", {}).get("zip_sha256"),
        "candidate_dll_sha256": report["libraries"]["candidate"]["sha256"],
        "baseline_dll_sha256": report["libraries"]["baseline"]["sha256"],
    }
    if not (
        summary["report_status"] == "pass"
        and summary["synthetic_pass"] == summary["synthetic_cases"] == 19
        and summary["real_frames_pass"] == summary["real_frames"] == 173
        and summary["synthetic_bit_mismatches"] == 0
        and summary["real_layer_bit_mismatches"] == 0
        and summary["real_integer_mismatches"] == 0
    ):
        raise SystemExit("regression summary is not fully passing")

    manifest = {
        "created": now(),
        "scope": "Offline host validation only; no remote/board/SSH/serial/camera/overlay. New directory only; no source or old-result modification.",
        "binding_rule": "Every link in the chain is anchored by sha256 of its bytes; timestamps are recorded for audit only and are not required for identity.",
        "platform": platform.platform(),
        "python_for_regression": {
            "path": str(VENV_PY),
            "sha256": sha256(VENV_PY),
            "version": subprocess.run([str(VENV_PY), "--version"],
                                      capture_output=True).stdout.decode().strip()
                       + subprocess.run([str(VENV_PY), "--version"],
                                        capture_output=True).stderr.decode().strip(),
        },
        "compiler": {
            "path": str(GXX),
            "sha256": sha256(GXX),
            "version_output": version_text,
        },
        "source_snapshot": snapshot,
        "chain": {
            "source_snapshot": "sha256 in source_snapshot",
            "compile_dll": "commands/compile_dll.json",
            "compile_tb": "commands/compile_tb.json",
            "candidate_dll": "build/ffn_host_r03_20261018.dll",
            "testbench_exe": "build/testbench_r03_20261018.exe",
            "testbench_run": "commands/testbench.json",
            "regression_run": "commands/regression.json",
            "regression_report": "results/host_payload_regression_r03_20261018.json",
        },
        "compile": {
            "dll": {
                "source_files": ["source/ffn_accel.cpp", "source/host_bridge.cpp"],
                "command_record": "commands/compile_dll.json",
                "command": compile_dll["command"],
                "exit_code": compile_dll["exit_code"],
                "output_path": str(DLL),
                "output_sha256": sha256(DLL),
                "output_bytes": DLL.stat().st_size,
            },
            "testbench": {
                "source_files": ["source/ffn_accel.cpp", "source/testbench.cpp"],
                "command_record": "commands/compile_tb.json",
                "command": compile_tb["command"],
                "exit_code": compile_tb["exit_code"],
                "output_path": str(TB_EXE),
                "output_sha256": sha256(TB_EXE),
                "output_bytes": TB_EXE.stat().st_size,
            },
        },
        "testbench_run": {
            "command_record": "commands/testbench.json",
            "exit_code": testbench["exit_code"],
            "passed": "All FFN reference tests passed." in testbench["stdout"],
            "test_lines": [
                line for line in testbench["stdout"].splitlines()
                if line.startswith("PASS ")
            ],
        },
        "regression": {
            "command_record": "commands/regression.json",
            "exit_code": regression["exit_code"],
            "validator_script": "source/validate_cpp_parallel.py",
            "validator_script_sha256": snapshot["validate_cpp_parallel.py"]["sha256"],
            "baseline_library": str(BASE_DLL),
            "baseline_library_sha256": sha256(BASE_DLL),
            "candidate_library": str(DLL),
            "candidate_library_sha256": sha256(DLL),
            "report": str(REPORT),
            "report_sha256": sha256(REPORT),
            "summary": summary,
        },
        "unmodified_prior_artifacts": {
            "candidate_r02_host_untouched": str(
                ROOT / "pynq_ffn" / "artifacts" / "ffn_opt_20261017" / "candidate_r02_host"
            ),
            "baseline_host_used_in_place": str(BASE_DLL.parent),
        },
    }
    (HV / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({
        "dll_sha256": manifest["compile"]["dll"]["output_sha256"],
        "tb_exit": testbench["exit_code"],
        "regression_exit": regression["exit_code"],
        "summary": summary,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
