"""Run AMD batch entrypoints from a local cwd and preserve command/result evidence."""
import argparse
import datetime
import json
from pathlib import Path
import subprocess
import sys

p = argparse.ArgumentParser()
p.add_argument("--cwd", required=True)
p.add_argument("--log", required=True)
p.add_argument("--timeout", type=int, default=1200)
p.add_argument("tool")
p.add_argument("args", nargs=argparse.REMAINDER)
a = p.parse_args()
log = Path(a.log)
log.parent.mkdir(parents=True, exist_ok=True)
command = '"' + str(Path(a.tool)) + '" ' + subprocess.list2cmdline(a.args)
invocation = 'cmd.exe /d /s /c "' + command + '"'
start = datetime.datetime.now().isoformat()
result = {"cwd": a.cwd, "argv": invocation, "command": command, "started": start}
with log.open("wb") as stream:
    try:
        proc = subprocess.run(invocation, cwd=a.cwd, stdout=stream,
                              stderr=subprocess.STDOUT, timeout=a.timeout)
        result["exit_code"] = proc.returncode
    except subprocess.TimeoutExpired:
        result["exit_code"] = None
        result["timeout_seconds"] = a.timeout
result["finished"] = datetime.datetime.now().isoformat()
log.with_suffix(".command.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result, indent=2))
sys.exit(result["exit_code"] if result["exit_code"] is not None else 124)
