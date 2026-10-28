"""KV260 串口的固定口位。只读定位，不打开串口，不发送任何字节。

口位是用户绑定的（2026-10-22，两个扩展坞接法）：串口在扩展坞 A 的 1-1.2.3，
相机在 1-1.2.1、1-1.4.1、1-1.4.2。这里只认 USB 口，不认厂商号，
也不认 /dev/ttyUSB0 这种会变的名字。

ttyUSB 和 ttyACM 的 sysfs 长得不一样：ttyUSB 的 device 指向接口下面的
tty 节点，ttyACM 的 device 直接指向接口。两种都按 USB 设备目录的名字
回溯到口，不靠 by-path 名字。
"""

from pathlib import Path
import json

CONFIG_NAME = "serial_port.json"
TTY_PREFIXES = ("ttyUSB", "ttyACM")


def load_serial_config(path=None):
    config_path = Path(path) if path else Path(__file__).with_name(CONFIG_NAME)
    with open(config_path, encoding="utf-8") as handle:
        config = json.load(handle)
    if not config["port"]:
        raise ValueError("serial port is empty")
    line = config["line"]
    if line != {"baud": 115200, "bytesize": 8, "parity": "N", "stopbits": 1}:
        raise ValueError(f"serial line must stay 115200 8N1, got {line}")
    if config["port"] in {camera["port"] for camera in config["cameras_on_same_hub"]}:
        raise ValueError(f"serial port {config['port']} is already a camera port")
    return config


def tty_port(sysfs_root, tty_name):
    """回溯一个 tty 节点到它的 USB 口。不是 USB 串口设备就返回 None。"""
    if not tty_name.startswith(TTY_PREFIXES):
        return None
    node = Path(sysfs_root) / "class" / "tty" / tty_name
    if not node.exists():
        return None
    current = (node / "device").resolve()
    while current != current.parent:
        if (current / "idVendor").exists() and (current / "busnum").exists():
            return current.name
        current = current.parent
    return None


def serial_nodes(sysfs_root):
    """列出 ttyUSB 和 ttyACM，按 USB 口索引。一个口上有两个就报错。"""
    found = {}
    for entry in sorted(Path(sysfs_root, "class", "tty").iterdir()):
        port = tty_port(sysfs_root, entry.name)
        if port is None:
            continue
        if port in found:
            raise RuntimeError(f"port {port} has two serial nodes: {found[port]}, {entry.name}")
        found[port] = entry.name
    return found


def resolve_serial_port(config, nodes):
    """在给定的口到设备名映射里找配置指定的那个口。没插或被占就报错。"""
    device = nodes.get(config["port"])
    if device is None:
        raise RuntimeError(f"serial converter is not on port {config['port']}")
    if not device.startswith(TTY_PREFIXES):
        raise RuntimeError(f"port {config['port']} has {device}, which is not a serial node")
    return {"port": config["port"], "device": device, "line": config["line"]}


def resolve_on_board(config, sysfs_root="/sys"):
    """读本机 sysfs 再定位。只读目录，不打开串口。"""
    return resolve_serial_port(config, serial_nodes(sysfs_root))
