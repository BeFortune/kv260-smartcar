"""NUC -> STM32 的 21 字节速度帧。只编码字节，不打开串口。

和旧 NUC `SerialSender::SendSideSpeed` 逐字节一致：
  '%' '@' '1' + 左 3 位 + '1' + 左 3 位 + '1' + 右 3 位 + '1' + 右 3 位 + '1' + 0xFF 0xFF
速度用 C++ int(float) 的截断（向零取整）。

旧代码对负数和超过 999 会发出错位的帧（setw(3) 不是限幅）。这里不发这种帧，
直接拒绝，由调用方决定怎么处理（主程序的做法是这一帧不发，并记事件）。

停车帧是四个 000、标志 '1' 的合法帧。固件解析后四个目标都是 0，对应
`test[n]==0` 时电机输出归零的已有分支，不需要改固件。旧 NUC 从来不发它，
所以这是我们这边的新行为，要告知 STM32 那边。
"""

import math

FRAME_LEN = 21
HEADER = b"%@1"
TAIL = b"\xff\xff"
MAX_SPEED = 999


class PacketRejected(ValueError):
    """这组速度编不成合法帧。不发送。"""


def truncate_like_cpp(value):
    """C++ 的 int(float)：向零截断。非有限值抛错。"""
    if not math.isfinite(value):
        raise PacketRejected(f"speed is not finite: {value!r}")
    return int(value)  # Python int() 对 float 也是向零截断


def encode_side_speed(left_speed, right_speed):
    """把左右速度编成 21 字节帧。负数、超过 999、NaN/Inf 抛 PacketRejected。"""
    left = truncate_like_cpp(float(left_speed))
    right = truncate_like_cpp(float(right_speed))
    # 按截断后的整数判断：-0.5 截成 0，旧代码会正常发 000，这里保持一致。
    for name, value in (("left", left), ("right", right)):
        if value < 0:
            raise PacketRejected(f"{name} speed {value} is negative; the frame cannot encode it")
        if value > MAX_SPEED:
            raise PacketRejected(f"{name} speed {value} exceeds {MAX_SPEED}")
    body = f"{left:03d}1{left:03d}1{right:03d}1{right:03d}1".encode("ascii")
    frame = HEADER + body + TAIL
    assert len(frame) == FRAME_LEN
    return frame


def stop_frame():
    """四个 000 的停车帧。"""
    return encode_side_speed(0, 0)


def parse_like_stm32(frame):
    """按固件 parse_packet_simple 的写法解出四个目标值，用于离线核对。

    固件只看 data[2]=='1'，数字按 (c-'0') 算，标志是 '0' 才取负。
    这里额外要求长度、帧头帧尾、数字和标志字符都合法，不合法返回 None。
    """
    if len(frame) != FRAME_LEN or frame[:3] != HEADER or frame[-2:] != TAIL:
        return None
    values = []
    for start in (3, 7, 11, 15):
        digits = frame[start:start + 3]
        flag = frame[start + 3:start + 4]
        if not digits.isdigit() or flag not in (b"0", b"1"):
            return None
        value = (digits[0] - 48) * 100 + (digits[1] - 48) * 10 + (digits[2] - 48)
        values.append(-value if flag == b"0" else value)
    return values
