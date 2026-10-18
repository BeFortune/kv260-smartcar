"""Stage the frozen production spin opt-in into a new CPU-only code directory."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile

ROOT = Path('/home/ubuntu/opt_round2_20261018/ort')
SOURCE = ROOT / 'runtime_spin_optin_frozen.zip'
EXPECTED = 'e07319c0fbe792dfa4b37ba86dc8b44e5e04e427485df9275b71992a641d7c42'


def main():
    if hashlib.sha256(SOURCE.read_bytes()).hexdigest() != EXPECTED:
        raise ValueError('Production CPU code archive mismatch')
    destination = ROOT / 'production_code'
    if destination.exists():
        raise FileExistsError('Refusing to overwrite production CPU validation code')
    with zipfile.ZipFile(SOURCE) as z:
        names = z.namelist()
        if len(names) != len(set(names)):
            raise ValueError('Duplicate archive entries')
        for name in names:
            p = PurePosixPath(name)
            if p.is_absolute() or '..' in p.parts or any(':' in part or chr(92) in part for part in p.parts):
                raise ValueError('Unsafe archive member')
        runtime_data = z.read('ps/runtime.py')
        if hashlib.sha256(runtime_data).hexdigest() != '49ace8fbf61a29e2e54efc174b45477cd08fc5fe469d13547df17326b9745576':
            raise ValueError('Unexpected production Runtime revision')
        destination.mkdir()
        files = {}
        for name in names:
            data = z.read(name)
            target = destination.joinpath(*PurePosixPath(name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as stream:
                stream.write(data)
            files[name] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    with (ROOT / 'production_deployed_files.json').open('x', encoding='utf-8') as stream:
        json.dump({'archive_sha256': EXPECTED, 'files': files,
                   'scope': 'CPU-only Runtime validation; no PYNQ or hardware state changes'}, stream, indent=2)
    print(json.dumps({'status': '已验证', 'directory': str(destination), 'files': files}, indent=2))


if __name__ == '__main__':
    main()
