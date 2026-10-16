"""Stage a verified HLS IP package into a new local catalog directory."""

import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
NS = {"s": "http://www.spiritconsortium.org/XMLSchema/SPIRIT/1685-2009"}


def main():
    work = ROOT / "hls/build/ffn_accel_modern"
    packages = sorted(work.rglob("*.zip"))
    candidates = []
    for path in packages:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            components = [name for name in names if name == "component.xml" or name.endswith("/component.xml")]
            for component in components:
                tree = ET.fromstring(archive.read(component))
                if tree.findtext("s:name", namespaces=NS) == "ffn_accel":
                    candidates.append((path, component))
    if len(candidates) != 1:
        raise RuntimeError("Expected exactly one exported FFN IP zip, got " + str(candidates))
    path, component = candidates[0]
    prefix = str(PurePosixPath(component).parent)
    prefix = "" if prefix == "." else prefix + "/"
    destination = ROOT / "ip_repo"
    if destination.exists():
        raise FileExistsError(destination)
    destination.mkdir()
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            if not info.filename.startswith(prefix):
                continue
            relative = PurePosixPath(info.filename[len(prefix):])
            if relative.is_absolute() or ".." in relative.parts or any(":" in part or "\\" in part for part in relative.parts):
                raise RuntimeError("Unsafe archive member: " + info.filename)
            target = destination.joinpath(*relative.parts)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as stream:
                    stream.write(archive.read(info))
    tree = ET.parse(destination / "component.xml").getroot()
    identity = {key: tree.findtext("s:" + key, namespaces=NS) for key in ("vendor", "library", "name", "version")}
    ports = {}
    for port in tree.findall("s:model/s:ports/s:port", NS):
        name = port.findtext("s:name", namespaces=NS)
        if name in ("m_axi_gmem0_ARADDR", "m_axi_gmem0_AWADDR", "m_axi_gmem1_ARADDR", "m_axi_gmem1_AWADDR"):
            left = port.findtext("s:wire/s:vector/s:left", namespaces=NS)
            right = port.findtext("s:wire/s:vector/s:right", namespaces=NS)
            if left is None or right is None:
                raise RuntimeError("Missing packaged address vector: " + name)
            width = int(left) - int(right) + 1
            if width != 64:
                raise RuntimeError("Packaged address width mismatch: " + name + "=" + str(width))
            ports[name] = width
    if len(ports) != 4:
        raise RuntimeError("Package lacks expected address ports: " + str(ports))
    report = {"zip": str(path), "zip_bytes": path.stat().st_size,
              "zip_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
              "catalog_directory": str(destination), "identity": identity,
              "packaged_master_address_widths": ports,
              "component_xml_sha256": hashlib.sha256((destination / "component.xml").read_bytes()).hexdigest(),
              "status": "package_staged_not_system_verified"}
    output = ROOT / "evidence/hls/ip_package_check.json"
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
