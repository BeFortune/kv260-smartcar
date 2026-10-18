"""Offline-only Vivado system builder. No SSH, PYNQ, board access or old-IP fallback.

Windows example (run with C:/Python314/python.exe):
  run_system.py prepare
  run_system.py bd --resume
  run_system.py synth --resume
  run_system.py all --resume
Use Windows `start "ffn_system" /b ... > detached.log 2>&1` to detach externally.
An existing build is never adopted; failed/interrupted stages require a NEW root.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET
import zipfile

HERE = Path(__file__).resolve().parent
IP = 'D:/f3/hls/build/ffn_accel_modern/ffn_accel.zip'
IP_SHA = 'de24cf176e36e8ddf8aea03db02faa9b218133e799cd8ba0289c722a29c5e56b'
VIVADO = 'D:/Learning/AMDDesignTools/2026.1/Vivado/bin/vivado.bat'
FILES = ('run_system.py', 'create_overlay_bd.tcl', 'run_overlay_synth.tcl',
         'run_overlay_impl.tcl', 'finalize_bit_xsa.tcl', 'gate_overlay.tcl',
         'extract_verify_hwh.py', 'abi_reference.py', 'stage_entry.tcl')
STAGES = ('prepare', 'bd', 'synth', 'impl', 'export')
TCL = dict(bd='create_overlay_bd.tcl', synth='run_overlay_synth.tcl',
           impl='run_overlay_impl.tcl', export='finalize_bit_xsa.tcl')
NS = {'s': 'http://www.spiritconsortium.org/XMLSchema/SPIRIT/1685-2009'}


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def atomic_new(path, value):
    """Publish complete JSON atomically, never replace a prior record."""
    path = Path(path)
    temp = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('x', encoding='utf-8') as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.link(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def hashes(paths, root):
    return {str(p.relative_to(root)).replace('\\', '/'): digest(p) for p in sorted(paths) if p.is_file()}


def verify_hashes(root, table):
    for name, expected in table.items():
        path = root / name
        if not path.is_file() or digest(path) != expected:
            raise RuntimeError(f'Changed/missing immutable input or completed output: {path}')


def extract_ip(source, destination, expected_sha):
    if digest(source) != expected_sha:
        raise ValueError('r03 source ZIP SHA256 mismatch; no fallback permitted')
    with zipfile.ZipFile(source) as z:
        components = [i for i in z.infolist() if PurePosixPath(i.filename).name == 'component.xml']
        if len(components) != 1:
            raise ValueError('Require exactly one component.xml')
        component = components[0]
        tree = ET.fromstring(z.read(component))
        identity = [tree.findtext('s:' + n, namespaces=NS) for n in ('vendor', 'library', 'name', 'version')]
        if identity != ['xilinx.com', 'hls', 'ffn_accel', '1.0']:
            raise ValueError(f'Unexpected IP identity {identity}')
        ports = {}
        wanted = {f'm_axi_gmem{n}_{c}ADDR' for n in (0, 1) for c in ('AR', 'AW')}
        for port in tree.findall('s:model/s:ports/s:port', NS):
            name = port.findtext('s:name', namespaces=NS)
            if name in wanted:
                if name in ports:
                    raise ValueError('Duplicate address port')
                left = int(port.findtext('s:wire/s:vector/s:left', namespaces=NS))
                right = int(port.findtext('s:wire/s:vector/s:right', namespaces=NS))
                if (left, right) != (63, 0):
                    raise ValueError(f'Address port not [63:0]: {name}')
                ports[name] = 64
        if set(ports) != wanted:
            raise ValueError('Missing one of the four 64-bit address ports')
        prefix = str(PurePosixPath(component.filename).parent)
        prefix = '' if prefix == '.' else prefix + '/'
        entries, seen = [], set()
        for info in z.infolist():
            name = info.filename
            relative = PurePosixPath(name)
            if (relative.is_absolute() or '..' in relative.parts or '\\' in name
                    or any(':' in p or p.rstrip(' .') != p or p.split('.')[0].upper() in
                           {'CON', 'PRN', 'AUX', 'NUL', *['COM' + str(i) for i in range(1, 10)],
                            *['LPT' + str(i) for i in range(1, 10)]} for p in relative.parts)
                    or ((info.external_attr >> 16) & 0o170000) == 0o120000):
                raise ValueError(f'Unsafe ZIP member {name}')
            if not name.startswith(prefix):
                raise ValueError('Unexpected content outside component root')
            relative = PurePosixPath(name[len(prefix):])
            key = str(relative).casefold()
            if key == '.':
                continue
            if key in seen:
                raise ValueError('Duplicate/case-colliding archive member')
            seen.add(key)
            entries.append((info, relative))
        destination.mkdir(exist_ok=False)
        for info, relative in entries:
            target = destination.joinpath(*relative.parts)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open('xb') as f:
                    f.write(z.read(info))
    return {'identity': identity, 'address_ports': ports}


def windows_command(vivado, script, root, log, journal):
    """Use explicit cmd /s /c outer quote, all tokens quoted, shell metachars rejected."""
    cmd = os.environ.get('COMSPEC', 'C:/Windows/System32/cmd.exe')
    args = [str(vivado), '-mode', 'batch', '-notrace', '-source', str(Path(root) / 'stage_entry.tcl') if root is not None else str(script),
            '-log', str(log), '-journal', str(journal)]
    if root is not None:
        args.extend(['-tclargs', str(script)])
    for token in [cmd, *args]:
        if not re.fullmatch(r'[a-zA-Z0-9_~ .:/\\=\-]+', token):
            raise ValueError('Unsupported shell character in command path/argument')
    return '"' + cmd + '" /d /s /v:off /c "' + ' '.join('"' + s + '"' for s in args) + '"'


@contextmanager
def locked(root):
    lock = root / '.system.lock'
    with lock.open('x', encoding='utf-8') as f:
        f.write(str(os.getpid()))
    try:
        yield
    finally:
        lock.unlink()


def outputs(root, stage):
    ev = root / 'evidence/vivado'
    if stage == 'prepare':
        return [root / 'manifest.json', root / 'ip_check.json']
    if stage == 'bd':
        return list(ev.glob('*.txt')) + [ev / 'ffn_overlay_bd.tcl']
    if stage == 'synth':
        return [ev / 'post_synth_utilization.rpt', ev / 'post_synth_timing.rpt',
                root / 'vivado03/ffn_overlay.runs/synth_1/ffn_overlay_wrapper.dcp']
    if stage == 'impl':
        return [ev / 'implemented_gate.txt', ev / 'implemented_route_status.rpt',
                ev / 'implemented_timing_summary.rpt', ev / 'implemented_drc.rpt',
                ev / 'ffn_overlay_wrapper_routed.dcp']
    return [root / p for p in ('traceability/ffn_overlay_wrapper.xsa', 'traceability/artifact_verification.json',
                               'load/ffn_overlay_wrapper.bit', 'load/ffn_overlay_wrapper.hwh',
                               'evidence/vivado/pre_bitgen_gate.txt', 'evidence/vivado/post_bitgen_gate.txt')]


def execute_stage(root, stage, action, input_hash):
    record_dir = root / 'records'
    start = record_dir / (stage + '.started.json')
    finish = record_dir / (stage + '.finished.json')
    if start.exists() or finish.exists():
        raise FileExistsError(f'Stage already attempted: {stage}; no overwrite/retry')
    log = record_dir / (stage + '.console.log')
    record = {'stage': stage, 'cwd': str(root), 'start': now(), 'end': None,
              'status': '未执行', 'exit_code': None, 'input_sha256': input_hash, 'log': str(log)}
    if stage in TCL:
        record['command'] = windows_command(root_config(root)['vivado'], root / TCL[stage], root,
                                            record_dir / (stage + '.vivado.log'), record_dir / (stage + '.vivado.jou'))
    else:
        record['command'] = [sys.executable, str(HERE / 'run_system.py'), 'prepare']
    atomic_new(start, record)
    actual_exit = None
    try:
        with log.open('x', encoding='utf-8') as stream:
            if stage in TCL:
                completed = subprocess.run(record['command'], cwd=str(root), stdin=subprocess.DEVNULL,
                                           stdout=stream, stderr=subprocess.STDOUT, shell=False)
                actual_exit = completed.returncode
                record['exit_code'] = actual_exit
                if actual_exit != 0:
                    raise RuntimeError(f'Vivado {stage} exit code {actual_exit}; see {log}')
            action(stream)
        required = outputs(root, stage)
        for path in required:
            if not path.is_file():
                raise FileNotFoundError(path)
        record.update(status='已验证', exit_code=0, outputs=hashes(required, root))
    except BaseException as e:
        record.update(status='失败', error=f'{type(e).__name__}: {e}', exit_code=actual_exit,
                      validation_exit_code=1)
        raise
    finally:
        record['end'] = now()
        atomic_new(finish, record)


def root_config(root):
    return json.loads((root / 'config.json').read_text(encoding='utf-8'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=(*STAGES, 'all'))
    parser.add_argument('--root', default='D:/g3', help='NEW short build root; never adopt an existing directory')
    parser.add_argument('--vivado', default=VIVADO, help='Full vivado.bat path')
    parser.add_argument('--ip-sha256', default=IP_SHA, choices=[IP_SHA], help='Pinned r03 ZIP SHA256 (cannot bypass)')
    parser.add_argument('--resume', action='store_true', help='Skip only successful hash-identical stages; never retry failure')
    args = parser.parse_args(argv)
    if os.name != 'nt':
        raise RuntimeError('This driver targets Windows cmd/Vivado only')
    root, vivado, source = Path(args.root).resolve(), Path(args.vivado).resolve(), Path(IP)
    if not vivado.is_file() or vivado.name.lower() != 'vivado.bat' or not source.is_file():
        raise FileNotFoundError('Full Vivado path and fixed r03 IP ZIP required')
    if digest(source) != IP_SHA or source.stat().st_size != 351343:
        raise ValueError('Pinned r03 source ZIP hash/size mismatch')
    source_hashes = {name: digest(HERE / name) for name in FILES}
    config = {'root': str(root), 'vivado': str(vivado), 'vivado_bat_sha256': digest(vivado), 'ip': IP,
              'ip_sha256': IP_SHA, 'scripts': source_hashes}
    config_hash = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    if root.exists():
        if not args.resume or not (root / 'config.json').is_file() or root_config(root) != config:
            raise FileExistsError('Existing root requires --resume and identical driver/config/input hashes')
    else:
        if args.stage not in ('prepare', 'all'):
            raise RuntimeError('Run prepare first')
        root.mkdir(exist_ok=False)
        (root / 'records').mkdir()
        atomic_new(root / 'config.json', config)
    with locked(root):
        if (root / 'manifest.json').is_file():
            verify_hashes(root, json.loads((root / 'manifest.json').read_text(encoding='utf-8')))
        selected = STAGES if args.stage == 'all' else (args.stage,)
        for stage in selected:
            previous = STAGES[:STAGES.index(stage)]
            for dependency in previous:
                f = root / 'records' / (dependency + '.finished.json')
                if not f.is_file():
                    raise RuntimeError(f'{stage} requires completed {dependency}')
                state = json.loads(f.read_text(encoding='utf-8'))
                if state['status'] != '已验证' or state['input_sha256'] != config_hash:
                    raise RuntimeError(f'Failed/incompatible dependency {dependency}')
                verify_hashes(root, state['outputs'])
            finished = root / 'records' / (stage + '.finished.json')
            if finished.exists():
                state = json.loads(finished.read_text(encoding='utf-8'))
                if not args.resume or state['status'] != '已验证' or state['input_sha256'] != config_hash:
                    raise RuntimeError(f'Cannot resume {stage}: not a hash-identical success')
                verify_hashes(root, state['outputs'])
                print(stage + ': resume verified', flush=True)
                continue
            def action(stream, stage=stage):
                if stage == 'prepare':
                    for name in FILES:
                        with (root / name).open('xb') as out:
                            out.write((HERE / name).read_bytes())
                    info = extract_ip(source, root / 'ip_repo', IP_SHA)
                    atomic_new(root / 'ip_check.json', {**info, 'source': IP, 'sha256': IP_SHA})
                    manifest = hashes([root / n for n in FILES] + list((root / 'ip_repo').rglob('*')), root)
                    atomic_new(root / 'manifest.json', manifest)
                    stream.write('Pinned r03 IP safely staged; no Vivado/board access in prepare.\n')
                elif stage == 'export':
                    from extract_verify_hwh import verify
                    result = verify(root)
                    stream.write(json.dumps(result, ensure_ascii=False) + '\n')
            execute_stage(root, stage, action, config_hash)
            print(stage + ': 已验证 (offline system build only)', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'FAILED: {type(exc).__name__}: {exc}', file=sys.stderr, flush=True)
        sys.exit(1)
