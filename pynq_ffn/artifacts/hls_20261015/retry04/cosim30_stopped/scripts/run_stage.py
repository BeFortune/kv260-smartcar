"""Execute one build stage without overwriting existing evidence or changing PATH."""

import argparse
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def now():
    return datetime.datetime.now().astimezone().isoformat()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--env", action="append", default=[])
    parser.add_argument("tool")
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    options = parser.parse_args()
    cwd = Path(options.cwd).resolve(strict=True)
    tool = Path(options.tool).resolve(strict=True)
    if str(cwd).startswith("\\\\"):
        parser.error("UNC working directories are forbidden for these builds.")
    log = Path(options.log)
    metadata = log.with_suffix(".command.json")
    if log.exists() or metadata.exists():
        parser.error("Refusing to overwrite stage evidence: " + str(log))
    log.parent.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    overrides = {}
    for entry in options.env:
        key, separator, value = entry.partition("=")
        if not separator or not key or key.upper() == "PATH":
            parser.error("Expected KEY=VALUE environment override, not PATH.")
        environment[key] = value
        overrides[key] = value
    if tool.suffix.lower() in (".bat", ".cmd"):
        command = '"' + str(tool) + '"'
        if options.arguments:
            command += " " + subprocess.list2cmdline(options.arguments)
        invocation = '"' + os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe") + '" /d /s /c "' + command + '"'
    else:
        invocation = [str(tool), *options.arguments]
        command = subprocess.list2cmdline(invocation)
    record = {
        "cwd": str(cwd),
        "tool": str(tool),
        "arguments": options.arguments,
        "command": command,
        "invocation": invocation,
        "environment_overrides": overrides,
        "path_modified_by_runner": False,
        "started": now(),
        "status": "starting",
        "exit_code": None,
    }
    with metadata.open("x", encoding="utf-8") as stream:
        json.dump(record, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    started = time.monotonic()
    with log.open("xb") as stream:
        try:
            process = subprocess.Popen(invocation, cwd=cwd, env=environment,
                                       stdout=stream, stderr=subprocess.STDOUT)
            record.update(status="running", pid=process.pid)
            metadata.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            record["exit_code"] = process.wait()
            record["status"] = "completed"
        except OSError as error:
            record.update(status="launch_failed", launch_error=str(error))
    record.update(finished=now(), elapsed_seconds=round(time.monotonic() - started, 3))
    metadata.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2, ensure_ascii=False))
    return record["exit_code"] if record["exit_code"] is not None else 125


if __name__ == "__main__":
    sys.exit(main())
