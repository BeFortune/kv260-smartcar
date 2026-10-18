"""Print bounded r04 synthesis facts; never load or program FPGA hardware."""
from pathlib import Path
import collections
import json
import re
import sys
import xml.etree.ElementTree as ET

ROOT = Path(sys.argv[1])
WORK = ROOT / 'work'

report = WORK / 'hls/syn/report/ffn_accel_csynth.xml'
tree = ET.parse(report)
print('SUMMARY_XML')
print(json.dumps({n.tag: n.text for n in tree.iter() if n.tag in (
    'EstimatedClockPeriod', 'TargetClockPeriod', 'Worst-caseLatency', 'Best-caseLatency',
    'DSP', 'BRAM_18K', 'FF', 'LUT', 'URAM')}, indent=2))
print('MAIN_OPERATOR_INSTANCES')
rtl = (WORK / 'hls/syn/verilog/ffn_accel.v').read_text()
operators = re.findall(r'^\s*(f(?:add|mul)_\S+)\s+(\w+)\s*\(', rtl, re.M)
print(json.dumps({'instances': operators, 'counts': dict(collections.Counter(n.split('_')[0] for n, _ in operators))}, indent=2))
print('LOOP_MODULE_SUMMARY')
for kind in ('fc1', 'fc2'):
    p = WORK / f'hls/syn/report/ffn_accel_Pipeline_{kind}_steps_csynth.rpt'
    lines = p.read_text().splitlines()
    print('FILE', p.name)
    for line in lines:
        if any(s in line for s in ('|ap_clk', f'|- {kind}_steps', 'full_dsp', 'max_dsp')):
            print(line)
print('HLS_LOG_FINAL')
print('\n'.join((ROOT / 'records_v1/csynth.log').read_text(errors='replace').splitlines()[-7:]))
print('REGISTER_CONTRACT')
top = WORK / 'hls/syn/verilog/ffn_accel.v'
for line in top.read_text().splitlines():
    if 'C_M_AXI_GMEM' in line and 'parameter' in line:
        print(line)
control = (WORK / 'hls/syn/verilog/ffn_accel_control_s_axi.v').read_text()
for line in control.splitlines():
    if ('Protocol Used' in line or re.search(r'ADDR_(AP_CTRL|X_DATA|Y_DATA|W1_DATA|B1_DATA|W2_DATA|B2_DATA|NUM_TOKENS_DATA)', line)):
        print(line)
