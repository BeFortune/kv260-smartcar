"""Foreground bootstrap: no SSH/board/device operations, no old-root writes."""
from pathlib import Path
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
import zipfile

BASE = Path(__file__).resolve().parent
ROOT = Path('D:/g4')

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def save(path, data):
    with path.open('x', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def main():
    if ROOT.exists():
        raise FileExistsError('D:/g4 already exists; preserve and choose another new root')
    scripts = BASE / 'scripts'
    scripts.mkdir(exist_ok=False)
    with zipfile.ZipFile(BASE / 'scripts.zip') as z:
        for name in z.namelist():
            if Path(name).name != name or name.startswith('.'):
                raise ValueError('Unexpected script archive member')
            with (scripts / name).open('xb') as f:
                f.write(z.read(name))
    metadata = {'host': platform.node(), 'user': os.environ.get('USERNAME'), 'cwd': str(BASE),
                'python': sys.version, 'pid': os.getpid(), 'start_unix': time.time(), 'stages': []}
    save(BASE / 'bootstrap.started.json', metadata)
    exit_code = 1
    try:
        for label, command in [
            ('syntax', [sys.executable, '-m', 'compileall', '-q', str(scripts)]),
            ('offline_tests', [sys.executable, str(scripts / 'test_run_system.py')]),
            ('build', [sys.executable, '-u', str(scripts / 'run_system.py'), 'all', '--root', str(ROOT)])]:
            print('START ' + label, flush=True)
            with (BASE / (label + '.log')).open('xb') as stream:
                result = subprocess.run(command, cwd=BASE, stdin=subprocess.DEVNULL,
                                        stdout=stream, stderr=subprocess.STDOUT, shell=False)
            state = {'stage': label, 'command': command, 'exit_code': result.returncode,
                     'log': str(BASE / (label + '.log')), 'finished_unix': time.time()}
            metadata['stages'].append(state)
            save(BASE / (label + '.finished.json'), state)
            print('FINISH ' + label + ' exit=' + str(result.returncode), flush=True)
            if result.returncode:
                exit_code = result.returncode
                break
        else:
            exit_code = 0
    finally:
        metadata.update(exit_code=exit_code, end_unix=time.time())
        save(BASE / 'bootstrap.finished.json', metadata)
        if ROOT.exists():
            paths = []
            for directory in ('records', 'evidence', 'load', 'traceability'):
                paths.extend(p for p in (ROOT / directory).rglob('*') if p.is_file() and p.suffix != '.dcp')
            paths.extend(p for p in ROOT.iterdir() if p.is_file() and p.name != '.system.lock')
            table = {p.relative_to(ROOT).as_posix(): sha(p) for p in sorted(paths)}
            save(BASE / 'collection_manifest.json', table)
            with zipfile.ZipFile(BASE / 'system_evidence.zip', 'x', zipfile.ZIP_DEFLATED) as z:
                for p in sorted(paths):
                    z.write(p, p.relative_to(ROOT).as_posix())
                for p in BASE.iterdir():
                    if p.is_file() and p.name != 'system_evidence.zip':
                        z.write(p, 'bootstrap/' + p.name)
            print('BUNDLE ' + str(BASE / 'system_evidence.zip') + ' SHA256=' + sha(BASE / 'system_evidence.zip'), flush=True)
    return exit_code

if __name__ == '__main__':
    sys.exit(main())
