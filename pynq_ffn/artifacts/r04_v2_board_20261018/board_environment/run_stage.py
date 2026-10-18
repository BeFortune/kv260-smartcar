"""Run one pinned board stage without a shell, timeout or automatic next stage."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import signal
import subprocess
import sys


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pins", required=True, type=Path)
    parser.add_argument("--pins-sha256", required=True)
    parser.add_argument("--stage", required=True, choices=("static", "program", "validate"))
    parser.add_argument("--records", required=True, type=Path)
    args = parser.parse_args()
    if sha(args.pins) != args.pins_sha256:
        raise ValueError("Pinned launch manifest changed")
    pins = json.loads(args.pins.read_text(encoding="utf-8"))
    stage = args.stage
    script = Path(pins["program_script"] if stage == "program" else pins["validation_script"]).resolve(strict=True)
    if script.name not in ("program_overlay.py", "board_validate.py"):
        raise ValueError("Unreviewed entrypoint")
    for filename, expected in pins["launch_files"].items():
        if sha(Path(filename)) != expected:
            raise ValueError("Launch source/evidence changed: " + filename)
    if str(script) not in pins["launch_files"]:
        raise ValueError("Entrypoint not covered by pinned launch files")
    args.records.mkdir(parents=True, exist_ok=True)
    started, finished, logfile = [args.records / (stage + suffix) for suffix in (".started.json", ".finished.json", ".log")]
    if any(path.exists() for path in (started, finished, logfile)):
        raise FileExistsError("Stage attempted already; preserve it and use a new record directory")
    arguments = [sys.executable, "-B", str(script)]
    for name in ("root", "deployment_manifest", "manifest_sha256", "bit", "bit_sha256", "hwh_sha256",
                 "system_acceptance", "acceptance_sha256"):
        arguments.extend(["--" + name.replace("_", "-"), str(pins[name])])
    arguments.extend(["--output-dir", pins["output_dirs"][stage]])
    if stage == "program":
        arguments.extend(["--old-inspection", pins["old_inspection"],
                          "--old-inspection-sha256", pins["old_inspection_sha256"], "--program"])
    elif stage == "validate":
        previous = json.loads((Path(pins["output_dirs"]["program"]) / "report.json").read_text(encoding="utf-8"))
        if previous["status"] != "已验证" or not previous["hardware_programmed"] or previous["kernel_starts"] != 0:
            raise ValueError("Programming did not finish verified; no compute")
        arguments.extend(["--recording", pins["recording"], "--timeout-s", "60", "--allow-pl", "--reuse-programmed-overlay"])
    if any("\0" in arg for arg in arguments):
        raise ValueError("Invalid argument")
    record = {"status": "未执行", "stage": stage, "command": arguments,
              "started_utc": datetime.now(timezone.utc).isoformat(), "exit_code": None,
              "pins_sha256": args.pins_sha256, "cancellation": "No timeout/kill/terminate or automatic recovery"}
    with started.open("x", encoding="utf-8") as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2)
    if stage in ("program", "validate"):
        for sig in (signal.SIGINT, signal.SIGTERM, *([signal.SIGHUP] if hasattr(signal, "SIGHUP") else [])):
            signal.signal(sig, signal.SIG_IGN)
    with logfile.open("xb") as log:
        process = subprocess.Popen(arguments, shell=False, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT)
        while True:
            try:
                code = process.wait()
                break
            except BaseException:
                continue
    record.update(status="已验证" if code == 0 else "失败", exit_code=code,
                  finished_utc=datetime.now(timezone.utc).isoformat())
    with finished.open("x", encoding="utf-8") as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2)
    print(json.dumps(record, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
