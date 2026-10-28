"""car_loop 的 devices：check() -> None 表示齐了，否则返回原因字符串。

顺序：相机口位（只读 sysfs）-> 串口口位（只读 sysfs）-> 打开串口（不发字节）-> 三路相机出帧。
"""


class DeviceCheck:
    """joystick 可选：给了就最先查手柄（只读，不发东西给手柄）。"""

    def __init__(self, verify_cameras, resolve_serial, cameras, link, joystick=None):
        self.verify_cameras, self.resolve_serial = verify_cameras, resolve_serial
        self.cameras, self.link, self.joystick = cameras, link, joystick

    def check(self):
        if self.joystick is not None:
            problem = self.joystick.check()
            if problem:
                return problem
        try:
            self.verify_cameras()
        except Exception as error:  # noqa: BLE001
            return f"camera ports: {error}"
        try:
            serial = self.resolve_serial()
        except Exception as error:  # noqa: BLE001
            return f"serial port: {error}"
        problem = self.link.ensure_open(serial["device"])
        if problem:
            return problem
        return self.cameras.ensure_running()
