"""Stage source-only board code in a new directory; do not import deployed modules."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import zipfile


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--archive-sha256", required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--record", type=Path, required=True)
    args = parser.parse_args()
    if args.destination.exists() or args.record.exists():
        raise FileExistsError("Staging targets must be new")
    raw = args.archive.read_bytes()
    if sha(raw) != args.archive_sha256:
        raise ValueError("Archive SHA mismatch")
    bundle = args.bundle.resolve(strict=True)
    bm_raw = (bundle / "manifest.json").read_bytes()
    bm = json.loads(bm_raw)
    if bm["schema"] != "pynq_ffn.bundle.v1" or bm["source_sha256"] != "ad58d1fc9168da94b9ce076ba8cc9754c371ead87974da8bc9cc601ad1650d5b":
        raise ValueError("Unreviewed source model")
    for name, spec in bm["files"].items():
        path = bundle / name
        if path.resolve().parent != bundle or path.stat().st_size != spec["bytes"] or sha(path.read_bytes()) != spec["sha256"]:
            raise ValueError("Source bundle member differs: " + name)
    with zipfile.ZipFile(args.archive) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Duplicate archive entries")
        manifest = json.loads(archive.read("source_manifest.json"))
        if set(manifest["files"]) | {"source_manifest.json"} != set(names):
            raise ValueError("Unexpected archive entries")
        for name, spec in manifest["files"].items():
            relative = PurePosixPath(name)
            if relative.is_absolute() or ".." in relative.parts or "\\" in name or not name.endswith(".py"):
                raise ValueError("Unconfined source member")
            data = archive.read(name)
            if len(data) != spec["bytes"] or sha(data) != spec["sha256"]:
                raise ValueError("Source archive member differs")
        args.destination.mkdir(parents=True, exist_ok=False)
        records = {}
        for name, spec in manifest["files"].items():
            path = args.destination / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(archive.read(name))
            records[name] = spec
    target_bundle = args.destination / "deploy/bundle"
    target_bundle.mkdir(parents=True, exist_ok=False)
    for name in ("manifest.json", *bm["files"]):
        source, target = bundle / name, target_bundle / name
        shutil.copyfile(source, target)
        records["deploy/bundle/" + name] = {"bytes": target.stat().st_size, "sha256": sha(target.read_bytes())}
    result = {"status": "已验证", "scope": "File staging only; no PYNQ/project import or hardware access",
              "archive_sha256": args.archive_sha256, "source_bundle_manifest_sha256": sha(bm_raw),
              "files": records, "destination": str(args.destination)}
    with args.record.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"status": result["status"], "files": len(records), "record": str(args.record)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
