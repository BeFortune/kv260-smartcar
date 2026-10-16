"""Read actual generated system HWH without loading PYNQ or configuring a board."""

import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
REPO = Path("D:/Learning/FPGA/pynq_ffn")


def properties(node):
    return {item.get("NAME"): item.get("VALUE") for item in node.findall("./PROPERTY")}


def main():
    path = ROOT / "vivado03/ffn_overlay.gen/sources_1/bd/ffn_overlay/hw_handoff/ffn_overlay.hwh"
    tree = ET.parse(path).getroot()
    modules = [node for node in tree.findall(".//MODULE") if node.get("INSTANCE") == "ffn_accel_0"]
    assert len(modules) == 1
    module = modules[0]
    registers = {}
    for register in module.findall("./ADDRESSBLOCKS/ADDRESSBLOCK/REGISTERS/REGISTER"):
        data = properties(register)
        fields = {}
        for field in register.findall("./FIELDS/FIELD"):
            values = properties(field)
            fields[field.get("NAME")] = {"bit_offset": int(values["BIT_OFFSET"], 0),
                                         "bit_width": int(values["BIT_WIDTH"], 0)}
        registers[register.get("NAME")] = {"address_offset": int(data["ADDRESS_OFFSET"], 0),
                                          "size": int(data["SIZE"], 0), "fields": fields}
    sys.path.insert(0, str(REPO))
    from ps.pynq_backend import validate_registers
    validate_registers(registers)
    expected = {"x": (0x10, 0x14), "y": (0x1C, 0x20), "w1": (0x28, 0x2C),
                "b1": (0x34, 0x38), "w2": (0x40, 0x44), "b2": (0x4C, 0x50)}
    for name, (low, high) in expected.items():
        assert registers[name + "_1"]["address_offset"] == low and registers[name + "_1"]["size"] == 32
        assert registers[name + "_2"]["address_offset"] == high and registers[name + "_2"]["size"] == 32
    assert registers["num_tokens"]["address_offset"] == 0x58 and registers["num_tokens"]["size"] == 32
    widths = {}
    for bus in module.findall("./BUSINTERFACES/BUSINTERFACE"):
        if bus.get("NAME") in ("m_axi_gmem0", "m_axi_gmem1"):
            params = {item.get("NAME"): item.get("VALUE") for item in bus.findall("./PARAMETER")}
            assert int(params["ADDR_WIDTH"], 0) == 64
            widths[bus.get("NAME")] = 64
    assert len(widths) == 2
    ranges = [node.attrib for node in module.findall("./MEMORYMAP/MEMRANGE")]
    actual = {(row["MASTERBUSINTERFACE"], row["ADDRESSBLOCK"]):
              (int(row["BASEVALUE"], 0), int(row["HIGHVALUE"], 0)) for row in ranges}
    expected_ranges = {(bus, block): bounds for bus in ("m_axi_gmem0", "m_axi_gmem1")
                       for block, bounds in (("HP0_DDR_LOW", (0, 0x7FFFFFFF)),
                                             ("HP0_DDR_HIGH", (0x800000000, 0xFFFFFFFFF)))}
    assert actual == expected_ranges
    clock = next(node for node in tree.findall(".//MODULE") if node.get("INSTANCE") == "zynq_ultra_ps_e_0")
    params = {node.get("NAME"): node.get("VALUE") for node in clock.findall("./PARAMETERS/PARAMETER")}
    report = {"passed": True, "source": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
              "scope": "generated BD HWH static checks; not yet matched exported bit/HWH or board acceptance",
              "module": module.attrib, "registers": registers, "master_address_widths": widths,
              "full_ddr_ranges": ranges,
              "actual_pl0_frequency_MHz": params["PSU__CRL_APB__PL0_REF_CTRL__ACT_FREQMHZ"],
              "driver_validate_registers_passed": True, "board_execution": False}
    out = ROOT / "evidence/software/generated_hwh_precheck.json"
    with out.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
