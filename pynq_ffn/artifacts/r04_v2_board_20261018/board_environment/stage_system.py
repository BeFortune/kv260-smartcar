"""Add a reviewed system payload to staged sources; file operations only."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("archive", "archive-sha256", "inputs", "deployment-manifest"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    package = Path(args.archive)
    inputs = Path(args.inputs).resolve(strict=True)
    target_manifest = Path(args.deployment_manifest)
    if target_manifest.exists() or (inputs / "deploy/overlay").exists() or (inputs / "system_acceptance").exists():
        raise FileExistsError("System staging requires entirely new target paths")
    if sha(package.read_bytes()) != args.archive_sha256:
        raise ValueError("System archive SHA differs")
    with zipfile.ZipFile(package) as archive:
        names = archive.namelist()
        manifest = json.loads(archive.read("payload_manifest.json"))
        if len(names) != len(set(names)) or set(names) != set(manifest["files"]) | {"payload_manifest.json"}:
            raise ValueError("System archive entry set differs")
        for name, spec in manifest["files"].items():
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name:
                raise ValueError("Unconfined system entry")
            if not name.startswith(("deploy/overlay/", "system_acceptance/")):
                raise ValueError("System payload may not replace source files")
            target = inputs / name
            data = archive.read(name)
            if target.exists() or target.is_symlink() or len(data) != spec["bytes"] or sha(data) != spec["sha256"]:
                raise ValueError("System payload target/member differs")
        for name in manifest["files"]:
            target = inputs / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(archive.read(name))
    deployment = inputs / "deploy"
    files = {}
    for directory in ("ps", "bundle", "overlay"):
        for path in sorted((deployment / directory).rglob("*")):
            if path.is_symlink():
                raise ValueError("Deployment symlinks forbidden")
            if path.is_file():
                if path.suffix.lower() in (".pyc", ".pyo", ".so", ".pyd"):
                    raise ValueError("Deployment executable cache/native member forbidden")
                files[path.relative_to(deployment).as_posix()] = {"bytes": path.stat().st_size, "sha256": sha(path.read_bytes())}
    result = {"schema": "ffn_board.deployment.v1", "files": files,
              "scope": "Independent r04v2 source/model/system staging; no hardware programming"}
    with target_manifest.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"status": "已验证", "deployment_manifest": str(target_manifest),
                      "sha256": sha(target_manifest.read_bytes()), "files": len(files)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
