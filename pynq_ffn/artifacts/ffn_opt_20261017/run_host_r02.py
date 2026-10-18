"""Freeze the reviewed four-lane candidate without overwriting attempt01."""
from pathlib import Path
import ast
import hashlib
import json
import os
import shutil
import sys
from run_host import ROOT, OUT, COMPILER, run

candidate = OUT / 'candidate_r02' / 'hls'
candidate.mkdir(parents=True, exist_ok=False)
for name in ('ffn_accel.cpp', 'ffn_accel.hpp', 'testbench.cpp', 'make_config.py', 'build_hls.tcl', 'host_bridge.cpp'):
    shutil.copy2(ROOT / 'hls' / name, candidate / name)
validator = ROOT / 'tools/validate_cpp_parallel.py'
shutil.copy2(validator, candidate.parent / validator.name)
manifest = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in candidate.iterdir() if path.is_file()}
manifest[validator.name] = hashlib.sha256(validator.read_bytes()).hexdigest()
(candidate.parent / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
run('r02_python_syntax', [sys.executable, '-c',
    'import ast; from pathlib import Path; ast.parse(Path(' + repr(str(validator)) + ').read_text()); print("Syntax passed")'], ROOT)
env = os.environ.copy()
env.update(FPGA_PART='xck26-sfvc784-2LV-c', CLOCK_NS='10')
run('r02_generate_config', [sys.executable, candidate / 'make_config.py'], candidate, env)
flags = ['-std=c++11', '-O2', '-Wall', '-Wextra', '-Wpedantic', '-Wno-unknown-pragmas', '-ffp-contract=off']
build = OUT / 'candidate_r02_host'
build.mkdir(exist_ok=False)
run('r02_compile_testbench', [COMPILER] + flags + [candidate / 'ffn_accel.cpp', candidate / 'testbench.cpp', '-o', build / 'testbench.exe'], ROOT)
run('r02_testbench', [build / 'testbench.exe'], ROOT)
run('r02_compile_dll', [COMPILER] + flags + ['-shared', '-static-libgcc', '-static-libstdc++', candidate / 'ffn_accel.cpp', candidate / 'host_bridge.cpp', '-o', build / 'ffn_host.dll'], ROOT)
run('r02_bitwise_synthetic', [sys.executable, validator, '--baseline-library', OUT / 'baseline_host/ffn_host.dll', '--candidate-library', build / 'ffn_host.dll', '--output', OUT / 'r02_bitwise_synthetic.json'], ROOT)
