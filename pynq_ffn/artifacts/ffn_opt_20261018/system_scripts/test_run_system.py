"""Standard-library-only offline mocks. Never starts Vivado or connects a device."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_system as run
import extract_verify_hwh as extract


def component(width=63):
    ns = run.NS['s']
    root = ET.Element('{' + ns + '}component')
    def sub(parent, name, text=None):
        node = ET.SubElement(parent, '{' + ns + '}' + name)
        node.text = text
        return node
    for key, value in zip(('vendor', 'library', 'name', 'version'), ('xilinx.com', 'hls', 'ffn_accel', '1.0')):
        sub(root, key, value)
    ports = sub(sub(root, 'model'), 'ports')
    for bundle in (0, 1):
        for channel in ('AR', 'AW'):
            port = sub(ports, 'port')
            sub(port, 'name', f'm_axi_gmem{bundle}_{channel}ADDR')
            vector = sub(sub(port, 'wire'), 'vector')
            sub(vector, 'left', str(width))
            sub(vector, 'right', '0')
    return ET.tostring(root)


def hwh():
    tree = ET.Element('SYSTEM')
    module = ET.SubElement(ET.SubElement(tree, 'MODULES'), 'MODULE', INSTANCE='ffn_accel_0', VLNV='xilinx.com:hls:ffn_accel:1.0')
    ET.SubElement(tree, 'MEMRANGE', INSTANCE='ffn_accel_0', BASEVALUE='0xa0000000')
    registers = ET.SubElement(ET.SubElement(ET.SubElement(module, 'ADDRESSBLOCKS'), 'ADDRESSBLOCK'), 'REGISTERS')
    def reg(name, offset, size):
        node = ET.SubElement(registers, 'REGISTER', NAME=name)
        for key, value in [('ADDRESS_OFFSET', offset), ('SIZE', size)]:
            ET.SubElement(node, 'PROPERTY', NAME=key, VALUE=str(value))
        return node
    fields = ET.SubElement(reg('CTRL', 0, 32), 'FIELDS')
    for name, bit in [('AP_START', 0), ('AP_DONE', 1), ('AP_IDLE', 2), ('AP_READY', 3), ('AUTO_RESTART', 7)]:
        f = ET.SubElement(fields, 'FIELD', NAME=name)
        ET.SubElement(f, 'PROPERTY', NAME='BIT_OFFSET', VALUE=str(bit))
        ET.SubElement(f, 'PROPERTY', NAME='BIT_WIDTH', VALUE='1')
    for name, offset in {'x': 0x10, 'y': 0x1c, 'w1': 0x28, 'b1': 0x34, 'w2': 0x40, 'b2': 0x4c}.items():
        reg(name, offset, 64)
    reg('num_tokens', 0x58, 32)
    buses = ET.SubElement(module, 'BUSINTERFACES')
    memory = ET.SubElement(module, 'MEMORYMAP')
    for bus in ('m_axi_gmem0', 'm_axi_gmem1'):
        params = ET.SubElement(ET.SubElement(buses, 'BUSINTERFACE', NAME=bus), 'PARAMETERS')
        ET.SubElement(params, 'PARAMETER', NAME='ADDR_WIDTH', VALUE='64')
        for block, base, high in [('HP0_DDR_LOW', '0', '0x7fffffff'), ('HP0_DDR_HIGH', '0x800000000', '0xfffffffff')]:
            ET.SubElement(memory, 'MEMRANGE', MASTERBUSINTERFACE=bus, ADDRESSBLOCK=block, BASEVALUE=base, HIGHVALUE=high)
    return ET.tostring(tree)


class OfflineTests(unittest.TestCase):
    def test_atomic_no_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'state.json'
            run.atomic_new(p, {'ok': True})
            with self.assertRaises(FileExistsError):
                run.atomic_new(p, {'ok': False})
            self.assertTrue(json.loads(p.read_text())['ok'])

    def test_safe_ip_and_width(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for label, width in [('good', 63), ('bad', 31)]:
                zpath = root / (label + '.zip')
                with zipfile.ZipFile(zpath, 'w') as z:
                    z.writestr('component.xml', component(width))
                if label == 'good':
                    info = run.extract_ip(zpath, root / label, run.digest(zpath))
                    self.assertEqual(len(info['address_ports']), 4)
                else:
                    with self.assertRaises(ValueError):
                        run.extract_ip(zpath, root / label, run.digest(zpath))
                    self.assertFalse((root / label).exists())

    def test_unsafe_ip_rejected(self):
        for name in ('../escape', 'C:/escape', 'AUX.txt'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as d:
                zpath = Path(d) / 'bad.zip'
                with zipfile.ZipFile(zpath, 'w') as z:
                    z.writestr('component.xml', component())
                    z.writestr(name, b'bad')
                with self.assertRaises(ValueError):
                    run.extract_ip(zpath, Path(d) / 'ip', run.digest(zpath))
                self.assertFalse((Path(d) / 'ip').exists())

    def test_cmd_quoting_rejects_metachar(self):
        c = run.windows_command('C:/AMD Tools/vivado.bat', 'D:/g3/a.tcl', None, 'D:/g3/a.log', 'D:/g3/a.jou')
        self.assertIn('/d /s /v:off /c ""C:/AMD Tools/vivado.bat"', c)
        with self.assertRaises(ValueError):
            run.windows_command('C:/a&whoami.bat', 'a', None, 'b', 'c')

    def test_actual_windows_batch_exit_code(self):
        if os.name != 'nt':
            self.skipTest('Windows cmd required')
        with tempfile.TemporaryDirectory(prefix='ffn space ') as d:
            root = Path(d)
            bat = root / 'mock vivado.bat'
            bat.write_text('@exit /b 23\n', encoding='ascii')
            command = run.windows_command(bat, root / 'script.tcl', root, root / 'a.log', root / 'a.jou')
            done = subprocess.run(command, shell=False, cwd=root, capture_output=True)
            self.assertEqual(done.returncode, 23)

    def test_stage_records_real_exit_no_retry(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'records').mkdir()
            run.atomic_new(root / 'config.json', {'vivado': run.VIVADO})
            with patch.object(run.subprocess, 'run', return_value=subprocess.CompletedProcess([], 17)):
                with self.assertRaises(RuntimeError):
                    run.execute_stage(root, 'bd', lambda _: None, 'fixed')
            state = json.loads((root / 'records/bd.finished.json').read_text(encoding='utf-8'))
            self.assertEqual(state['exit_code'], 17)
            self.assertEqual(state['status'], '失败')
            self.assertIsNotNone(state['end'])
            with self.assertRaises(FileExistsError):
                run.execute_stage(root, 'bd', lambda _: None, 'fixed')

    def test_success_hash_and_tamper(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'records').mkdir()
            def prepare(_):
                run.atomic_new(root / 'manifest.json', {})
                run.atomic_new(root / 'ip_check.json', {})
            run.execute_stage(root, 'prepare', prepare, 'fixed')
            state = json.loads((root / 'records/prepare.finished.json').read_text(encoding='utf-8'))
            run.verify_hashes(root, state['outputs'])
            (root / 'ip_check.json').write_text('changed')
            with self.assertRaises(RuntimeError):
                run.verify_hashes(root, state['outputs'])

    def test_export_same_run_and_subhwh_selection(self):
        for mismatch in (False, True):
            with self.subTest(mismatch=mismatch), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                bit = root / 'vivado03/ffn_overlay.runs/impl_1/ffn_overlay_wrapper.bit'
                bit.parent.mkdir(parents=True)
                bit.write_bytes(b'run bit')
                (root / 'traceability').mkdir()
                with zipfile.ZipFile(root / 'traceability/ffn_overlay_wrapper.xsa', 'w') as z:
                    z.writestr('ffn_overlay_wrapper.bit', b'other' if mismatch else b'run bit')
                    z.writestr('ffn_overlay.hwh', hwh())
                    z.writestr('memory_sc.hwh', '<SYSTEM/>')
                if mismatch:
                    with self.assertRaises(ValueError):
                        extract.verify(root)
                    self.assertFalse((root / 'load').exists())
                else:
                    result = extract.verify(root)
                    self.assertTrue(result['static_ps_validate_registers'])
                    self.assertEqual({p.suffix for p in (root / 'load').iterdir()}, {'.bit', '.hwh'})
                    self.assertNotIn('pynq', sys.modules)
                    with self.assertRaises(FileExistsError):
                        extract.verify(root)

    def test_tcl_static_balance_only(self):
        for path in HERE.glob('*.tcl'):
            text = path.read_text()
            self.assertEqual(text.count('{'), text.count('}'), path)
            self.assertNotIn('set_property SEVERITY', text)
            self.assertNotIn('file delete', text)
        # This is only a text check, NOT a Vivado parser/build or timing test.


if __name__ == '__main__':
    unittest.main(verbosity=2)
