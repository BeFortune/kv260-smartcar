#!/usr/bin/env python3
"""Emit a Vitis Unified 2026.1 HLS configuration with explicit 64-bit AXI.

Option names are checked against the installed 2026.1 hls_options.json.
FPGA_PART must still be supplied from verified board/product data. Configuration
emission is not proof of successful simulation, synthesis or implementation.
"""

import argparse
import math
import os
from pathlib import Path
import re
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", default="build/hls_config.cfg",
        help="New cfg path within this hls directory; existing files are not overwritten.",
    )
    options = parser.parse_args()
    part = os.environ.get("FPGA_PART", "").strip()
    if not part or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]*", part):
        parser.error("Set FPGA_PART to the verified full device part name (no device guessed).")
    clock_text = os.environ.get("CLOCK_NS", "10").strip()
    try:
        clock = float(clock_text)
    except ValueError:
        parser.error("CLOCK_NS must be a finite positive number in nanoseconds.")
    if not math.isfinite(clock) or clock <= 0:
        parser.error("CLOCK_NS must be a finite positive number in nanoseconds.")

    root = Path(__file__).resolve().parent
    output = (root / options.output).resolve()
    if root not in output.parents:
        parser.error("Output must be inside this hls directory.")
    for name in ("ffn_accel.cpp", "ffn_accel.hpp", "testbench.cpp"):
        if not (root / name).is_file():
            parser.error("Missing source: " + name)
    # Vitis 2026.1 changes cwd to the cfg directory before resolving sources.
    # Keep both the source entries and their per-file flags consistent with it.
    source = os.path.relpath(root / "ffn_accel.cpp", output.parent).replace("\\", "/")
    testbench = os.path.relpath(root / "testbench.cpp", output.parent).replace("\\", "/")
    flags = "-std=c++11 -DFFN_USE_HLS_MATH -ffp-contract=off"
    config = "\n".join([
        "# Vitis Unified 2026.1 option names checked against installed tool data.",
        "# Explicit 64-bit AXI configuration; emitted cfg is NOT a hardware sign-off.",
        "# Actual device support, csim/csynth, RTL ABI/address widths, cosim,",
        "# packaging and implemented timing must be checked in successful tool runs.",
        "part=" + part,
        "",
        "[hls]",
        "clock=" + format(clock, ".17g"),
        "flow_target=vivado",
        "syn.top=ffn_accel",
        "syn.interface.m_axi_addr64=true",
        "syn.file=" + source,
        "syn.file_cflags=" + source + "," + flags,
        "syn.file_csimflags=" + source + "," + flags,
        "tb.file=" + testbench,
        "tb.file_cflags=" + testbench + "," + flags,
        "package.output.format=ip_catalog",
        "package.output.syn=false",
        "",
    ])
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(config)
    except FileExistsError:
        parser.error("Refusing to overwrite existing output: " + str(output))
    print("Wrote Vitis 2026.1 configuration: " + str(output))
    print("Explicit syn.interface.m_axi_addr64=true; verify generated RTL and ABI.")
    print("No Vitis command was executed by this generator; no build success is claimed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
