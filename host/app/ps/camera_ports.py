"""KV260 三路相机的固定口位。只读校验，不打开视频流。

模型输入顺序是左、前、右。两台 icSpring 序列号相同，不能靠序列号区分，
所以身份只认 USB 口。校验读 sysfs 和 /dev/v4l/by-path，不调用 v4l2 采集接口。
"""

from pathlib import Path
import json
import os

CONFIG_NAME = "camera_ports.json"


def load_camera_config(path=None):
    config_path = Path(path) if path else Path(__file__).with_name(CONFIG_NAME)
    with open(config_path, encoding="utf-8") as handle:
        config = json.load(handle)
    cameras = config["cameras"]
    roles = [camera["role"] for camera in cameras]
    ports = [camera["port"] for camera in cameras]
    if roles != ["left", "front", "right"]:
        raise ValueError(f"camera order must be left, front, right, got {roles}")
    if len(set(ports)) != len(ports):
        raise ValueError(f"camera ports must be unique, got {ports}")
    for camera in cameras:
        for key in ("port", "by_path", "vendor_id", "product_id"):
            if not camera[key]:
                raise ValueError(f"camera {camera['role']} is missing {key}")
    if len({camera["by_path"] for camera in cameras}) != len(cameras):
        raise ValueError("camera by-path names must be unique")
    return config


def read_capture_identity(sysfs_root, video_name):
    """读一个 /dev/videoN 的身份。返回 None 表示这不是视频采集节点。"""
    node = Path(sysfs_root) / "class" / "video4linux" / video_name
    if not node.exists():
        return None
    if (node / "index").read_text(encoding="utf-8").strip() != "0":
        return None
    interface = (node / "device").resolve()
    device = interface.parent
    return {
        "video": video_name,
        "port": device.name,
        "vendor_id": (device / "idVendor").read_text(encoding="utf-8").strip(),
        "product_id": (device / "idProduct").read_text(encoding="utf-8").strip(),
    }


def capture_nodes(sysfs_root):
    """列出 sysfs 里所有视频采集节点，跳过 index 不是 0 的元数据节点。"""
    found = {}
    for entry in sorted(Path(sysfs_root, "class", "video4linux").iterdir()):
        identity = read_capture_identity(sysfs_root, entry.name)
        if identity is None:
            continue
        if identity["port"] in found:
            raise RuntimeError(f"port {identity['port']} has two capture nodes")
        found[identity["port"]] = identity
    return found


def verify_camera_ports(config, nodes, links):
    """核对三个口的相机。nodes 按口索引，links 是 by-path 名到设备名的映射。

    不匹配时抛错，不改任何设备状态，也不打开视频流。
    """
    resolved = []
    for camera in config["cameras"]:
        identity = nodes.get(camera["port"])
        if identity is None:
            raise RuntimeError(f"{camera['role']} camera is not on port {camera['port']}")
        actual = f"{identity['vendor_id']}:{identity['product_id']}"
        expected = f"{camera['vendor_id']}:{camera['product_id']}"
        if actual != expected:
            raise RuntimeError(
                f"{camera['role']} port {camera['port']} has {actual}, expected {expected}")
        target = links.get(camera["by_path"])
        if target != identity["video"]:
            raise RuntimeError(
                f"{camera['role']} by-path points to {target}, not {identity['video']}")
        resolved.append({**camera, "video": identity["video"]})
    return resolved


def verify_on_board(config, sysfs_root="/sys", by_path_root=None):
    """读本机的 sysfs 和 by-path，再交给 verify_camera_ports。"""
    root = Path(by_path_root) if by_path_root else Path(config["by_path_dir"])
    links = {
        camera["by_path"]: Path(os.readlink(root / camera["by_path"])).name
        for camera in config["cameras"]
    }
    return verify_camera_ports(config, capture_nodes(sysfs_root), links)
