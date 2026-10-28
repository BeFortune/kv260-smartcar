"""串口发送层。car_loop 的 link：write(bytes) -> 实际写出的字节数。

只在 ensure_open() 时打开串口，打开本身不发任何字节。接线只有 GND/TX/RX，
没接 DTR/RTS，所以打开时 CH340 拉动控制线不会复位 STM32。
写失败就关掉串口再把错误抛给 car_loop，下一次设备检查时重新打开。
"""


def _open_pyserial(path, line, write_timeout_s):
    import serial
    return serial.Serial(path, baudrate=line["baud"], bytesize=line["bytesize"], parity=line["parity"],
                         stopbits=line["stopbits"], timeout=0, write_timeout=write_timeout_s,
                         exclusive=True)


class SerialLink:
    def __init__(self, line, opener=_open_pyserial, write_timeout_s=0.1):
        self.line, self.opener, self.write_timeout_s = line, opener, write_timeout_s
        self.port, self.device = None, None
        self.opens = 0

    def ensure_open(self, device):
        """device 是 ttyUSB0 或 /dev/ttyUSB0。返回 None 表示已打开，否则返回原因。"""
        path = device if device.startswith("/") else "/dev/" + device
        if self.port is not None and self.device == path:
            return None
        self.close()
        try:
            self.port = self.opener(path, self.line, self.write_timeout_s)
        except Exception as error:  # noqa: BLE001
            return f"serial open {path}: {type(error).__name__}: {error}"
        self.device = path
        self.opens += 1
        return None

    def write(self, data):
        if self.port is None:
            raise IOError("serial port not open")
        try:
            written = self.port.write(data)
            self.port.flush()  # 21 字节在 115200 下约 1.8 ms，等它发完，避免积压
            return written
        except Exception:
            self.close()
            raise

    def close(self):
        port, self.port, self.device = self.port, None, None
        if port is not None:
            try:
                port.close()
            except Exception:  # noqa: BLE001
                pass
