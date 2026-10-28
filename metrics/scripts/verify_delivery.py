"""Offline validation for the delivery tree; never imports project runtime modules."""
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
REQUIRED = [
    "README.md", "docs/architecture.md", "docs/interface.md", "docs/hardware_setup.md",
    "fpga/build/conv_front_int8_20261021.bit", "fpga/build/conv_front_int8_20261021.hwh",
    "host/model/s16_j1c2_cut2int8_ps.onnx", "host/deploy/make_bundle.py",
    "metrics/metrics.csv", "skill/README.md", "report/design_report.md", "delivery_manifest.json",
]
PINNED = {
    "fpga/build/conv_front_int8_20261021.bit": "8ab0f411cc3ddee74045a753b379d265a121871dc656a53ef25b7c2edfe8da24",
    "fpga/build/conv_front_int8_20261021.hwh": "c2b5c8138087c8e398d509df40e1c854311ebcca249f7a0e232c107d8db6d067",
    "host/model/s16_j1c2_cut2int8_ps.onnx": "61bcec8f01ffca61ecc611f12bdf61a177f8ee0e9dc0febd256e02b8132cecdb",
    "host/model/params.json": "889703bc0ea80da1a1f49f647c11714b249ee1eaea3149894f35dbbf4eb86a3f",
    "host/model/ref_int8.py": "02feef12ea147f5b772a726ebd3c90d5f1e66bd33ff9fcefa50c3618a040edb4",
}
errors = []
warnings = []


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def ignored(relative):
    return (relative == "delivery_manifest.json" or relative.startswith((".git/", "host/deploy/dist/", "build_runs/"))
            or "__pycache__" in relative.split("/") or ".pytest_cache" in relative.split("/"))


for relative in REQUIRED:
    if not (ROOT / relative).is_file():
        errors.append(f"missing required file: {relative}")

manifest_path = ROOT / "delivery_manifest.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
listed = {item["path"]: item for item in manifest["files"]}
for relative, item in listed.items():
    path = ROOT / relative
    if not path.is_file():
        errors.append(f"manifest file missing: {relative}")
        continue
    data = path.read_bytes()
    if len(data) != item["bytes"] or sha256(data) != item["sha256"]:
        errors.append(f"manifest hash/size mismatch: {relative}")
    if item["source"] and ("../" in item["source"] or re.search(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]", item["source"])):
        errors.append(f"non-portable manifest source: {relative}")

for path in ROOT.rglob("*"):
    if not path.is_file():
        continue
    relative = path.relative_to(ROOT).as_posix()
    if ignored(relative):
        continue
    if relative not in listed:
        errors.append(f"unlisted delivery file: {relative}")
    size = path.stat().st_size
    if size > 100 * 1024 * 1024:
        errors.append(f"GitHub hard-limit file: {relative} ({size} bytes)")
    elif size > 50 * 1024 * 1024:
        warnings.append(f"large file: {relative} ({size} bytes)")
    if relative in PINNED and sha256(path.read_bytes()) != PINNED[relative]:
        errors.append(f"pinned hardware/model hash mismatch: {relative}")
    if path.suffix in (".sh", ".service") and b"\r\n" in path.read_bytes():
        errors.append(f"CRLF in Linux script: {relative}")
    if path.suffix == ".py":
        try:
            compile(path.read_bytes(), relative, "exec")
        except SyntaxError as exc:
            errors.append(f"Python syntax: {relative}:{exc.lineno}: {exc.msg}")
    if path.suffix.lower() in (".md", ".py", ".json", ".txt", ".csv", ".cfg", ".rpt", ".sh", ".service"):
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            if re.search(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]", line):
                errors.append(f"absolute Windows path: {relative}:{lineno}")
            if re.search(r"(ghp_|gho_|github_pat_)[A-Za-z0-9_]{20,}", line):
                errors.append(f"possible GitHub token: {relative}:{lineno}")
            if relative != "metrics/scripts/verify_delivery.py" and "-----BEGIN " in line and "PRIVATE KEY-----" in line:
                errors.append(f"private key material: {relative}:{lineno}")

link_pattern = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
for path in ROOT.rglob("*.md"):
    relative = path.relative_to(ROOT).as_posix()
    if relative.startswith(".git/"):
        continue
    text = path.read_text(encoding="utf-8")
    for target in link_pattern.findall(text):
        target = target.split("#", 1)[0].strip()
        if not target or target.startswith(("http://", "https://", "mailto:")):
            continue
        if not (path.parent / target).resolve().exists():
            errors.append(f"broken Markdown link: {relative} -> {target}")

result = {
    "status": "已验证" if not errors else "失败",
    "files": len(listed),
    "bytes": sum(item["bytes"] for item in listed.values()),
    "errors": errors,
    "warnings": warnings,
}
print(json.dumps(result, ensure_ascii=False, indent=2))
sys.exit(1 if errors else 0)
