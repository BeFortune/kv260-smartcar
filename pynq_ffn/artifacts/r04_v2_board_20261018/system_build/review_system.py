"""Independent local review of collected Vivado facts, never connects to hardware.
The coordinator, not this report, publishes r04v2.system_acceptance.v1.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'scripts'))
from abi_reference import validate_registers


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def timing(text):
    start = text.index('| Design Timing Summary')
    row = re.search(r'^\s*(-?\d+\.\d+(?:\s+-?\d+(?:\.\d+)?){11})\s*$', text[start:], re.M)
    if not row:
        raise ValueError('Cannot parse 12-column global timing summary')
    values = row.group(1).split()
    result = dict(zip(('wns_ns', 'tns_ns', 'setup_failing', 'setup_total', 'whs_ns', 'ths_ns',
                       'hold_failing', 'hold_total', 'wpws_ns', 'tpws_ns', 'pulse_failing', 'pulse_total'),
                      map(float, values)))
    if not (result['wns_ns'] >= 0 and result['whs_ns'] >= 0 and
            result['tns_ns'] == 0 and result['ths_ns'] == 0 and
            result['setup_failing'] == result['hold_failing'] == result['pulse_failing'] == 0 and
            'All user specified timing constraints are met.' in text):
        raise ValueError('Independent timing gate failed')
    clocks = re.findall(r'^(\S+)\s+\{[\d.]+\s+[\d.]+\}\s+([\d.]+)\s+([\d.]+)\s*$', text, re.M)
    if clocks != [('clk_pl_0', '10.000', '100.000')]:
        raise ValueError('Expected only clk_pl_0 10ns / 100MHz')
    for check in ('no_clock', 'unconstrained_internal_endpoints'):
        if not re.search(r'checking ' + check + r' \(0\)', text):
            raise ValueError('Unexpected timing check ' + check)
    result['clocks'] = clocks
    return result


def route(text):
    result = {}
    for label in ('routable nets', 'fully routed nets', 'nets with routing errors'):
        match = re.search(r'# of ' + label + r'[. ]*:[ ]*([0-9]+)', text, re.I)
        if not match:
            raise ValueError('Cannot parse routing ' + label)
        result[label] = int(match.group(1))
    if result['routable nets'] <= 0 or result['routable nets'] != result['fully routed nets'] or result['nets with routing errors']:
        raise ValueError('Independent route gate failed')
    return result


def drc(text):
    rows = re.findall(r'^\|\s*([\w-]+)\s*\|\s*(Error|Critical Warning|Warning|Advisory)\s*\|.*\|\s*(\d+)\s*\|', text, re.M)
    if not rows:
        raise ValueError('Cannot parse DRC summary')
    if any(severity in ('Error', 'Critical Warning') for _, severity, _ in rows):
        raise ValueError('Fatal DRC violation')
    return {rule: {'severity': severity, 'count': int(count)} for rule, severity, count in rows}


def ps_config(path):
    result = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        key, _, value = line.partition('\t')
        if key.startswith('CONFIG.'):
            result[key] = value
    if len(result) < 100:
        raise ValueError('PS configuration report unexpectedly short')
    return result


def hwh_review(path):
    tree = ET.parse(path).getroot()
    modules = {m.get('INSTANCE'): m for m in tree.findall('.//MODULE')}
    kernel = modules['ffn_accel_0']
    if kernel.get('VLNV') != 'xilinx.com:hls:ffn_accel:1.0':
        raise ValueError('Kernel VLNV mismatch')
    def prop(node, name):
        return int(next(p.get('VALUE') for p in node.findall('PROPERTY') if p.get('NAME') == name), 0)
    registers = {}
    for reg in kernel.findall('./ADDRESSBLOCKS/ADDRESSBLOCK/REGISTERS/REGISTER'):
        name = reg.get('NAME')
        if name in registers:
            raise ValueError('Duplicate register')
        registers[name] = {'address_offset': prop(reg, 'ADDRESS_OFFSET'), 'size': prop(reg, 'SIZE'),
                           'fields': {f.get('NAME'): {'bit_offset': prop(f, 'BIT_OFFSET'), 'bit_width': prop(f, 'BIT_WIDTH')}
                                      for f in reg.findall('./FIELDS/FIELD')}}
    validate_registers(registers)
    control = [n for n in tree.findall('.//MEMRANGE') if n.get('INSTANCE') == 'ffn_accel_0']
    if len(control) != 1 or int(control[0].get('BASEVALUE'), 0) != 0xA0000000:
        raise ValueError('Control base mismatch')
    buses = {}
    for bus in kernel.findall('./BUSINTERFACES/BUSINTERFACE'):
        if bus.get('NAME', '').startswith('m_axi_'):
            params = {p.get('NAME'): p.get('VALUE') for p in bus.findall('./PARAMETERS/PARAMETER') + bus.findall('./PARAMETER')}
            buses[bus.get('NAME')] = int(params['ADDR_WIDTH'], 0)
    if buses != {'m_axi_gmem0': 64, 'm_axi_gmem1': 64}:
        raise ValueError('Require exactly two 64-bit address m_axi masters')
    actual = {}
    for row in kernel.findall('./MEMORYMAP/MEMRANGE'):
        if row.get('MASTERBUSINTERFACE') in buses:
            key = row.get('MASTERBUSINTERFACE') + '/' + row.get('ADDRESSBLOCK')
            if key in actual:
                raise ValueError('Duplicate DDR segment')
            actual[key] = [int(row.get('BASEVALUE'), 0), int(row.get('HIGHVALUE'), 0)]
    expected = {bus + '/' + block: bounds for bus in buses for block, bounds in
                [('HP0_DDR_LOW', [0, 0x7fffffff]), ('HP0_DDR_HIGH', [0x800000000, 0xfffffffff])]}
    if actual != expected:
        raise ValueError('DDR mapping mismatch')
    if any('dma' in str(m.get('VLNV')).lower() for m in modules.values()):
        raise ValueError('Unexpected DMA IP')
    return {'registers': registers, 'control_base': '0xA0000000', 'master_addr_widths': buses,
            'ddr_segments_inclusive': actual, 'modules': {k: v.get('VLNV') for k, v in modules.items()},
            'dma_present': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('collected', type=Path)
    p.add_argument('old_system', type=Path)
    args = p.parse_args()
    root, old = args.collected.resolve(), args.old_system.resolve()
    collection = json.loads((root / 'bootstrap/collection_manifest.json').read_text(encoding='utf-8'))
    for name, expected in collection.items():
        pure = Path(name)
        if pure.is_absolute() or '..' in pure.parts or ':' in name or '\\' in name:
            raise ValueError('Unsafe manifest path')
        if sha(root / name) != expected:
            raise ValueError('Collection hash mismatch: ' + name)
    states = {}
    for stage in ('prepare', 'bd', 'synth', 'impl', 'export'):
        s = json.loads((root / 'records' / (stage + '.finished.json')).read_text(encoding='utf-8'))
        if s['status'] != '已验证' or s['exit_code'] != 0:
            raise ValueError('Incomplete/failed stage ' + stage)
        for name, digest in s['outputs'].items():
            if (root / name).is_file() and sha(root / name) != digest:
                raise ValueError('Changed completed-stage output: ' + name)
        states[stage] = s
    config = json.loads((root / 'config.json').read_text(encoding='utf-8'))
    if config['ip_sha256'] != '3e7eb615f7fc0ecf61ad5e56443eab6f4665e698045cef2565a4990e340e4185':
        raise ValueError('Wrong source IP')
    if sha(root / 'frozen_ip.zip') != config['ip_sha256']:
        raise ValueError('Wrong frozen IP copy')
    for name, digest in config['scripts'].items():
        if sha(root / name) != digest:
            raise ValueError('Script input changed')
        if name.endswith('.tcl') or name in ('extract_verify_hwh.py', 'abi_reference.py'):
            if sha(root / name) != sha(old / name):
                raise ValueError('Unexpected baseline topology/gate change ' + name)
    ev = root / 'evidence/vivado'
    gates = {prefix: {'timing': timing((ev / (prefix + '_timing_summary.rpt')).read_text()),
                      'route': route((ev / (prefix + '_route_status.rpt')).read_text()),
                      'drc': drc((ev / (prefix + '_drc.rpt')).read_text())}
             for prefix in ('implemented', 'pre_bitgen', 'post_bitgen')}
    new_ps, old_ps = ps_config(ev / 'ps_properties.txt'), ps_config(old / 'evidence/vivado/ps_properties.txt')
    ps_changes = {k: {'old': old_ps.get(k), 'new': new_ps.get(k)} for k in sorted(set(new_ps) | set(old_ps)) if new_ps.get(k) != old_ps.get(k)}
    if ps_changes:
        raise ValueError('PS preset/config changes: ' + json.dumps(ps_changes))
    bit = root / 'load/ffn_overlay_wrapper.bit'
    hwh = root / 'load/ffn_overlay_wrapper.hwh'
    xsa = root / 'traceability/ffn_overlay_wrapper.xsa'
    hw = hwh_review(hwh)
    old_hw = hwh_review(old / 'load/ffn_overlay_wrapper.hwh')
    if hw != old_hw:
        raise ValueError('HWH ABI/DDR/modules differ from baseline')
    with zipfile.ZipFile(xsa) as z:
        bits = [n for n in z.namelist() if n.endswith('.bit')]
        if len(bits) != 1 or hashlib.sha256(z.read(bits[0])).hexdigest() != sha(bit):
            raise ValueError('XSA embedded bit mismatch')
        if z.read('ffn_overlay.hwh') != hwh.read_bytes():
            raise ValueError('XSA embedded HWH mismatch')
    artifact = json.loads((root / 'traceability/artifact_verification.json').read_text(encoding='utf-8'))
    if artifact['bit_sha256'] != sha(bit) or artifact['hwh_sha256'] != sha(hwh) or artifact['xsa_sha256'] != sha(xsa):
        raise ValueError('Export record mismatch')
    files = {p.relative_to(HERE).as_posix(): sha(p) for p in root.rglob('*') if p.is_file()}
    report = {'schema': 'r04v2.system_build_review.v1', 'status': '已验证', 'scope': 'offline Vivado system build only; not board acceptance',
              'machine': '4090内网 192.168.110.248/JW', 'remote_root': 'D:/g4', 'part': 'xck26-sfvc784-2LV-c',
              'frozen_ip_sha256': config['ip_sha256'],
              'candidate_cpp_sha256': '553b9c9b36fee41d791908f1fc3b8314ea4631db403743f5e80665d18fad1350',
              'candidate_hpp_sha256': 'fcdced1362b460a6cea73fc5869564e6797c041469a50b9c56deba1253cd7478',
              'stage_records': states, 'gates': gates, 'hardware_metadata': hw,
              'ps_config_comparison': {'config_property_count': len(new_ps), 'changes': ps_changes,
                                       'source': 'r03 g3 versus r04 g4 ps_properties CONFIG.*'},
              'clock': {'design_period_ns': 10, 'design_mhz': 100, 'new_clocks': [], 'ps_pll_cpu_changes': [],
                        'board_clock_measurement': '未执行; coordinator owns board validation'},
              'same_export': {'bit_sha256': sha(bit), 'hwh_sha256': sha(hwh), 'xsa_sha256': sha(xsa),
                              'xsa_bit_equal_load_bit': True, 'xsa_hwh_equal_load_hwh': True},
              'evidence_files': files,
              'not_executed': ['KV260 connection by this worker', 'Overlay programming', 'clock/register writes',
                               'real PL numerical tests', 'board timing/performance', 'vehicle commands'],
              'notes': ['Timing WNS/WHS/TNS/THS and route reports independently parsed; board performance not implied.',
                        'DCP hashes retained in stage records but checkpoints not included in lightweight evidence collection.',
                        'Final r04v2.system_acceptance.v1 confirmation is reserved for coordinator review.']}
    with (HERE / 'system_acceptance_report.json').open('x', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps({'status': report['status'], 'gates': gates, 'same_export': report['same_export'], 'ps_changes': ps_changes}, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
