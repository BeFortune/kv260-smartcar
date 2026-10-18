"""Extract one same-run top-level HWH and validate PS ABI without importing PYNQ."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import xml.etree.ElementTree as ET
import zipfile
from abi_reference import validate_registers


def sha(data):
    return hashlib.sha256(data).hexdigest()


def prop(node, key):
    values = [x.get('VALUE') for x in node.findall('./PROPERTY') if x.get('NAME') == key]
    if len(values) != 1:
        raise ValueError(f'Missing/ambiguous property {key}')
    return int(values[0], 0)


def verify(root):
    root = Path(root)
    xsa = root / 'traceability/ffn_overlay_wrapper.xsa'
    run_bit = root / 'vivado03/ffn_overlay.runs/impl_1/ffn_overlay_wrapper.bit'
    with zipfile.ZipFile(xsa) as z:
        bits = [n for n in z.namelist() if n.lower().endswith('.bit')]
        candidates = []
        for name in z.namelist():
            if name.lower().endswith('.hwh'):
                tree = ET.fromstring(z.read(name))
                modules = [m for m in tree.findall('.//MODULE') if m.get('INSTANCE') == 'ffn_accel_0']
                if modules:
                    candidates.append((name, tree, modules))
        if len(bits) != 1 or len(candidates) != 1:
            raise ValueError('Require unique embedded bit and FFN top HWH')
        name, tree, modules = candidates[0]
        if PurePosixPath(name).stem != 'ffn_overlay' or len(modules) != 1:
            raise ValueError('Require ffn_overlay.hwh with unique ffn_accel_0, not sub-HWH')
        bit, hwh = z.read(bits[0]), z.read(name)
        if sha(bit) != sha(run_bit.read_bytes()):
            raise ValueError('XSA bit differs from completed run bit')
        members = z.namelist()
    module = modules[0]
    if module.get('VLNV') != 'xilinx.com:hls:ffn_accel:1.0':
        raise ValueError('Unexpected HWH kernel VLNV')
    registers = {}
    for reg in module.findall('./ADDRESSBLOCKS/ADDRESSBLOCK/REGISTERS/REGISTER'):
        key = reg.get('NAME')
        if key in registers:
            raise ValueError('Duplicate register')
        registers[key] = {'address_offset': prop(reg, 'ADDRESS_OFFSET'), 'size': prop(reg, 'SIZE'),
                          'fields': {f.get('NAME'): {'bit_offset': prop(f, 'BIT_OFFSET'), 'bit_width': prop(f, 'BIT_WIDTH')}
                                     for f in reg.findall('./FIELDS/FIELD')}}
    validate_registers(registers)
    control = [n for n in tree.findall('.//MEMRANGE') if n.get('INSTANCE') == 'ffn_accel_0']
    if len(control) != 1 or int(control[0].get('BASEVALUE', '-1'), 0) != 0xA0000000:
        raise ValueError('Unexpected control base')
    masters = [n for n in module.findall('./BUSINTERFACES/BUSINTERFACE') if n.get('NAME') in ('m_axi_gmem0', 'm_axi_gmem1')]
    if len(masters) != 2 or len({n.get('NAME') for n in masters}) != 2:
        raise ValueError('Expected two distinct masters')
    for bus in masters:
        params = {p.get('NAME'): p.get('VALUE') for p in bus.findall('./PARAMETERS/PARAMETER') + bus.findall('./PARAMETER')}
        if int(params.get('ADDR_WIDTH', '0'), 0) != 64:
            raise ValueError('HWH master not 64-bit address')
    actual = {}
    for row in module.findall('./MEMORYMAP/MEMRANGE'):
        key = row.get('MASTERBUSINTERFACE'), row.get('ADDRESSBLOCK')
        if key[0] in ('m_axi_gmem0', 'm_axi_gmem1') and key[1] in ('HP0_DDR_LOW', 'HP0_DDR_HIGH'):
            if key in actual:
                raise ValueError('Duplicate memory mapping')
            actual[key] = int(row.get('BASEVALUE'), 0), int(row.get('HIGHVALUE'), 0)
    expected = {(bus, block): bounds for bus in ('m_axi_gmem0', 'm_axi_gmem1')
                for block, bounds in [('HP0_DDR_LOW', (0, 0x7fffffff)), ('HP0_DDR_HIGH', (0x800000000, 0xfffffffff))]}
    if actual != expected:
        raise ValueError('HP0 low/high DDR map changed')
    output = root / 'load'
    output.mkdir(exist_ok=False)
    for suffix, data in [('bit', bit), ('hwh', hwh)]:
        with (output / ('ffn_overlay_wrapper.' + suffix)).open('xb') as f:
            f.write(data)
    report = {'status': '已验证', 'xsa_sha256': sha(xsa.read_bytes()), 'bit_sha256': sha(bit), 'hwh_sha256': sha(hwh),
              'run_bit_sha256': sha(run_bit.read_bytes()), 'embedded_hwh': name, 'members': members,
              'registers': registers, 'static_ps_validate_registers': True,
              'not_executed': ['board programming', 'real PL numeric/performance tests']}
    with (root / 'traceability/artifact_verification.json').open('x', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return report


if __name__ == '__main__':
    import sys
    verify(sys.argv[1])
