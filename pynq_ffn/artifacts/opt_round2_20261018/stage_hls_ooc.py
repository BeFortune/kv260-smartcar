"""Clone frozen r04 v2 source inputs into a separate implementation work root."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile

SOURCE = Path('D:/opt2_inputs/interleaved_r04_hls_v2.zip')
ROOT = Path('D:/io8')
EXPECTED = 'fc40b31a3a73fa838ac6d9be338be5a51f4881313353d50e6806b90ebb8c0ab5'


def main():
    if ROOT.exists():
        raise FileExistsError('New implementation root exists; not overwriting')
    if hashlib.sha256(SOURCE.read_bytes()).hexdigest() != EXPECTED:
        raise ValueError('Frozen r04 v2 ZIP changed')
    with zipfile.ZipFile(SOURCE) as archive:
        manifest = json.loads(archive.read('input_manifest.json'))
        if set(archive.namelist()) != set(manifest['files']) | {'input_manifest.json'}:
            raise ValueError('Unexpected source entries')
        for name, expected in manifest['files'].items():
            pure = PurePosixPath(name)
            if pure.is_absolute() or '..' in pure.parts or any(':' in p or chr(92) in p for p in pure.parts):
                raise ValueError('Unsafe archive entry')
            data = archive.read(name)
            if len(data) != expected['bytes'] or hashlib.sha256(data).hexdigest() != expected['sha256']:
                raise ValueError('Changed source: ' + name)
        ROOT.mkdir()
        for name in archive.namelist():
            target = ROOT.joinpath(*PurePosixPath(name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as stream:
                stream.write(archive.read(name))
    print(json.dumps({'source_archive_sha256': EXPECTED, 'root': str(ROOT),
                      'scope': 'separate source/work copy; board hardware untouched'}))


if __name__ == '__main__':
    main()
