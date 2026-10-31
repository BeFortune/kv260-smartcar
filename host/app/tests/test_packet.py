"""ps/packet.py 和旧 NUC SendSideSpeed 逐字节对照。

黄金字节 tests/golden/side_speed_golden.txt 由 tests/golden/side_speed_golden.cpp
（原样照抄 serial_sender.cpp 第 63-83 行）离线编译生成，测试只读这个文件，不调用编译器。
每行：left right 后面跟帧的十六进制字节。
"""
from pathlib import Path
import math
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ps.packet import (FRAME_LEN, PacketRejected, encode_side_speed, parse_like_stm32,
                       stop_frame, truncate_like_cpp)

GOLDEN = Path(__file__).resolve().parent / "golden" / "side_speed_golden.txt"


def golden_cases():
    cases = []
    for line in GOLDEN.read_text(encoding="ascii").splitlines():
        parts = line.split()
        cases.append((float(parts[0]), float(parts[1]), bytes(int(x, 16) for x in parts[2:])))
    return cases


CASES = golden_cases()


def test_golden_file_is_not_empty():
    assert len(CASES) > 200


@pytest.mark.parametrize("left,right,old", CASES)
def test_matches_old_nuc_or_refuses_its_broken_frames(left, right, old):
    in_range = all(0 <= int(v) <= 999 for v in (left, right))
    if in_range:
        # 合法范围：必须和旧代码一个字节都不差。
        assert encode_side_speed(left, right) == old
        assert len(old) == FRAME_LEN
    else:
        # 旧代码在这里发出带 '-' 或超长的错位帧；我们拒绝发送。
        assert len(old) != FRAME_LEN or parse_like_stm32(old) is None
        with pytest.raises(PacketRejected):
            encode_side_speed(left, right)


def test_minus_half_truncates_to_zero_like_cpp():
    assert truncate_like_cpp(-0.5) == 0
    assert encode_side_speed(-0.5, 100) == encode_side_speed(0, 100)


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_non_finite_is_refused(bad):
    with pytest.raises(PacketRejected):
        encode_side_speed(bad, 100)
    with pytest.raises(PacketRejected):
        encode_side_speed(100, bad)


def test_stop_frame_is_four_zero_targets():
    frame = stop_frame()
    assert frame == b"%@1000100010001000" + b"1\xff\xff"
    assert parse_like_stm32(frame) == [0, 0, 0, 0]


def test_firmware_view_is_left_left_right_right():
    assert parse_like_stm32(encode_side_speed(125.9, 300.2)) == [125, 125, 300, 300]
