"""Read-only confirmation of an already programmed image after a known cache warning.
Never downloads, resets, changes clocks, allocates buffers or starts a kernel.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "board_validation"))
import board_validate as validation
import safety_gates as gates

BASE = Path("/home/ubuntu/r04_v2_board_20261018")


def inside(value):
    path = Path(value).resolve()
    if BASE not in path.parents or ".." in Path(value).parts:
        raise ValueError("Path outside this isolated run")
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("pins", "pins-sha256", "program-record", "program-record-sha256", "output"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    pins_path, program_path, output = inside(args.pins), inside(args.program_record), inside(args.output)
    if gates.sha(pins_path) != args.pins_sha256 or gates.sha(program_path) != args.program_record_sha256:
        raise ValueError("External launch/program pin changed")
    pins = json.loads(pins_path.read_text(encoding="utf-8"))
    for name, expected in pins["launch_files"].items():
        if gates.sha(inside(name)) != expected:
            raise ValueError("Launch file changed")
    previous = json.loads(program_path.read_text(encoding="utf-8"))
    if (previous.get("status") != "失败" or not previous.get("hardware_programmed")
            or previous.get("kernel_starts") != 0
            or previous.get("first_error") != "RuntimeError: Programming emitted warnings; do not start a kernel"):
        raise ValueError("Unexpected programming failure; do not continue")
    journal = inside(program_path.with_name("events.jsonl"))
    returned = [row["data"] for row in map(json.loads, journal.read_text(encoding="utf-8").splitlines())
                if row["kind"] == "programming_returned"]
    known = "unclosed file <_io.BufferedWriter name='/usr/local/share/pynq-venv/lib/python3.10/site-packages/pynq/pl_server/_current_metadata.pkl'>"
    if len(returned) != 1 or returned[0]["warnings"] != [known]:
        raise ValueError("Unreviewed programming warning; do not continue")
    launch = argparse.Namespace(**pins)
    launch.ip_name = "ffn_accel_0"
    launch.expected_clock_mhz, launch.clock_tolerance_percent = 100., .5
    old_inspection = inside(pins["old_inspection"])
    if gates.sha(old_inspection) != pins["old_inspection_sha256"]:
        raise ValueError("Old inspection changed")
    evidence = gates.Evidence(output, launch)
    try:
        validation.static(launch)
        validation.require_target()
        if os.environ.get("XILINX_XRT") != "/usr" or "XCL_EMULATION_MODE" in os.environ:
            raise RuntimeError("Use reviewed real-hardware environment")
        from pynq import Clocks, Overlay, PL
        from pynq._3rdparty import xrt
        if not xrt.XRT_SUPPORTED or xrt.XRT_EMULATION:
            raise RuntimeError("Not real embedded XRT")
        overlay = gates.reuse_overlay(Overlay, PL, launch, evidence)
        ip = gates.CountedIP(getattr(overlay, launch.ip_name))
        inspection = json.loads(old_inspection.read_text(encoding="utf-8"))
        clock = validation.clock_snapshot(Clocks, ip, launch, evidence, "independent_post_program", inspection["snapshot"])
        if ip.starts or ip.writes:
            raise RuntimeError("Read-only verification performed a write")
        evidence.report.update(status="已验证", hardware_programmed=True,
            programmed_by_this_verifier=False, kernel_starts=0, kernel_starts_observed=0,
            new_bit_sha256=pins["bit_sha256"], new_hwh_sha256=pins["hwh_sha256"],
            before_snapshot=inspection["snapshot"], after_snapshot=clock,
            original_program_record_sha256=args.program_record_sha256,
            original_program_status="失败", original_warning=known,
            scope="Read-only verification of already programmed image after known cache ResourceWarning; no additional download/start",
            warning_source="Installed gen_cache pickle.dump(parser, open(...)) has no explicit close; source captured separately",
            rollback_executed=False, finished_utc=datetime.now(timezone.utc).isoformat())
    except BaseException as error:
        evidence.report.update(status="失败", first_error=f"{type(error).__name__}: {error}")
    evidence.save()
    print(json.dumps({"status": evidence.report["status"], "output": str(output),
                      "clock": evidence.report.get("after_snapshot"), "first_error": evidence.report.get("first_error")}, ensure_ascii=False))
    return 0 if evidence.report["status"] == "已验证" else 1


if __name__ == "__main__":
    raise SystemExit(main())
