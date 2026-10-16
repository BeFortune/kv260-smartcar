"""Archive only new snapshots of real build evidence; never overwrite prior attempts."""

import argparse
import datetime
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent
REPO = Path("D:/Learning/FPGA/pynq_ffn")


def record_file(source, destination, rows):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError("Refusing to overwrite archive: " + str(destination))
    data = source.read_bytes()
    with destination.open("xb") as stream:
        stream.write(data)
    digest = hashlib.sha256(data).hexdigest()
    if digest != hashlib.sha256(destination.read_bytes()).hexdigest():
        raise RuntimeError("Archive hash mismatch: " + str(source))
    rows.append({"source": str(source), "archived_path": str(destination),
                 "bytes": destination.stat().st_size, "sha256": digest})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot")
    parser.add_argument("--area", choices=("hls", "vivado"), default="hls")
    options = parser.parse_args()
    if not options.snapshot or Path(options.snapshot).name != options.snapshot:
        parser.error("Expected a single snapshot directory name")
    base = REPO / "artifacts" / (options.area + "_20261015") / "retry04" / options.snapshot
    if base.exists():
        parser.error("Snapshot already exists: " + str(base))
    base.mkdir(parents=True)
    rows = []
    evidence_paths = [ROOT / "evidence/preflight", ROOT / "evidence/source_manifest.json"]
    if options.area == "hls":
        evidence_paths += [ROOT / "evidence/hls", ROOT / "hls/build/hls_config.cfg",
                           ROOT / "hls/build/hls_config_cosim_small.cfg"]
        for name in ("ffn_accel.cpp", "ffn_accel.hpp", "testbench.cpp", "testbench_cosim_small.cpp",
                     "make_config.py", "build_hls.tcl"):
            evidence_paths.append(ROOT / "hls" / name)
        work = ROOT / "hls/build/ffn_accel_modern"
        if work.exists():
            for source in sorted(work.rglob("*")):
                if source.is_file() and (source.suffix.lower() in (".log", ".rpt", ".xml", ".cfg", ".ini")
                                         or source.name == "xffn_accel_hw.h"):
                    if ".autopilot" not in source.parts:
                        record_file(source, base / "tool_outputs" / source.relative_to(work), rows)
            rtl = work / "hls/syn/verilog"
            if rtl.exists():
                for source in sorted(rtl.glob("*")):
                    if source.is_file():
                        record_file(source, base / "rtl" / source.name, rows)
            for source in sorted(work.rglob("*.zip")):
                record_file(source, base / "ip_packages" / source.relative_to(work), rows)
    else:
        evidence_paths += [ROOT / "evidence/vivado", ROOT / "evidence/software", ROOT / "output"]
        project = ROOT / "vivado"
        if project.exists():
            for source in sorted(project.rglob("*")):
                if source.is_file() and source.suffix.lower() in (".log", ".rpt", ".tcl", ".jou", ".bd", ".hwh", ".xpr", ".xdc"):
                    record_file(source, base / "tool_outputs" / source.relative_to(project), rows)
    for source in evidence_paths:
        if source.is_dir():
            for item in sorted(source.rglob("*")):
                if item.is_file():
                    record_file(item, base / item.relative_to(ROOT), rows)
        elif source.is_file():
            record_file(source, base / source.relative_to(ROOT), rows)
    for source in sorted(ROOT.glob("*.py")) + sorted(ROOT.glob("*.tcl")):
        record_file(source, base / "scripts" / source.name, rows)
    record_file(Path("D:/ffn_build/run_stage.py"), base / "scripts/run_stage.py", rows)
    stages = []
    for source in sorted((ROOT / "evidence").rglob("*.command.json")):
        data = json.loads(source.read_text(encoding="utf-8"))
        stages.append({"file": str(source), **data})
    status = {
        "attempt": "retry04", "snapshot": options.snapshot,
        "snapshot_time": datetime.datetime.now().astimezone().isoformat(),
        "stages": stages,
        "cosim_scope": "reduced, 30 valid tokens; no 659/660-token RTL invocation",
        "produced_bit": (ROOT / "output/ffn_overlay_wrapper.bit").is_file(),
        "produced_hwh": (ROOT / "output/ffn_overlay_wrapper.hwh").is_file(),
        "board_execution": "not_run",
        "warning": "Snapshot records only reached stages. A produced file alone is not acceptance signoff.",
    }
    with (base / "status.json").open("x", encoding="utf-8") as stream:
        json.dump(status, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    with (base / "manifest.json").open("x", encoding="utf-8") as stream:
        json.dump({"files": rows}, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"archive": str(base), "files_copied_and_hash_verified": len(rows),
                      "stage_results": [{"file": row["file"], "status": row["status"],
                                         "exit_code": row["exit_code"]} for row in stages]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
