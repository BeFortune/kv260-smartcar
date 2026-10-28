"""手柄启停（KV260，xpad 驱动，/dev/input/jsN）。只读手柄，不开相机、串口、PL。

按键编号来自 2026-10-22 板上实测，写在 joystick.json：Back=6（启停），
右摇杆按下=10（急停），和 NUC 旧代码 buttons[6]/buttons[10] 一致。

手柄超时自动关机时，接收器会切到 2106 模式，js0 消失，按手柄断开处理（停车）；
手柄唤醒后接收器回到 2104，xpad 自动重绑，设备检查会重新找到 jsN。

规则（用户 2026-10-22 定）：
  toggle_button  等待启动时按下 -> 开始自动跑；运行中按下 -> 补停车帧，回到等待启动。
  stop_button    任何时候按下 -> 立刻停车：运行中补停车帧回到等待启动；等待时取消还没生效的启动。
  手柄断开或读出错 -> 运行中按停车处理；设备检查不通过，回到等设备。
只认按下沿（value=1），忽略驱动连接时补发的初始状态事件（JS_EVENT_INIT）。
进入等待启动时清空积压的按键（car_loop 的 on_wait_start 调 clear），
避免设备没齐时按下的键在设备齐了以后让车突然开走。

注意：这个接收器断开会被发现，但手柄本身没电或走出接收范围时，接收器不一定会报，
程序只能看到“没有新按键”，分不出来。
"""

from collections import deque
import json
import os
from pathlib import Path
import select
import struct
import threading
import time

CONFIG_NAME = "joystick.json"
EVENT = struct.Struct("IhBB")  # struct js_event: time, value, type, number
JS_BUTTON, JS_INIT = 0x01, 0x80


def load_joystick_config(path=None):
    config_path = Path(path) if path else Path(__file__).with_name(CONFIG_NAME)
    with open(config_path, encoding="utf-8") as handle:
        config = json.load(handle)
    for key in ("port", "vendor_id", "product_id"):
        if not config.get(key):
            raise ValueError(f"joystick config is missing {key}")
    toggle, stop = config.get("toggle_button"), config.get("stop_button")
    if not all(isinstance(b, int) and not isinstance(b, bool) and b >= 0 for b in (toggle, stop)):
        raise ValueError(f"joystick buttons must be non-negative integers, got {toggle}, {stop}")
    if toggle == stop:
        raise ValueError("toggle and stop must be different buttons")
    return config


def usb_device_of(path):
    """从 sysfs 节点往上找到 USB 设备目录（有 idVendor 和 busnum 的那一层）。"""
    current = Path(path).resolve()
    while current != current.parent:
        if (current / "idVendor").exists() and (current / "busnum").exists():
            return current
        current = current.parent
    return None


def find_js(config, sysfs_root="/sys"):
    """按 USB 口找 jsN 的名字。只读 sysfs。没有、多于一个或型号不对都抛错。"""
    root = Path(sysfs_root, "class", "input")
    found = []
    if root.exists():
        for entry in sorted(root.iterdir()):
            if not entry.name.startswith("js"):
                continue
            usb = usb_device_of(entry / "device")
            if usb is not None and usb.name == config["port"]:
                found.append((entry.name, usb))
    if not found:
        raise RuntimeError(f"joystick is not on port {config['port']} "
                           "(receiver unplugged, wrong mode, or xpad not bound)")
    if len(found) > 1:
        raise RuntimeError(f"port {config['port']} has several joysticks: {[n for n, _ in found]}")
    name, usb = found[0]
    actual = "{}:{}".format((usb / "idVendor").read_text(encoding="utf-8").strip(),
                            (usb / "idProduct").read_text(encoding="utf-8").strip())
    expected = f"{config['vendor_id']}:{config['product_id']}"
    if actual != expected:
        raise RuntimeError(f"joystick port {config['port']} has {actual}, expected {expected}")
    return name


class JsDevice:
    """真设备。read(timeout) 超时返回 b""，设备拔掉时抛 OSError。"""

    def __init__(self, path):
        self.fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)

    def read(self, timeout):
        ready, _, _ = select.select([self.fd], [], [], timeout)
        if not ready:
            return b""
        try:
            data = os.read(self.fd, EVENT.size * 64)
        except BlockingIOError:
            return b""
        if not data:
            raise IOError("joystick device closed")
        return data

    def close(self):
        os.close(self.fd)


class _Reader(threading.Thread):
    """后台读事件，只把启停两个键的按下沿放进队列。"""

    def __init__(self, device, toggle, stop, clock):
        super().__init__(name="joystick", daemon=True)
        self.device, self.toggle, self.stop_button, self.clock = device, toggle, stop, clock
        self.events = deque()
        self.lock = threading.Lock()
        self.error = None
        self.events_seen = 0
        self._halt = threading.Event()

    def run(self):
        buf = b""
        try:
            while not self._halt.is_set():
                data = self.device.read(0.1)
                if not data:
                    continue
                buf += data
                while len(buf) >= EVENT.size:
                    _, value, etype, number = EVENT.unpack_from(buf)
                    buf = buf[EVENT.size:]
                    kind = None
                    if not etype & JS_INIT and etype == JS_BUTTON and value == 1:
                        kind = {self.toggle: "toggle", self.stop_button: "stop"}.get(number)
                    with self.lock:
                        if kind:
                            self.events.append((kind, self.clock()))
                        self.events_seen += 1
        except Exception as error:  # noqa: BLE001
            self.error = f"{type(error).__name__}: {error}"
        finally:
            try:
                self.device.close()
            except Exception:  # noqa: BLE001
                pass

    def take(self):
        with self.lock:
            items = list(self.events)
            self.events.clear()
        return items

    def halt(self, timeout=1.0):
        self._halt.set()
        self.join(timeout)


class Joystick:
    """给 car_loop 用：start_trigger()、stop_request()、clear()；给设备检查用：check()。"""

    def __init__(self, config, locate=None, opener=JsDevice, dev_root="/dev/input", clock=time.monotonic):
        self.config = config
        self.locate = locate or (lambda: find_js(config))
        self.opener, self.dev_root, self.clock = opener, dev_root, clock
        self.device = None
        self.opens = 0
        self.presses = {"toggle": 0, "stop": 0}
        self._reader = None

    def lost_reason(self):
        reader = self._reader
        if reader is None:
            return "joystick not open"
        if reader.error:
            return f"joystick lost: {reader.error}"
        if not reader.is_alive():
            return "joystick lost: reader stopped"
        return None

    def check(self):
        """None 表示手柄在读；否则（重新）定位并打开，失败返回原因。不发任何东西给手柄。"""
        if self.lost_reason() is None:
            return None
        self._halt()
        try:
            name = self.locate()
            device = self.opener(os.path.join(self.dev_root, name))
        except Exception as error:  # noqa: BLE001
            return f"joystick: {type(error).__name__}: {error}"
        self.device = name
        self.opens += 1
        self._reader = _Reader(device, self.config["toggle_button"], self.config["stop_button"], self.clock)
        self._reader.start()
        return None

    def _take(self):
        items = self._reader.take() if self._reader is not None else []
        for kind, _ in items:
            self.presses[kind] += 1
        return items

    def clear(self):
        """丢掉积压的按键。进入等待启动时调用。"""
        self._take()

    def start_trigger(self):
        """等待启动时调用。按顺序看积压的键：启停键 -> 要启动，停车键 -> 取消。"""
        if self.lost_reason() is not None:
            return False
        start = False
        for kind, _ in self._take():
            start = kind == "toggle"
        return start

    def stop_request(self):
        """运行中每轮调用。返回停车原因，None 表示继续跑。"""
        lost = self.lost_reason()
        if lost is not None:
            return lost
        kinds = [kind for kind, _ in self._take()]
        if "stop" in kinds:  # 同一批里有急停就按急停算
            return "stop button: emergency stop"
        if kinds:
            return "toggle button: manual stop"
        return None

    def _halt(self):
        if self._reader is not None:
            self._reader.halt()
            self._reader = None

    def close(self):
        self._halt()
