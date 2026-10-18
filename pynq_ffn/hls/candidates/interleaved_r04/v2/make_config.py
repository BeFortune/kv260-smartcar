#!/usr/bin/env python3
"""Emit independent Vitis Unified 2026.1 full/small configurations, without builds."""
import argparse
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--testbench", choices=("full", "small"), default="full")
    parser.add_argument("--output", help="New cfg path inside this candidate directory")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    output = (root / (args.output or ("build/" + args.testbench + ".cfg"))).resolve()
    if root not in output.parents:
        parser.error("Output must be inside the candidate directory")
    tb_name = "testbench.cpp" if args.testbench == "full" else "testbench_small.cpp"
    for name in ("ffn_accel.cpp", "ffn_accel.hpp", "test_support.hpp", tb_name):
        if not (root / name).is_file():
            parser.error("Missing input: " + name)
    source = os.path.relpath(root / "ffn_accel.cpp", output.parent).replace("\\", "/")
    tb = os.path.relpath(root / tb_name, output.parent).replace("\\", "/")
    flags = "-std=c++11 -DFFN_USE_HLS_MATH -ffp-contract=off"
    config = "\n".join([
        "# Independent interleaved_r04_v2; full and small RTL scopes are distinct.",
        "part=xck26-sfvc784-2LV-c", "", "[hls]", "clock=10",
        "flow_target=vivado", "syn.top=ffn_accel", "syn.interface.m_axi_addr64=true",
        "syn.file=" + source, "syn.file_cflags=" + source + "," + flags,
        "syn.file_csimflags=" + source + "," + flags,
        "tb.file=" + tb, "tb.file_cflags=" + tb + "," + flags,
        "package.output.format=ip_catalog", "package.output.syn=false", "",
    ])
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(config)
    except FileExistsError:
        parser.error("Refusing to overwrite: " + str(output))
    print("Wrote " + str(output))
    print("scope=" + args.testbench + "; no HLS command executed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
