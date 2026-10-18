"""Verify and extract a collected evidence archive into a new local directory."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    if hashlib.sha256(args.archive.read_bytes()).hexdigest() != args.sha256:
        raise ValueError("Evidence archive SHA mismatch")
    with zipfile.ZipFile(args.archive) as archive:
        names = archive.namelist()
        manifest = json.loads(archive.read("manifest.json"))
        if len(names) != len(set(names)) or set(names) != set(manifest["files"]) | {"manifest.json"}:
            raise ValueError("Unexpected evidence archive member set")
        for name, expected in manifest["files"].items():
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
                raise ValueError("Unconfined archive name")
            data = archive.read(name)
            if len(data) != expected["bytes"] or hashlib.sha256(data).hexdigest() != expected["sha256"]:
                raise ValueError("Evidence archive member mismatch: " + name)
        args.output.mkdir(parents=True, exist_ok=False)
        for name in names:
            path = args.output / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(archive.read(name))
    print(json.dumps({"status": "已验证", "files": len(manifest["files"]), "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
