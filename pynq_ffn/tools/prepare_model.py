from pathlib import Path
import argparse
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ps.partition import build_bundle


def main():
    parser = argparse.ArgumentParser(description="Prepare reviewed model CPU/FFN graph partitions; no hardware IO.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--model", type=Path)
    source.add_argument("--payload", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    manifest = build_bundle(args.output_dir, model=args.model, payload=args.payload)
    print(json.dumps({"status": "prepared", "bundle": str(args.output_dir.resolve()), "source_sha256": manifest["source_sha256"], "files": len(manifest["files"]), "hardware_execution": False}))


if __name__ == "__main__":
    main()
