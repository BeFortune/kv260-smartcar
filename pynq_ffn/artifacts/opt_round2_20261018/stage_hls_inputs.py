"""Unpack only the pinned candidate into a new tool-host directory."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile

ARCHIVE = Path('D:/opt2_inputs/interleaved_r04_hls_v1.zip')
DESTINATION = Path('D:/i4')
EXPECTED = 'e4c20cf9ed6c7c627767763651ecb83a3f80ffe360ed830b50a9e31e03f1c69a'


def main():
    if hashlib.sha256(ARCHIVE.read_bytes()).hexdigest() != EXPECTED:
        raise ValueError('Candidate archive mismatch')
    if DESTINATION.exists():
        raise FileExistsError('New D:/i4 already exists; not overwriting')
    with zipfile.ZipFile(ARCHIVE) as z:
        manifest = json.loads(z.read('input_manifest.json'))
        expected_names = set(manifest['files']) | {'input_manifest.json'}
        names = z.namelist()
        if set(names) != expected_names or len(names) != len(expected_names):
            raise ValueError('Unexpected or duplicate archive member')
        for name in names:
            p = PurePosixPath(name)
            if p.is_absolute() or '..' in p.parts or any(':' in s or chr(92) in s for s in p.parts):
                raise ValueError('Unsafe member')
        for name, expected in manifest['files'].items():
            data = z.read(name)
            if len(data) != expected['bytes'] or hashlib.sha256(data).hexdigest() != expected['sha256']:
                raise ValueError('Member hash mismatch: ' + name)
        DESTINATION.mkdir()
        for name in names:
            target = DESTINATION.joinpath(*PurePosixPath(name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as f:
                f.write(z.read(name))
    print(json.dumps({'status': '已验证', 'scope': 'source/config staging only; no HLS or hardware execution',
                      'directory': str(DESTINATION), 'archive_sha256': EXPECTED,
                      'members': len(names)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
