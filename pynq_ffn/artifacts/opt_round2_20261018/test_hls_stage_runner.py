"""Local Windows command-line validation only; no AMD tool invocation."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile

runner = Path(__file__).with_name('run_hls_stage.py')
compile(runner.read_text(encoding='utf-8'), str(runner), 'exec')
with tempfile.TemporaryDirectory(prefix='hls_runner_test_') as temp:
    root = Path(temp) / 'with space'
    root.mkdir()
    tool = root / 'exit test.bat'
    tool.write_text('@exit /b 23\n', encoding='ascii')
    args = [str(tool), '--mode', 'hls']
    invocation = '"' + os.environ.get('COMSPEC', r'C:\Windows\System32\cmd.exe') + '" /d /s /v:off /c "' + ' '.join('"' + arg + '"' for arg in args) + '"'
    result = subprocess.run(invocation, cwd=root, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=False)
    print(json.dumps({'invocation': invocation, 'exit_code': result.returncode,
                      'stdout': result.stdout.decode('utf-8', errors='replace')}, ensure_ascii=False))
    assert result.returncode == 23
print('syntax and real batch exit-code propagation passed; HLS not invoked')
