"""Local-only host validation; every output and stage log refuses overwrites."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
CANDIDATE = ROOT / "hls/candidates/interleaved_r04"
GXX = Path("D:/Programs/TDM-GCC-64/bin/g++.exe")
FLAGS = ["-std=c++11", "-O2", "-Wall", "-Wextra", "-Wpedantic",
         "-Wno-unknown-pragmas", "-Wno-unused-label", "-ffp-contract=off"]


def run(name, command, expected=0):
    start = datetime.now(timezone.utc).isoformat()
    with (HERE / (name + ".log")).open("x", encoding="utf-8") as output:
        result = subprocess.run([str(v) for v in command], cwd=ROOT,
                                stdout=output, stderr=subprocess.STDOUT, check=False)
    meta = {"command": [str(v) for v in command], "cwd": str(ROOT),
            "started": start, "ended": datetime.now(timezone.utc).isoformat(),
            "exit_code": result.returncode, "expected_exit_code": expected,
            "python": sys.version, "platform": platform.platform()}
    with (HERE / (name + ".command.json")).open("x", encoding="utf-8") as output:
        json.dump(meta, output, indent=2)
    print(name, "exit=", result.returncode, flush=True)
    if result.returncode != expected:
        raise SystemExit("STOP first failed stage: " + name)


def main():
    host = HERE / "host"
    host.mkdir(exist_ok=False)
    run("compiler", [GXX, "--version"])
    run("python_syntax", [sys.executable, "-c",
        "from pathlib import Path; compile(Path(r'" + str(CANDIDATE / "make_config.py") +
        "').read_text(), 'make_config.py', 'exec')"])
    for scope, tb in (("full", "testbench.cpp"), ("small", "testbench_small.cpp")):
        exe = host / (scope + ".exe")
        run("compile_" + scope, [GXX] + FLAGS + [CANDIDATE / "ffn_accel.cpp", CANDIDATE / tb, "-o", exe])
        run("test_" + scope, [exe])
        run("config_" + scope, [sys.executable, CANDIDATE / "make_config.py", "--testbench", scope])
    dll = host / "ffn_host.dll"
    run("compile_dll", [GXX] + FLAGS + ["-shared", "-static-libgcc", "-static-libstdc++",
        CANDIDATE / "ffn_accel.cpp", CANDIDATE / "host_bridge.cpp", "-o", dll])
    compare = ROOT / "tools/validate_cpp_parallel.py"
    history = ROOT / "artifacts/ffn_opt_20261017"
    for label, baseline in (("serial", history / "baseline_host/ffn_host.dll"),
                            ("r03", history / "candidate_r03_host/ffn_host.dll")):
        run("bitwise_" + label, [sys.executable, compare, "--baseline-library", baseline,
            "--candidate-library", dll, "--output", HERE / ("bitwise_" + label + ".json")])
    run("payload_173", [sys.executable, compare, "--baseline-library",
        history / "candidate_r03_host/ffn_host.dll", "--candidate-library", dll,
        "--bundle", ROOT / "artifacts/bundle_20261014", "--zip", ROOT.parent / "kv260_v1/v1_payload.zip",
        "--output", HERE / "payload_173.json"])
    print("All local stages completed; HLS/RTL/PL were not executed.", flush=True)


if __name__ == "__main__":
    main()
