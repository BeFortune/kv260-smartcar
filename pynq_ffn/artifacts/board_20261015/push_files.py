"""Push overlay/bundle/ps/data trees to the board over SFTP, then hash-verify both ends."""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sshboard import connect  # noqa: E402
import paramiko  # noqa: E402

REPO = Path(r"D:\Learning\FPGA\pynq_ffn")
BOARDROOT = "/home/ubuntu/ffn_migrate"

SECTIONS = [
    # (local dir, board dir)
    (REPO / "artifacts/vivado_20261015/retry04/overlay", f"{BOARDROOT}/overlay"),
    (REPO / "artifacts/bundle_20261014", f"{BOARDROOT}/bundle"),
    (REPO / "ps", f"{BOARDROOT}/ps"),
    (Path(r"D:\Learning\FPGA\_boardwork\data"), f"{BOARDROOT}/data"),
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ftp_mkdir(sftp, path):
    parts = path.strip("/").split("/")
    cur = ""
    for part in parts:
        cur += "/" + part
        try:
            sftp.stat(cur)
        except FileNotFoundError:
            sftp.mkdir(cur)


def puttree(sftp: paramiko.SFTPClient, local: Path, remote: str):
    ftp_mkdir(sftp, remote)
    for name in sorted(os.listdir(local)):
        # skip caches and notebook artifacts
        if name == "__pycache__":
            continue
        lpath = local / name
        rpath = f"{remote}/{name}"
        if lpath.is_dir():
            puttree(sftp, lpath, rpath)
        else:
            size = lpath.stat().st_size
            print(f"  put {rpath} ({size} bytes)", flush=True)
            sftp.put(str(lpath), rpath, confirm=True)
            rsize = sftp.stat(rpath).st_size
            if rsize != size:
                raise IOError(f"size mismatch {lpath}: {size} != {rsize}")


def main():
    client = connect()
    sftp = client.open_sftp()
    try:
        for local_dir, remote_dir in SECTIONS:
            print(f"=== {local_dir} -> {remote_dir}")
            puttree(sftp, local_dir, remote_dir)
        # host-side manifest for later board verification
        manifest = {}
        for local_dir, remote_dir in SECTIONS:
            for path in sorted(local_dir.rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts:
                    rel = str(path.relative_to(local_dir)).replace("\\", "/")
                    manifest[f"{remote_dir}/{rel}"] = sha256(path)
        out = Path(r"D:\Learning\FPGA\_boardwork\host_manifest.json")
        import json
        out.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        print(f"host manifest: {len(manifest)} files -> {out}")
    finally:
        sftp.close()
        client.close()


if __name__ == "__main__":
    main()
