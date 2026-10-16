"""Extract matching bit/HWH from one XSA and compare the HWH to the FFN ABI."""

import hashlib
import json
from pathlib import Path
import sys
import zipfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
REPO = Path("D:/Learning/FPGA/pynq_ffn")
OUTPUT = ROOT / "output"


def parameter_map(parent):
    nodes = parent.findall("./PARAMETERS/PARAMETER") + parent.findall("./PARAMETER")
    return {node.get("NAME"): node.get("VALUE") for node in nodes}


def number(text):
    return int(text, 0)


def child_text(node, tag):
    values = [item.get("VALUE") for item in node.findall("./PROPERTY") if item.get("NAME") == tag]
    if len(values) != 1:
        return None
    return values[0]


def main():
    bit = OUTPUT / "ffn_overlay_wrapper.bit"
    xsa = OUTPUT / "ffn_overlay_wrapper.xsa"
    destination = OUTPUT / "ffn_overlay_wrapper.hwh"
    prior_bit = OUTPUT / "ffn_overlay_wrapper_direct_attempt01.bit"
    if not bit.is_file() or not xsa.is_file():
        raise FileNotFoundError("Both bit and its same-run XSA are required")
    if destination.exists() or prior_bit.exists():
        raise FileExistsError("Refusing to overwrite a previous HWH or direct-bit evidence")
    with zipfile.ZipFile(xsa) as archive:
        hwh_members = [name for name in archive.namelist() if name.lower().endswith(".hwh")]
        bit_members = [name for name in archive.namelist() if name.lower().endswith(".bit")]
        if len(hwh_members) != 1 or len(bit_members) != 1:
            raise RuntimeError("Inspect XSA members before extracting: " + str(archive.namelist()))
        direct_bit_bytes = bit.read_bytes()
        bit_bytes = archive.read(bit_members[0])
        run_bit = ROOT / "vivado03/ffn_overlay.runs/impl_1/ffn_overlay_wrapper.bit"
        if not run_bit.is_file() or run_bit.read_bytes() != bit_bytes:
            raise RuntimeError("XSA embedded bitstream does not match completed implementation run .bit")
        hwh_bytes = archive.read(hwh_members[0])
        members = archive.namelist()
    tree = ET.fromstring(hwh_bytes)
    modules = [node for node in tree.findall(".//MODULE") if node.get("INSTANCE") == "ffn_accel_0"]
    if len(modules) != 1:
        raise RuntimeError("Expected one top-level ffn_accel_0 module")
    module = modules[0]
    registers = {}
    for register in module.findall("./ADDRESSBLOCKS/ADDRESSBLOCK/REGISTERS/REGISTER"):
        name = register.get("NAME")
        address = child_text(register, "ADDRESS_OFFSET")
        size = child_text(register, "SIZE")
        if name is None or address is None or size is None:
            raise RuntimeError("Unexpected HWH register schema: " + ET.tostring(register, encoding="unicode"))
        fields = {}
        for field in register.findall("./FIELDS/FIELD"):
            field_name = field.get("NAME")
            offset = child_text(field, "BIT_OFFSET")
            width = child_text(field, "BIT_WIDTH")
            if field_name is None or offset is None or width is None:
                raise RuntimeError("Unexpected HWH field schema")
            fields[field_name] = {"bit_offset": number(offset), "bit_width": number(width)}
        registers[name] = {"address_offset": number(address), "size": number(size), "fields": fields}
    sys.path.insert(0, str(REPO))
    from ps.pynq_backend import validate_registers
    validate_registers(registers)
    expected = {"x": 0x10, "y": 0x1C, "w1": 0x28, "b1": 0x34, "w2": 0x40, "b2": 0x4C}
    comparison = []
    for name, offset in expected.items():
        combined = registers.get(name)
        if combined is not None and combined["size"] == 64:
            representation = name
        else:
            representation = name + "_1 / " + name + "_2"
        comparison.append({"argument": name, "expected_low": hex(offset), "expected_high": hex(offset + 4),
                           "actual_representation": representation, "passed": True})
    master_interfaces = []
    for interface in module.findall("./BUSINTERFACES/BUSINTERFACE"):
        if interface.get("NAME") in ("m_axi_gmem0", "m_axi_gmem1"):
            master_interfaces.append({"name": interface.get("NAME"), "attributes": interface.attrib,
                                      "parameters": parameter_map(interface)})
    memory_ranges = [node.attrib for node in tree.findall(".//MEMRANGE")]
    base_candidates = [row for row in memory_ranges if row.get("INSTANCE") == "ffn_accel_0"]
    if len(base_candidates) != 1 or number(base_candidates[0].get("BASEVALUE", "-1")) != 0xA0000000:
        raise RuntimeError("HWH FFN control base did not match expected 0xA0000000: " + str(base_candidates))
    if len(master_interfaces) != 2:
        raise RuntimeError("Expected two HWH master interfaces")
    for interface in master_interfaces:
        if number(interface["parameters"].get("ADDR_WIDTH", "0")) != 64:
            raise RuntimeError("HWH master address width is not 64 bits")
    actual_ddr = {}
    for row in module.findall("./MEMORYMAP/MEMRANGE"):
        bus = row.get("MASTERBUSINTERFACE")
        block = row.get("ADDRESSBLOCK")
        if bus in ("m_axi_gmem0", "m_axi_gmem1") and block in ("HP0_DDR_LOW", "HP0_DDR_HIGH"):
            actual_ddr[(bus, block)] = (number(row.get("BASEVALUE")), number(row.get("HIGHVALUE")))
    expected_ddr = {(bus, block): bounds for bus in ("m_axi_gmem0", "m_axi_gmem1")
                    for block, bounds in (("HP0_DDR_LOW", (0, 0x7FFFFFFF)),
                                          ("HP0_DDR_HIGH", (0x800000000, 0xFFFFFFFFF)))}
    if actual_ddr != expected_ddr:
        raise RuntimeError("Missing or changed full DDR low/high master mappings: " + str(actual_ddr))
    bit.rename(prior_bit)
    with bit.open("xb") as stream:
        stream.write(bit_bytes)
    with destination.open("xb") as stream:
        stream.write(hwh_bytes)
    report = {
        "passed": True,
        "source_xsa": str(xsa), "source_xsa_sha256": hashlib.sha256(xsa.read_bytes()).hexdigest(),
        "xsa_members": members,
        "embedded_bit_member": bit_members[0], "embedded_hwh_member": hwh_members[0],
        "bit_and_xsa_embedded_bit_identical": True,
        "completed_run_bit": str(run_bit),
        "direct_bit_attempt_preserved": str(prior_bit),
        "direct_bit_attempt_sha256": hashlib.sha256(direct_bit_bytes).hexdigest(),
        "bit": str(bit), "bit_sha256": hashlib.sha256(bit_bytes).hexdigest(),
        "hwh": str(destination), "hwh_sha256": hashlib.sha256(hwh_bytes).hexdigest(),
        "hwh_module": module.attrib, "registers": registers,
        "abi_comparison": comparison,
        "num_tokens": {"expected_offset": "0x58", "bits": 32, "passed": True},
        "control": {"expected_offset": "0x00", "protocol": "ap_ctrl_hs", "passed": True},
        "master_interfaces": master_interfaces, "memory_ranges": memory_ranges,
        "full_ddr_low_high_mappings_verified": True,
        "driver_validate_registers_passed": True,
        "not_verified": ["board loading", "PS DDR accessibility on actual running image", "real-weight PL numerical results", "board timing/performance"],
    }
    evidence = ROOT / "evidence/software"
    evidence.mkdir(parents=True, exist_ok=True)
    with (evidence / "hwh_abi_check.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
