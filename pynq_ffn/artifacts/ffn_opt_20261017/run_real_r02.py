"""Execute real-weight validation with the approved private environment."""
from run_host import ROOT, OUT, run

PYTHON = 'C:/Users/BeFortuneEX/.zcode/tmp/ffn_opt_20261017/venv/Scripts/python.exe'
library = OUT / 'candidate_r02_host/ffn_host.dll'
bundle = ROOT / 'artifacts/bundle_20261014'
run('r02_validate_cpp_real', [PYTHON, ROOT / 'tools/validate_cpp.py', '--bundle', bundle,
    '--library', library, '--recording', ROOT / 'artifacts/sample_recording_1779374497',
    '--random-cases', '3', '--output', OUT / 'r02_validate_cpp_real.json'], ROOT)
run('r02_payload_173', [PYTHON, ROOT / 'tools/validate_cpp_parallel.py',
    '--baseline-library', OUT / 'baseline_host/ffn_host.dll', '--candidate-library', library,
    '--bundle', bundle, '--zip', ROOT.parent / 'kv260_v1/v1_payload.zip',
    '--output', OUT / 'r02_payload_173.json'], ROOT)
