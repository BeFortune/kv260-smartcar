"""Collect completed CPU study and profiles, not large decoded input arrays."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path('/home/ubuntu/opt_round2_20261018/ort')
RUN = ROOT / 'run01'
OUT = ROOT / 'run01_evidence.zip'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    if OUT.exists():
        raise FileExistsError(OUT)
    study = json.loads((RUN / 'study.json').read_text())
    if study['status'] != '已验证' or study['exit_code'] != 0:
        raise RuntimeError('Study not successful')
    reports = list(RUN.glob('perf_*/report.json'))
    if len(reports) != 12:
        raise ValueError('Require both orders of all six formal conditions')
    for report in reports:
        d = json.loads(report.read_text())
        if d['n'] != 117 or d['profiling'] or not d['comparison']['pass']:
            raise ValueError('Formal condition failed validation')
    files = [p for p in RUN.rglob('*') if p.is_file() and p.suffix in ('.json', '.log', '.npz')]
    files += [ROOT / 'deployed_files.json', *list((ROOT / 'code').rglob('*.py'))]
    entries = {}
    with zipfile.ZipFile(OUT, 'x', zipfile.ZIP_DEFLATED) as z:
        for p in sorted(files):
            data = p.read_bytes()
            name = p.relative_to(ROOT).as_posix()
            entries[name] = {'sha256': sha(data), 'bytes': len(data)}
            z.writestr(name, data)
        z.writestr('manifest.json', json.dumps({'files': entries,
            'scope': 'CPU-only ORT thread experiment; preloaded 117 frames; profiles measured separately',
            'excluded_input': {'path': 'run01/inputs.npy', 'sha256': sha((RUN / 'inputs.npy').read_bytes()),
                               'reason': 'rebuild via inputs.json original image hashes and unchanged preprocessing'}}, indent=2))
    print(json.dumps({'path': str(OUT), 'bytes': OUT.stat().st_size,
                      'sha256': sha(OUT.read_bytes()), 'files': len(entries)}, indent=2))


if __name__ == '__main__':
    main()
