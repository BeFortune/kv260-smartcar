"""Print bounded HLS diagnostic excerpts; never execute hardware operations."""
from pathlib import Path
import hashlib
import json

ROOT = Path('D:/i4')
WORK = ROOT / 'work'
print('SYNTHESIS_WARNING_EXCERPTS')
log = (ROOT / 'records_v1/csynth.log').read_text(encoding='utf-8', errors='replace').splitlines()
seen = set()
for i, line in enumerate(log):
    if any(s in line.lower() for s in ('critical path', 'timing violation', 'unable to satisfy', 'bind_op', 'latency constraint', 'constraint violation')):
        for j in range(max(0, i-1), min(len(log), i+9)):
            if j not in seen:
                print(f'{j+1}:{log[j]}')
                seen.add(j)
print('OPERATOR_INSTANCE_COUNTS')
import re
rtl = (WORK / 'hls/syn/verilog/ffn_accel.v').read_text(encoding='utf-8')
for op in ('fadd', 'fmul'):
    matches = re.findall(r'^(' + op + r'_\S+)\s+(\w+)\s*\(', rtl, re.M)
    print(op, len(matches), matches)
print('SMALL_CRITICAL_REPORTS')
for name in ('ffn_accel_Pipeline_fc1_steps_csynth.rpt', 'ffn_accel_Pipeline_fc2_steps_csynth.rpt'):
    path = WORK / 'hls/syn/report' / name
    if path.is_file():
        lines = path.read_text().splitlines()
        print('FILE', str(path), 'SHA256', hashlib.sha256(path.read_bytes()).hexdigest())
        print('\n'.join(lines[:48]))
