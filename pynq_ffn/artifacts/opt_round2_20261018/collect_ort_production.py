"""Collect the completed direct Runtime CPU validation without touching hardware."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path('/home/ubuntu/opt_round2_20261018/ort')
RUN = ROOT / 'production_run01'
OUT = ROOT / 'production_run01_evidence.zip'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    if OUT.exists():
        raise FileExistsError(OUT)
    validation = json.loads((RUN / 'validation.json').read_text())
    if validation['status'] != '已验证' or validation['exit_code'] != 0 or not validation['comparison']['pass']:
        raise RuntimeError('Direct Runtime validation incomplete or failed')
    for name in ('golden4', 'split4_spinning_off'):
        result = json.loads((RUN / name / 'report.json').read_text())
        process = json.loads((RUN / name / 'process.json').read_text())
        if result['status'] != '已验证' or process['exit_code'] != 0 or result['n'] != 117:
            raise ValueError('Production worker did not finish all input frames')
    files = [p for p in RUN.rglob('*') if p.is_file() and p.suffix in ('.json', '.log', '.npz')]
    files += [ROOT / 'production_deployed_files.json', *list((ROOT / 'production_code').rglob('*.py'))]
    manifest = {}
    with zipfile.ZipFile(OUT, 'x', zipfile.ZIP_DEFLATED) as archive:
        for p in sorted(files):
            name = p.relative_to(ROOT).as_posix()
            data = p.read_bytes()
            manifest[name] = {'bytes': len(data), 'sha256': sha(data)}
            archive.writestr(name, data)
        archive.writestr('manifest.json', json.dumps({'files': manifest,
            'scope': 'CPU-only direct production Runtime; explicit spinning=False, golden default unchanged',
            'excluded_input': {'path': 'production_run01/inputs.npy',
                               'sha256': sha((RUN / 'inputs.npy').read_bytes()),
                               'reason': 'Decoded inputs can be recreated from the recorded source; full model and preprocessing remain unchanged'}}, indent=2))
    print(json.dumps({'archive': str(OUT), 'bytes': OUT.stat().st_size,
                      'sha256': sha(OUT.read_bytes()), 'files': len(manifest)}, indent=2))


if __name__ == '__main__':
    main()
