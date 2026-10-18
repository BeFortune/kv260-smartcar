"""Program a reviewed r04v2 image once without calling the Overlay clock setter.
Default: static validation only. No compute, automatic retry, reset or rollback.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from importlib import metadata
import json
import os
from pathlib import Path
import signal
import sys
import warnings

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "board_validation"))
import board_validate as validation
import safety_gates as gates

OLD_BIT = "c67dd63ccd579ab105840a30c4ca1a61715a26b7631cc323473328a821319eb1"
OLD_HWH = "f4040b704f14ab02ab25b5266226442541be1fc30cc6e786512d9a00ea675649"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def verify_loader(inspection):
    dist = metadata.distribution("pynq")
    require(dist.version == "3.0.1", "Unreviewed PYNQ version")
    for name, expected in inspection["loader_sources"].items():
        require(gates.sha(dist.locate_file(name)) == expected, "Installed loader source changed: " + name)


def verify_identity(state, bitfile, timestamp, bit_sha, hwh_sha):
    require(bitfile == state["bitfile_name"] and timestamp and timestamp == state["timestamp"],
            "Preconstruction runtime identity/timestamp mismatch")
    require(gates.sha(bitfile) == bit_sha and gates.sha(Path(bitfile).with_suffix(".hwh")) == hwh_sha,
            "Current bit/HWH pair is not the pinned pair")
    require(not state.get("shutdown_ips"), "Unexpected shutdown manager; inspect before programming")


def verify_firmware_target(path):
    require(not path.exists() and not path.is_symlink(),
            "New firmware filename already exists or is a symlink; do not overwrite")


def verify_post_identity(state, bitfile, runtime_timestamp, overlay_timestamp, old_timestamp, args):
    require(state.get("timestamp") and state["timestamp"] != old_timestamp,
            "New persistent metadata timestamp was not generated")
    verify_identity(state, bitfile, state["timestamp"], args.bit_sha256, args.hwh_sha256)
    require(runtime_timestamp and runtime_timestamp == overlay_timestamp and runtime_timestamp != old_timestamp,
            "New in-process programming timestamp mismatch")


def clock_state(clocks, ip):
    value = gates.read_clock_snapshot(clocks, ip)
    value["cpu_raw"] = {"ACPU_CTRL": int(clocks._instance._crf_registers.ACPU_CTRL[:])}
    value["cpu_mhz"] = float(clocks.cpu_mhz)
    gates.require_idle(value["ctrl"])
    require(abs(value["fclk0_mhz"] - 100.0) <= 0.5, "Actual PL0 is not target 100 MHz")
    return value


def equal_clocks(before, after):
    for key in ("pl_raw", "pll_raw", "cpu_raw"):
        require(before[key] == after[key], "Programming changed clock registers: " + key)


def program_checked_overlay(args, bit, static_report, inspection, journal, report):
    validation.require_target()
    require(os.environ.get("XILINX_XRT") == "/usr" and "XCL_EMULATION_MODE" not in os.environ,
            "Use reviewed real-hardware XRT environment, never emulation")
    verify_loader(inspection)
    from pynq import Bitstream, Clocks, Overlay, PL
    from pynq.pl_server import global_state
    from pynq._3rdparty import xrt
    require(xrt.XRT_SUPPORTED and not xrt.XRT_EMULATION, "Not real embedded XRT")
    state_path = Path(global_state.STATE_DIR) / "global_pl_state.json"
    require(gates.sha(state_path) == inspection["runtime_state_sha256"], "Old runtime state changed since inspection")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    verify_identity(state, PL.bitfile_name, PL.timestamp, OLD_BIT, OLD_HWH)
    old = Overlay(state["bitfile_name"], download=False)
    old_ip = getattr(old, args.ip_name)
    before = clock_state(Clocks, old_ip)
    equal_clocks(inspection["snapshot"], before)
    journal("old_identity_and_clock_verified", {"state": state, "snapshot": before})
    firmware = Path("/lib/firmware") / (bit.stem + ".bin")
    verify_firmware_target(firmware)
    overlay = Overlay(str(bit), download=False)
    require(overlay.dtbo is None and not overlay.partial, "Unexpected device tree or partial image")
    require(overlay.ip_dict[args.ip_name]["phys_addr"] == 0xA0000000, "Control address mismatch")
    gates.hwh_registers(bit.with_suffix(".hwh"), args.ip_name)
    require(overlay.clock_dict == old.clock_dict, "New metadata changes the PS clock contract")
    equal_clocks(before, clock_state(Clocks, old_ip))
    journal("before_programming", {"method": "Bitstream.download(overlay, overlay.parser)",
            "clock_setters": 0, "firmware_path": str(firmware), "static": static_report})
    for sig in (signal.SIGINT, signal.SIGTERM, *([signal.SIGHUP] if hasattr(signal, "SIGHUP") else [])):
        signal.signal(sig, signal.SIG_IGN)
    report["programming_attempted"] = True
    report["hardware_programmed"] = None
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        Bitstream.download(overlay, overlay.parser)
    report["hardware_programmed"] = True
    journal("programming_returned", {"warnings": [str(item.message) for item in caught],
            "new_timestamp": overlay.timestamp, "kernel_starts": 0})
    require(not caught, "Programming emitted warnings; do not start a kernel")
    after_state = json.loads(state_path.read_text(encoding="utf-8"))
    verify_post_identity(after_state, PL.bitfile_name, PL.timestamp, overlay.timestamp,
                         state["timestamp"], args)
    require(overlay.is_loaded(), "New identity not registered")
    after = clock_state(Clocks, getattr(overlay, args.ip_name))
    equal_clocks(before, after)
    journal("programming_verified_without_compute", {"state": after_state, "snapshot": after})
    return {"status": "已验证", "hardware_programmed": True, "kernel_starts": 0,
            "before_snapshot": before, "after_snapshot": after,
            "runtime_state_before": state, "runtime_state_after": after_state,
            "programming_runtime_timestamp": overlay.timestamp,
            "timestamp_contract": "gen_cache persists before download; post_download timestamps this process separately; fresh worker must verify persisted identity before constructing Overlay",
            "firmware_path": str(firmware), "new_bit_sha256": args.bit_sha256,
            "new_hwh_sha256": args.hwh_sha256, "rollback_executed": False,
            "authority": "Programming completion plus runtime state, host-file SHA and MMIO idle/clock checks; not configuration readback"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("root", "deployment-manifest", "manifest-sha256", "bit-sha256", "hwh-sha256",
                 "system-acceptance", "acceptance-sha256", "old-inspection", "old-inspection-sha256", "output-dir"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--bit", default="overlay/r04_v2_20261018.bit")
    parser.add_argument("--ip-name", default="ffn_accel_0")
    parser.add_argument("--program", action="store_true")
    args = parser.parse_args(argv)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=False)
    report = {"status": "未执行", "date_utc": datetime.now(timezone.utc).isoformat(),
              "arguments": vars(args), "programming_attempted": False, "hardware_programmed": False,
              "kernel_starts": 0, "scope": "One reviewed image programming only; no kernel start, clock write or recovery",
              "loader_sha256": gates.sha(__file__)}
    with (output / "events.jsonl").open("x", encoding="utf-8") as stream:
        def journal(kind, data):
            stream.write(json.dumps({"date_utc": datetime.now(timezone.utc).isoformat(),
                                    "kind": kind, "data": data}, ensure_ascii=False, default=str) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            require(gates.sha(args.old_inspection) == args.old_inspection_sha256, "Old inspection SHA changed")
            inspection = json.loads(Path(args.old_inspection).read_text(encoding="utf-8"))
            require(inspection["status"] == "已验证" and inspection["bit_sha256"] == OLD_BIT
                    and inspection["hwh_sha256"] == OLD_HWH, "Old inspection not pinned r03")
            _, bit, _, static_report = validation.static(args)
            journal("static_verified", static_report)
            if args.program:
                report.update(program_checked_overlay(args, bit, static_report, inspection, journal, report))
            else:
                report.update(status="已验证", scope="Static checks only; no PYNQ import or hardware action")
        except BaseException as error:
            report.update(status="失败", first_error=f"{type(error).__name__}: {error}",
                          operator_action="No automatic retry/reset/rollback. Inspect journal to determine whether programming began.")
            journal("failure", report)
        report["finished_utc"] = datetime.now(timezone.utc).isoformat()
        with (output / "report.json").open("x", encoding="utf-8") as result:
            json.dump(report, result, ensure_ascii=False, indent=2, default=str)
            result.write("\n")
    print(json.dumps({"status": report["status"], "output": str(output), "first_error": report.get("first_error")}, ensure_ascii=False))
    return 0 if report["status"] == "已验证" else 1


if __name__ == "__main__":
    raise SystemExit(main())
