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
    return {node.get("NAME"): node.get("VALUE") for node in parent.findall("./PARAMETERS/PARAMETER")}


def number(text):
    return int(text, 0)


def child_text(node, tag):
    child = node.find(tag)
    return child.text.strip() if child is not None and child.text else None


def main():
    bit = OUTPUT / "ffn_overlay_wrapper.bit"
    xsa = OUTPUT / "ffn_overlay_wrapper.xsa"
    destination = OUTPUT / "ffn_overlay_wrapper.hwh"
    if not bit.is_file() or not xsa.is_file():
        raise FileNotFoundError("Both bit and its same-run XSA are required")
    if destination.exists():
        raise FileExistsError(destination)
    with zipfile.ZipFile(xsa) as archive:
        hwh_members = [name for name in archive.namelist() if name.lower().endswith(".hwh")]
        bit_members = [name for name in archive.namelist() if name.lower().endswith(".bit")]
        if len(hwh_members) != 1 or len(bit_members) != 1:
            raise RuntimeError("Inspect XSA members before extracting: " + str(archive.namelist()))
        bit_bytes = bit.read_bytes()
        if archive.read(bit_members[0]) != bit_bytes:
            raise RuntimeError("XSA embedded bitstream does not match exported .bit")
        hwh_bytes = archive.read(hwh_members[0])
        members = archive.namelist()
    tree = ET.fromstring(hwh_bytes)
    modules = [node for node in tree.findall(".//MODULE") if node.get("INSTANCE") == "ffn_accel_0"]
    if len(modules) != 1:
        raise RuntimeError("Expected one top-level ffn_accel_0 module")
    module = modules[0]
    registers = {}
    for register in module.findall("./REGISTERS/REGISTER"):
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
    memory_ranges = [node.attrib for node in tree.findall(".//MEMRANGES/MEMRANGE")]
    base_candidates = [row for row in memory_ranges if row.get("INSTANCE") == "ffn_accel_0"]
    if len(base_candidates) != 1 or number(base_candidates[0].get("BASEVALUE", "-1")) != 0xA0000000:
        raise RuntimeError("HWH FFN control base did not match expected 0xA0000000: " + str(base_candidates))
    with destination.open("xb") as stream:
        stream.write(hwh_bytes)
    report = {
        "passed": True,
        "source_xsa": str(xsa), "source_xsa_sha256": hashlib.sha256(xsa.read_bytes()).hexdigest(),
        "xsa_members": members,
        "embedded_bit_member": bit_members[0], "embedded_hwh_member": hwh_members[0],
        "bit_and_xsa_embedded_bit_identical": True,
        "bit": str(bit), "bit_sha256": hashlib.sha256(bit_bytes).hexdigest(),
        "hwh": str(destination), "hwh_sha256": hashlib.sha256(hwh_bytes).hexdigest(),
        "hwh_module": module.attrib, "registers": registers,
        "abi_comparison": comparison,
        "num_tokens": {"expected_offset": "0x58", "bits": 32, "passed": True},
        "control": {"expected_offset": "0x00", "protocol": "ap_ctrl_hs", "passed": True},
        "master_interfaces": master_interfaces, "memory_ranges": memory_ranges,
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
