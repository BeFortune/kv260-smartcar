"""Run explicit HLS stages on the tool host; never program hardware."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

TOOLS = Path('D:/Learning/AMDDesignTools/2026.1/Vitis/bin')
TOOL_NAMES = {'csim': 'vitis-run.bat', 'csynth': 'v++.bat', 'cosim': 'vitis-run.bat', 'impl': 'vitis-run.bat'}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stamp():
    return datetime.now(timezone.utc).isoformat()


def record_new(path, data):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', required=True, choices=TOOL_NAMES)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--work-dir', required=True, type=Path)
    parser.add_argument('--record-dir', required=True, type=Path)
    args = parser.parse_args()
    if os.name != 'nt':
        raise RuntimeError('This runner uses the verified Windows tool installation')
    root = args.root.resolve(strict=True)
    config = args.config.resolve(strict=True)
    tool = TOOLS / TOOL_NAMES[args.stage]
    if not tool.is_file() or root not in config.parents:
        raise ValueError('Tool/config path invalid')
    evidence = args.record_dir.resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    output = evidence / (args.stage + '.finished.json')
    startfile = evidence / (args.stage + '.started.json')
    logfile = evidence / (args.stage + '.log')
    if any(p.exists() for p in (output, startfile, logfile)):
        raise FileExistsError('Stage already attempted; select a new record directory')
    if args.stage != 'csim' and not (args.work_dir / 'hls/syn').is_dir() and args.stage in ('cosim', 'impl'):
        raise RuntimeError('Synthesis output required before RTL/implementation')
    inputs = {p.relative_to(root).as_posix(): {'bytes': p.stat().st_size, 'sha256': sha(p)}
              for p in root.rglob('*') if p.is_file() and p.suffix in ('.cpp', '.hpp', '.cfg')
              and not any(part in ('.autopilot', 'work', 'records') for part in p.relative_to(root).parts)}
    if args.stage == 'csynth':
        argv = [str(tool), '--compile', '--mode', 'hls', '--config', str(config), '--work_dir', str(args.work_dir)]
    else:
        argv = [str(tool), '--mode', 'hls', '--config', str(config), '--work_dir', str(args.work_dir), '--' + args.stage]
    for arg in argv:
        if any(c in arg for c in ('"', '\n', '\r', '%', '&', '|', '<', '>', '^', '!')):
            raise ValueError('Unsupported shell character')
    command = '"' + os.environ.get('COMSPEC', 'C:/Windows/System32/cmd.exe') + '" /d /s /v:off /c "' + ' '.join('"' + arg + '"' for arg in argv) + '"'
    record = {'stage': args.stage, 'status': '未执行', 'started': stamp(), 'cwd': str(root),
              'command': command, 'arguments': argv, 'inputs': inputs, 'exit_code': None,
              'scope': 'HLS tool execution only; no board programming'}
    record_new(startfile, record)
    t0 = time.monotonic()
    code = None
    try:
        with logfile.open('xb') as log:
            process = subprocess.run(command, cwd=root, stdin=subprocess.DEVNULL,
                                     stdout=log, stderr=subprocess.STDOUT, shell=False)
        code = process.returncode
        record['exit_code'] = code
        record['status'] = '已验证' if code == 0 else '失败'
        if args.stage == 'csynth' and code == 0:
            xml = args.work_dir / 'hls/syn/report/ffn_accel_csynth.xml'
            if not xml.is_file():
                raise RuntimeError('Successful command lacks synthesized report')
            tree = ET.parse(xml)
            record['report_sha256'] = sha(xml)
            record['report_summary'] = {node.tag: node.text for node in tree.iter()
                if node.tag in ('EstimatedClockPeriod', 'TargetClockPeriod', 'Worst-caseLatency',
                                'Best-caseLatency', 'DSP', 'BRAM_18K', 'FF', 'LUT', 'URAM')}
    except BaseException as error:
        record['status'] = '失败'
        record['error'] = f'{type(error).__name__}: {error}'
        if code is None:
            record['note'] = 'Tool completion unknown; inspect before any retry'
        raise
    finally:
        record.update(finished=stamp(), elapsed_s=time.monotonic()-t0)
        record_new(output, record)
    print(json.dumps({k: record[k] for k in ('stage', 'status', 'exit_code', 'elapsed_s')}, ensure_ascii=False), flush=True)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
