"""Collect r04 HLS evidence only; board programming is never invoked."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    files = {}
    stages = {}
    for label, root in [('v1', Path('D:/i4')), ('v2', Path('D:/i8')), ('v2_ooc', Path('D:/io8'))]:
        candidates = list(root.glob('*.cpp')) + list(root.glob('*.hpp')) + list(root.glob('*.json'))
        candidates += list((root / 'build').glob('*.cfg'))
        candidates += list((root / 'records_v1').glob('*'))
        work = root / 'work'
        candidates += list(work.glob('*.hls*summary'))
        candidates += list((work / 'logs').glob('*.log'))
        candidates += list((work / 'reports').glob('*.rpt'))
        candidates += list((work / 'hls/syn/report').glob('*.rpt'))
        candidates += list((work / 'hls/syn/report').glob('*.xml'))
        candidates += list((work / 'hls/impl/report').rglob('*.rpt'))
        candidates += list((work / 'hls/impl/report').rglob('*.xml'))
        candidates += list((work / 'hls/impl/verilog/report').glob('*.rpt'))
        candidates += [work / 'hls/syn/verilog/ffn_accel.v',
                       work / 'hls/syn/verilog/ffn_accel_control_s_axi.v',
                       work / 'hls/.autopilot/db/driver/src/xffn_accel_hw.h',
                       work / 'hls/sim/verilog/xsim.log',
                       work / 'hls/impl/ip/component.xml', work / 'ffn_accel.zip']
        for p in candidates:
            if p.is_file():
                name = label + '/' + p.relative_to(root).as_posix()
                files[name] = p
        for record in (root / 'records_v1').glob('*.finished.json'):
            stages[label + '/' + record.name] = json.loads(record.read_text(encoding='utf-8'))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    entries = {}
    with zipfile.ZipFile(args.output, 'x', zipfile.ZIP_DEFLATED) as archive:
        for name, path in sorted(files.items()):
            data = path.read_bytes()
            entries[name] = {'bytes': len(data), 'sha256': digest(data)}
            archive.writestr(name, data)
        archive.writestr('collection_manifest.json', json.dumps({'files': entries,
            'scope': 'HLS tool artifacts, source, reports and RTL only; no board result',
            'stage_records': stages}, indent=2))
    print(json.dumps({'archive': str(args.output), 'bytes': args.output.stat().st_size,
                      'sha256': digest(args.output.read_bytes()), 'files': len(entries)}, indent=2))


if __name__ == '__main__':
    main()
