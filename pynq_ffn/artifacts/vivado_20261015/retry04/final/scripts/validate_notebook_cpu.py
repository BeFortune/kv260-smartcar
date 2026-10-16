"""Execute notebook CPU-only path with explicit guards against PYNQ download."""

import datetime
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path("D:/Learning/FPGA/pynq_ffn")
WORK = Path(__file__).resolve().parent


def main():
    notebook_path = ROOT / "notebooks/01_ps_to_pl.ipynb"
    notebook = json.loads(notebook_path.read_text(encoding="utf-8"))
    cells = [(index, "".join(cell["source"])) for index, cell in enumerate(notebook["cells"])
             if cell.get("cell_type") == "code"]
    assert len(cells) == 6
    assert any("ALLOW_PL_DOWNLOAD = False" in text for _, text in cells)
    assert not any("ALLOW_PL_DOWNLOAD = True" in text for _, text in cells)
    namespace = {"__name__": "__main__"}
    results = []
    for index, source in cells:
        started = datetime.datetime.now().astimezone().isoformat()
        exec(compile(source, str(notebook_path) + ":cell" + str(index), "exec"), namespace)
        assert namespace.get("ALLOW_PL_DOWNLOAD", False) is False
        assert "pynq" not in sys.modules
        results.append({"cell_index": index, "status": "passed", "started": started,
                        "finished": datetime.datetime.now().astimezone().isoformat()})
    bit = namespace["BITSTREAM"]
    assert bit.is_file() and bit.with_suffix(".hwh").is_file()
    assert namespace["IP_NAME"] == "ffn_accel_0"
    manifest = json.loads((bit.parent / "manifest.json").read_text(encoding="utf-8"))
    for row in manifest["files"]:
        path = bit.parent / row["name"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row["sha256"]
    report = {"passed": True, "notebook": str(notebook_path),
              "notebook_sha256": hashlib.sha256(notebook_path.read_bytes()).hexdigest(),
              "cells": results, "bitstream": str(bit), "ip_name": namespace["IP_NAME"],
              "allow_pl_download": False, "pynq_imported": False,
              "hardware_execution": False, "scope": "CPU-only notebook execution and matched overlay file hashes, not board acceptance"}
    with (WORK / "evidence/software/notebook_cpu_validation.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
