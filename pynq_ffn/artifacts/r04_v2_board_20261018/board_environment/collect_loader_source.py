"""Read installed loader source as text; do not import PYNQ or touch devices."""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
from pathlib import Path

TARGETS = {
    "pynq/overlay.py": {"Overlay": {"__init__", "download", "is_loaded"}},
    "pynq/bitstream.py": {"Bitstream": {"__init__", "download"}},
    "pynq/pl_server/embedded_device.py": {
        "": {"_preload_binfile"},
        "EmbeddedDevice": {"__init__", "download", "shutdown", "set_axi_port_width"}},
    "pynq/pl_server/device.py": {"Device": {"post_download", "reset", "shutdown"}},
    "pynq/pl_server/xrt_device.py": {"XrtDevice": {"_xrt_download"}},
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    dist = metadata.distribution("pynq")
    files = []
    for relative, targets in TARGETS.items():
        path = Path(dist.locate_file(relative))
        data = path.read_bytes()
        text = data.decode("utf-8")
        tree = ast.parse(text)
        excerpts = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in targets.get("", set()):
                excerpts.append({"class": None, "method": node.name, "line": node.lineno,
                                 "source": ast.get_source_segment(text, node)})
            if isinstance(node, ast.ClassDef) and node.name in targets:
                for method in node.body:
                    if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and method.name in targets[node.name]:
                        excerpts.append({"class": node.name, "method": method.name, "line": method.lineno,
                                         "source": ast.get_source_segment(text, method)})
        files.append({"path": str(path), "sha256": hashlib.sha256(data).hexdigest(), "excerpts": excerpts})
    result = {"status": "已验证", "date_utc": datetime.now(timezone.utc).isoformat(),
              "scope": "Installed source text only; no PYNQ import or device access",
              "pynq_version": dist.version, "files": files}
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"status": result["status"], "output": str(args.output),
                      "files": len(files), "methods": sum(len(f["excerpts"]) for f in files)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
