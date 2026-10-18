# Static AST-extracted PS validator; no numpy/PYNQ imports or hardware access.
from typing import Mapping

POINTER_OFFSETS = {'x': 16, 'y': 28, 'w1': 40, 'b1': 52, 'w2': 64, 'b2': 76}
TOKEN_OFFSET = 88

def _number(value) -> int:
    return int(value, 0) if isinstance(value, str) else int(value)

def validate_registers(registers: Mapping) -> None:
    """Check exported HWH metadata, not a guessed register layout."""
    if not isinstance(registers, Mapping):
        raise ValueError("Pass overlay.ip_dict[ip_name]['registers'] from the matching HWH")
    control = registers.get('CTRL', registers.get('AP_CTRL'))
    if not isinstance(control, Mapping) or _number(control.get('address_offset', -1)) != 0 or _number(control.get('size', 0)) != 32:
        raise ValueError('Expected 32-bit ap_ctrl_hs CTRL register at 0x00')
    fields = control.get('fields')
    if not isinstance(fields, Mapping) or not fields:
        raise ValueError('HWH CTRL fields are required to verify ap_ctrl_hs')
    fields = {str(name).upper(): value for name, value in fields.items()}
    if 'AP_CONTINUE' in fields:
        raise ValueError('ap_ctrl_chain is not supported')
    for name, offset in {'AP_START': 0, 'AP_DONE': 1, 'AP_IDLE': 2, 'AP_READY': 3, 'AUTO_RESTART': 7}.items():
        field = fields.get(name)
        if not isinstance(field, Mapping) or _number(field.get('bit_offset', -1)) != offset or _number(field.get('bit_width', 0)) != 1:
            raise ValueError(f'HWH CTRL {name} must be one bit at offset {offset}')
    for name, offset in POINTER_OFFSETS.items():
        combined = registers.get(name)
        if isinstance(combined, Mapping):
            if _number(combined.get('address_offset', -1)) == offset and _number(combined.get('size', 0)) == 64:
                continue
        low, high = (registers.get(name + '_1'), registers.get(name + '_2'))
        if isinstance(low, Mapping) and isinstance(high, Mapping) and (_number(low.get('address_offset', -1)) == offset) and (_number(high.get('address_offset', -1)) == offset + 4) and (_number(low.get('size', 0)) == 32) and (_number(high.get('size', 0)) == 32):
            continue
        raise ValueError(f'HWH must describe the 64-bit {name} pointer at {offset:#x}')
    tokens = registers.get('num_tokens', {})
    if not isinstance(tokens, Mapping) or _number(tokens.get('address_offset', -1)) != TOKEN_OFFSET or _number(tokens.get('size', 0)) != 32:
        raise ValueError('HWH num_tokens must be a 32-bit register at 0x58')
