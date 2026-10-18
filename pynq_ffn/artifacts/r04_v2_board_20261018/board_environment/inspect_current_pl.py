"""Inspect current PL identity and clocks; no configuration writes or kernel starts."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path

OLD_BIT = "c67dd63ccd579ab105840a30c4ca1a61715a26b7631cc323473328a821319eb1"
OLD_HWH = "f4040b704f14ab02ab25b5266226442541be1fc30cc6e786512d9a00ea675649"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def snapshot(clocks, ip):
    instance = clocks._instance
    crl, crf = instance._crl_registers, instance._crf_registers
    return {"ctrl": int(ip.read(0)),
            "pl_raw": [int(reg[:]) for reg in instance.PL_CLK_CTRLS],
            "pll_raw": {name: int(getattr(bank, name)[:]) for bank, names in
                        ((crl, ("IOPLL_CTRL", "RPLL_CTRL")),
                         (crf, ("APLL_CTRL", "DPLL_CTRL", "VPLL_CTRL"))) for name in names},
            "cpu_raw": {"ACPU_CTRL": int(crf.ACPU_CTRL[:])},
            "cpu_mhz": float(clocks.cpu_mhz),
            "fclk0_mhz": float(clocks.fclk0_mhz)}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    require(not args.output.exists(), "Output already exists")
    require(os.geteuid() == 0, "Read-only privileged inspection requires existing sudo authorization")
    dist = metadata.distribution("pynq")
    require(dist.version == "3.0.1", "Unreviewed PYNQ version")
    expected_sources = {
        "pynq/overlay.py": "949bbd3a1d296b487702f4c49013de24c3d6eb44da9fb2ebe1ada70d054237d7",
        "pynq/bitstream.py": "b7e37302fefca9f2d09ebf487aa14abac8e603e12c544e8888d9ebaca9bf2914",
        "pynq/pl_server/embedded_device.py": "cfd41079517552329914c125ecfec97293e0d3160db008ed18de9afa7834dee1",
        "pynq/pl_server/device.py": "cbfac4c5d0e78b1c2aa726ac18dcd5bf52d3d693159e8f948a0977b4af30ed74",
        "pynq/pl_server/xrt_device.py": "b79765ec53d92ac97657e95a7c990e8d1bdacb133161b278e1a8c3e4159fcd5e",
    }
    for relative, expected in expected_sources.items():
        require(sha(dist.locate_file(relative)) == expected, "Installed loader source changed")
    from pynq import Clocks, Overlay, PL
    from pynq.pl_server import global_state
    state_path = Path(global_state.STATE_DIR) / "global_pl_state.json"
    raw = state_path.read_bytes()
    state = json.loads(raw)
    bitfile, timestamp = PL.bitfile_name, PL.timestamp
    require(bitfile == state["bitfile_name"] and timestamp == state["timestamp"], "Runtime identity mismatch")
    require(sha(bitfile) == OLD_BIT and sha(Path(bitfile).with_suffix(".hwh")) == OLD_HWH,
            "Current identity is not the reviewed r03 pair; do not reprogram")
    # PYNQ persists SHA1 bookkeeping; the pinned SHA256 pair above is the integrity gate.
    require(hashlib.sha1(Path(bitfile).read_bytes()).hexdigest() == state["bitfile_hash"], "Runtime SHA1 mismatch")
    overlay = Overlay(bitfile, download=False)
    require(overlay.is_loaded(), "Runtime overlay identity mismatch")
    ip = getattr(overlay, "ffn_accel_0")
    clocks = snapshot(Clocks, ip)
    require(clocks["ctrl"] & 4 and not clocks["ctrl"] & 0x81, "Old kernel not safely idle")
    require(abs(clocks["fclk0_mhz"] - 100.0) <= 0.5, "Current actual PL0 not at target 100 MHz")
    result = {"status": "已验证", "date_utc": datetime.now(timezone.utc).isoformat(),
              "scope": "Read-only current PL identity/clock inspection; no download/MMIO writes/kernel starts",
              "self_sha256": sha(__file__), "loader_sources": expected_sources,
              "runtime_state_path": str(state_path), "runtime_state_sha256": hashlib.sha256(raw).hexdigest(),
              "runtime_state": state, "bit_sha256": OLD_BIT, "hwh_sha256": OLD_HWH,
              "snapshot": clocks, "clock_dict_metadata_only": overlay.clock_dict,
              "ip_name": "ffn_accel_0", "ip_phys_addr": ip.mmio.base_addr,
              "authority": "Runtime state and pinned host bit/hwh files; not FPGA configuration readback"}
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"status": result["status"], "output": str(args.output), "snapshot": clocks}, ensure_ascii=False))


if __name__ == "__main__":
    main()
