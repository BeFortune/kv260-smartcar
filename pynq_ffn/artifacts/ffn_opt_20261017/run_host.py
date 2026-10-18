"""Run archived and candidate host builds with append-only command evidence."""
from pathlib import Path
import datetime
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
COMPILER = Path('D:/Programs/TDM-GCC-64/bin/g++.exe')


def run(name, command, cwd, env=None):
    log = OUT / (name + '.log')
    record = OUT / (name + '.command.json')
    if log.exists() or record.exists():
        raise FileExistsError(name)
    started = datetime.datetime.now().astimezone().isoformat()
    with log.open('x', encoding='utf-8') as stream:
        result = subprocess.run([str(item) for item in command], cwd=cwd, env=env,
                                stdout=stream, stderr=subprocess.STDOUT)
    record.write_text(json.dumps({'command': [str(item) for item in command],
                                 'cwd': str(cwd), 'started': started,
                                 'ended': datetime.datetime.now().astimezone().isoformat(),
                                 'exit_code': result.returncode, 'python': sys.version,
                                 'platform': platform.platform()}, indent=2), encoding='utf-8')
    print(name, 'exit=', result.returncode, 'log=', log, flush=True)
    if result.returncode:
        raise SystemExit(result.returncode)


def main():
    candidate = OUT / 'candidate' / 'hls'
    candidate.mkdir(parents=True, exist_ok=False)
    files = ['ffn_accel.cpp', 'ffn_accel.hpp', 'testbench.cpp', 'make_config.py',
             'build_hls.tcl', 'host_bridge.cpp']
    for name in files:
        shutil.copy2(ROOT / 'hls' / name, candidate / name)
    shutil.copy2(ROOT / 'tools/validate_cpp_parallel.py', candidate.parent / 'validate_cpp_parallel.py')
    manifest = {name: hashlib.sha256((candidate / name).read_bytes()).hexdigest() for name in files}
    (candidate.parent / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    run('compiler_version', [COMPILER, '--version'], ROOT)
    run('python_syntax', [sys.executable, '-c',
        "from pathlib import Path; import ast; [ast.parse(Path(p).read_text(encoding='utf-8')) for p in "
        + repr([str(ROOT / 'tools/validate_cpp_parallel.py'), str(candidate / 'make_config.py')]) + "]; print('Syntax passed')"], ROOT)
    env = os.environ.copy()
    env.update(FPGA_PART='xck26-sfvc784-2LV-c', CLOCK_NS='10')
    run('generate_config', [sys.executable, candidate / 'make_config.py'], candidate, env)
    config = (candidate / 'build/hls_config.cfg').read_text()
    for required in ('part=xck26-sfvc784-2LV-c', 'clock=10', 'syn.top=ffn_accel',
                     'syn.interface.m_axi_addr64=true', 'syn.file=../ffn_accel.cpp',
                     '-ffp-contract=off', 'tb.file=../testbench.cpp'):
        assert required in config, required
    (OUT / 'config_check.json').write_text(json.dumps({'status': 'pass',
        'amd_tool_execution': False, 'blocking_reason': 'No Vivado/Vitis/HLS installation on current host',
        'part': 'xck26-sfvc784-2LV-c', 'clock_ns': 10, 'output_lanes': 4,
        'requested_II': 1, 'actual_II': None, 'pipeline_math': 'Sequential FP32 per lane; exact erf GELU'}, indent=2), encoding='utf-8')
    flags = ['-std=c++11', '-O2', '-Wall', '-Wextra', '-Wpedantic', '-Wno-unknown-pragmas', '-ffp-contract=off']
    for label, source in [('baseline', OUT / 'baseline'), ('candidate', candidate)]:
        build = OUT / (label + '_host')
        build.mkdir(exist_ok=False)
        run(label + '_compile_testbench', [COMPILER] + flags + [source / 'ffn_accel.cpp',
            source / 'testbench.cpp', '-o', build / 'testbench.exe'], ROOT)
        run(label + '_testbench', [build / 'testbench.exe'], ROOT)
        run(label + '_compile_dll', [COMPILER] + flags + ['-shared', '-static-libgcc', '-static-libstdc++',
            source / 'ffn_accel.cpp', source / 'host_bridge.cpp', '-o', build / 'ffn_host.dll'], ROOT)
    run('bitwise_synthetic', [sys.executable, ROOT / 'tools/validate_cpp_parallel.py',
        '--baseline-library', OUT / 'baseline_host/ffn_host.dll',
        '--candidate-library', OUT / 'candidate_host/ffn_host.dll',
        '--output', OUT / 'bitwise_synthetic.json'], ROOT)


if __name__ == '__main__':
    main()
