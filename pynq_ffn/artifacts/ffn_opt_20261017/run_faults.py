"""Fault injection of the new validator; no device I/O or source mutation."""
from pathlib import Path
import hashlib
import importlib.util
import json
import subprocess
import sys
import warnings
import zipfile
from run_host import ROOT, OUT

PYTHON = Path('C:/Users/BeFortuneEX/.zcode/tmp/ffn_opt_20261017/venv/Scripts/python.exe')
validator = ROOT / 'tools/validate_cpp_parallel.py'
fixtures = OUT / 'fault_injection_r02'
fixtures.mkdir(exist_ok=False)

if len(sys.argv) > 1:
    raise RuntimeError('This script has no arguments')

cases = []

def check(name, command, expected_exit, expected_text):
    result = subprocess.run([str(item) for item in command], cwd=ROOT, capture_output=True, text=True)
    (fixtures / (name + '.stdout.log')).write_text(result.stdout, encoding='utf-8')
    (fixtures / (name + '.stderr.log')).write_text(result.stderr, encoding='utf-8')
    passed = result.returncode == expected_exit and expected_text in result.stdout + result.stderr
    cases.append({'name': name, 'command': [str(item) for item in command], 'cwd': str(ROOT),
                  'exit_code': result.returncode, 'expected_exit': expected_exit,
                  'expected_text': expected_text, 'pass': passed})

existing = OUT / 'r02_bitwise_synthetic.json'
before = hashlib.sha256(existing.read_bytes()).hexdigest()
check('refuse_report_overwrite', [PYTHON, validator, '--baseline-library', OUT / 'baseline_host/ffn_host.dll',
    '--candidate-library', OUT / 'candidate_r02_host/ffn_host.dll', '--output', existing], 1, 'FileExistsError')
assert hashlib.sha256(existing.read_bytes()).hexdigest() == before

load = ('import importlib.util; from pathlib import Path; import sys; '
        'sys.path.insert(0, ' + repr(str(ROOT / 'tools')) + '); '
        's=importlib.util.spec_from_file_location("parallel", ' + repr(str(validator)) + '); '
        'm=importlib.util.module_from_spec(s); s.loader.exec_module(m); ')
nonfinite_report = fixtures / 'nonfinite_result.json'
code = load + '''
class Nonfinite:
    def __init__(self, path): pass
    def forward(self, name, x, weights):
        return m.np.full_like(x, m.np.nan)
m.HostFFN = Nonfinite
sys.argv = [str(m.__file__), '--baseline-library', BASE, '--candidate-library', CAND, '--output', REPORT]
raise SystemExit(m.main())
'''.replace('BASE', repr(str(OUT / 'baseline_host/ffn_host.dll'))).replace('CAND', repr(str(OUT / 'candidate_r02_host/ffn_host.dll'))).replace('REPORT', repr(str(nonfinite_report)))
check('nonfinite_result', [PYTHON, '-c', code], 1, 'Nonfinite baseline or candidate output')
r = json.loads(nonfinite_report.read_text())
assert r['status'] == 'failed' and 'Nonfinite' in r['error']

bundle = ROOT / 'artifacts/bundle_20261014'
model = (bundle / 'model.onnx').read_bytes()
model_sha = hashlib.sha256(model).hexdigest()
manifest = {'model_sha256': model_sha, 'recordings': [{'segment': '1', 'frames': 1}]}
for name, error in [('duplicate_member', 'Duplicate archive members'),
                    ('empty_recordings', 'Empty recording manifest'),
                    ('duplicate_recording', 'Duplicate recording in manifest'),
                    ('zero_frame', 'positive frame count'),
                    ('bad_model_hash', 'Actual payload model differs'),
                    ('escape_csv_path', 'escapes')]:
    path = fixtures / (name + '.zip')
    current = json.loads(json.dumps(manifest))
    if name == 'empty_recordings': current['recordings'] = []
    if name == 'duplicate_recording': current['recordings'] *= 2
    if name == 'zero_frame': current['recordings'][0]['frames'] = 0
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        with zipfile.ZipFile(path, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('payload_manifest.json', json.dumps(current))
            archive.writestr('model/model_current.onnx', b'corrupt' if name == 'bad_model_hash' else model)
            if name == 'duplicate_member': archive.writestr('payload_manifest.json', '{}')
            if name == 'escape_csv_path':
                archive.writestr('data/1/auto_data.csv', '../front.jpeg,../left.jpeg,../right.jpeg,0,0\n')
    code = load + ('m.payload_replay(None, None, Path(' + repr(str(bundle)) + '), Path(' + repr(str(path)) + '))')
    check(name, [PYTHON, '-c', code], 1, error)

(fixtures / 'results.json').write_text(json.dumps({'status': 'pass' if all(c['pass'] for c in cases) else 'failed',
    'cases': cases, 'report_overwrite_hash_unchanged': before == hashlib.sha256(existing.read_bytes()).hexdigest()}, indent=2), encoding='utf-8')
print(json.dumps({'passed': sum(c['pass'] for c in cases), 'total': len(cases)}))
raise SystemExit(0 if all(c['pass'] for c in cases) else 1)
