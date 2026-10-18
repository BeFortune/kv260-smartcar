"""Collect finished board reports with hashes, never raw recordings or credentials."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import zipfile


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--program", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--supplement-dir", action="append", default=[], type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    reports = {}
    for name, path in (("parent", args.run / "report.json"),
                       *((mode, args.run / mode / "report.json")
                         for mode in ("golden4", "split_ort4", "split_pl4")),
                       ("programming", args.program / "report.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        if report.get("status") != "已验证":
            raise RuntimeError("Only verified completed evidence can be collected as final: " + name)
        reports[name] = report
    pl = reports["split_pl4"]
    if pl.get("kernel_starts_observed") != 517 or pl.get("expected_kernel_starts") != 517:
        raise ValueError("Kernel start coverage differs")
    if pl.get("failure_mode") == "failed_quarantined":
        raise ValueError("Do not collect a quarantined run as complete")
    entries = {}
    with zipfile.ZipFile(args.output, "x", zipfile.ZIP_DEFLATED) as archive:
        roots = [("board_run", args.run), ("programming", args.program)]
        roots.extend(("supplement/" + path.name, path) for path in args.supplement_dir)
        if len({label for label, _ in roots}) != len(roots):
            raise ValueError("Duplicate supplement directory labels")
        for label, root in roots:
            for path in sorted(root.rglob("*")):
                if not path.is_file() or path.suffix not in (".json", ".jsonl", ".log", ".npz"):
                    continue
                data = path.read_bytes()
                name = label + "/" + path.relative_to(root).as_posix()
                entries[name] = {"bytes": len(data), "sha256": sha(data)}
                archive.writestr(name, data)
        manifest = {"schema": "r04v2.board_evidence.v1", "status": "已验证",
                    "date_utc": datetime.now(timezone.utc).isoformat(), "files": entries,
                    "scope": "Real KV260 offline CPU and PS+PL reports/reference outputs; not live camera or vehicle",
                    "raw_recordings_included": False}
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    print(json.dumps({"status": "已验证", "path": str(args.output),
                      "bytes": args.output.stat().st_size, "sha256": sha(args.output.read_bytes()),
                      "files": len(entries)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
