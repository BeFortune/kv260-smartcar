"""Stage this round's CPU-only ORT experiment into a new directory."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile

ROOT = Path('/home/ubuntu/opt_round2_20261018/ort')
SOURCE = ROOT / 'ort_thread_study_frozen.zip'
EXPECTED = 'bbaab463ea213ac0ebabbea1aea6ff8c26055b5cfa3f8f6651bd6c4504b392ca'


def main():
    if hashlib.sha256(SOURCE.read_bytes()).hexdigest() != EXPECTED:
        raise ValueError('CPU study archive mismatch')
    destination = ROOT / 'code'
    if destination.exists():
        raise FileExistsError('Refusing to overwrite CPU study')
    with zipfile.ZipFile(SOURCE) as z:
        names = z.namelist()
        if len(names) != len(set(names)):
            raise ValueError('Duplicate archive entries')
        for name in names:
            p = PurePosixPath(name)
            if p.is_absolute() or '..' in p.parts or any(':' in s or chr(92) in s for s in p.parts):
                raise ValueError('Unsafe path')
        destination.mkdir()
        hashes = {}
        for name in names:
            data = z.read(name)
            target = destination.joinpath(*PurePosixPath(name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as f:
                f.write(data)
            hashes[name] = hashlib.sha256(data).hexdigest()
    with (ROOT / 'deployed_files.json').open('x', encoding='utf-8') as f:
        json.dump({'archive_sha256': EXPECTED, 'files': hashes,
                   'scope': 'CPU-only; no PYNQ or device access'}, f, indent=2)
    print(json.dumps({'status': '已验证', 'directory': str(destination), 'files': hashes}, indent=2))


if __name__ == '__main__':
    main()
