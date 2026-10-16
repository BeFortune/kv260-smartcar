"""Verify emitted HLS register ABI and AXI address widths before cosimulation."""

import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
WORK = ROOT / "hls/build/ffn_accel_modern/hls"
HEADER = WORK / "impl/misc/drivers/ffn_accel_v1_0/src/xffn_accel_hw.h"
CONTROL = WORK / "syn/verilog/ffn_accel_control_s_axi.v"
TOP = WORK / "syn/verilog/ffn_accel.v"
REPORT = WORK / "syn/report/ffn_accel_csynth.xml"


def main():
    command = json.loads((ROOT / "evidence/hls/csynth.command.json").read_text(encoding="utf-8"))
    assert command["exit_code"] == 0
    header = HEADER.read_text(encoding="utf-8")
    control = CONTROL.read_text(encoding="utf-8")
    top = TOP.read_text(encoding="utf-8")
    defines = {name: int(value, 0) for name, value in re.findall(r"^#define\s+(\w+)\s+(0x[0-9a-fA-F]+|\d+)\s*$", header, re.M)}
    addresses = {name: int(value, 16) for name, value in re.findall(r"\b(ADDR_\w+)\s*=\s*7'h([0-9a-fA-F]+)", control)}
    parameters = {name: int(value) for name, value in re.findall(r"parameter\s+(\w+)\s*=\s*(\d+)\s*;", top)}
    assert "Protocol Used: ap_ctrl_hs" in control
    assert "ap_continue" not in control.lower()
    assert defines["XFFN_ACCEL_CONTROL_ADDR_AP_CTRL"] == addresses["ADDR_AP_CTRL"] == 0
    assert parameters["C_S_AXI_CONTROL_DATA_WIDTH"] == 32
    fields = {"ap_start": 0, "ap_done": 1, "ap_idle": 2, "ap_ready": 3, "auto_restart": 7}
    for name, bit in fields.items():
        assert re.search(r"//\s*bit\s+" + str(bit) + r"\s*-\s*" + name + r"\b", control)
    expected = {"x": 0x10, "y": 0x1C, "w1": 0x28, "b1": 0x34, "w2": 0x40, "b2": 0x4C}
    register_rows = []
    for name, offset in expected.items():
        key = name.upper()
        assert defines[f"XFFN_ACCEL_CONTROL_ADDR_{key}_DATA"] == offset
        assert defines[f"XFFN_ACCEL_CONTROL_BITS_{key}_DATA"] == 64
        assert addresses[f"ADDR_{key}_DATA_0"] == offset
        assert addresses[f"ADDR_{key}_DATA_1"] == offset + 4
        for index, bits in ((0, "31:0"), (1, "63:32")):
            assert f"waddr == ADDR_{key}_DATA_{index}" in control
            assert f"int_{name}[{bits}] <= (WDATA[31:0] & wmask)" in control
        register_rows.append({"argument": name, "low": hex(offset), "high": hex(offset + 4), "bits": 64})
    assert defines["XFFN_ACCEL_CONTROL_ADDR_NUM_TOKENS_DATA"] == addresses["ADDR_NUM_TOKENS_DATA_0"] == 0x58
    assert defines["XFFN_ACCEL_CONTROL_BITS_NUM_TOKENS_DATA"] == 32
    widths = {}
    for bundle in ("gmem0", "gmem1"):
        key = "C_M_AXI_" + bundle.upper() + "_ADDR_WIDTH"
        assert parameters[key] == 64
        for channel in ("ARADDR", "AWADDR"):
            signal = f"m_axi_{bundle}_{channel}"
            assert re.search(r"output\s+\[" + key + r"\s*-\s*1:0\]\s+" + signal + r"\s*;", top)
            widths[signal] = 64
    report = ET.parse(REPORT).getroot()
    resources = {node.tag: int(node.text) for node in report.findall("./AreaEstimates/Resources/*")}
    performance = {node.tag: node.text for node in report.findall("./PerformanceEstimates/SummaryOfOverallLatency/*")}
    timing = {node.tag: node.text for node in report.findall("./PerformanceEstimates/SummaryOfTimingAnalysis/*")}
    pipeline_rows = []
    for path in sorted((WORK / "syn/report").glob("*_csynth.xml")):
        tree = ET.parse(path).getroot()
        for item in tree.findall("./PerformanceEstimates/SummaryOfLoopLatency/*"):
            pipeline_rows.append({"report": path.name, **{node.tag: node.text for node in item}})
    evidence = {
        "passed": True,
        "protocol": "ap_ctrl_hs",
        "control_offset": "0x00",
        "control_data_width": 32,
        "control_fields": fields,
        "pointers": register_rows,
        "num_tokens": {"offset": "0x58", "bits": 32},
        "emitted_master_address_widths": widths,
        "resources_hls_estimate": resources,
        "latency_hls_estimate": performance,
        "timing_hls_estimate": timing,
        "loops": pipeline_rows,
        "evidence_files": [{"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in (HEADER, CONTROL, TOP, REPORT)],
        "not_verified_here": ["cosimulation", "packaged_IP", "Vivado system address mapping", "implemented timing", "board execution"],
    }
    destination = ROOT / "evidence/hls/rtl_abi_check.json"
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(evidence, stream, indent=2)
        stream.write("\n")
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
