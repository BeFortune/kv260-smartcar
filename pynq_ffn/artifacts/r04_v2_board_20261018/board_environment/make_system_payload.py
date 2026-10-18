"""Recheck original system reports, then freeze a board system-acceptance payload."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parent.parent
SYSTEM = ROOT / "system_build"
COLLECTED = SYSTEM / "collected"
OUTPUT = ROOT / "board_environment/system_payload_v1.zip"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    if OUTPUT.exists():
        raise FileExistsError(OUTPUT)
    delivery = json.loads((SYSTEM / "delivery_manifest.json").read_text(encoding="utf-8"))
    for name, expected in delivery["files"].items():
        path = SYSTEM / name
        if len(path.read_bytes()) != expected["bytes"] or sha(path.read_bytes()) != expected["sha256"]:
            raise ValueError("System delivery file differs: " + name)
    spec = importlib.util.spec_from_file_location("review_system", SYSTEM / "review_system.py")
    reviewer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reviewer)
    result = json.loads((SYSTEM / "system_acceptance_report.json").read_text(encoding="utf-8"))
    if result["status"] != "已验证" or result["ps_config_comparison"]["changes"]:
        raise ValueError("System review not clean")
    members = {}
    reports = {}
    for stage in ("prepare", "bd", "synth", "impl", "export"):
        path = COLLECTED / "records" / (stage + ".finished.json")
        raw = path.read_bytes()
        record = json.loads(raw)
        if record["status"] != "已验证" or record["exit_code"] != 0:
            raise ValueError("System stage not verified: " + stage)
        reports["reports/records/" + path.name] = raw
    ev = COLLECTED / "evidence/vivado"
    for prefix in ("implemented", "pre_bitgen", "post_bitgen"):
        for suffix, check in (("timing_summary", reviewer.timing), ("route_status", reviewer.route), ("drc", reviewer.drc)):
            path = ev / (prefix + "_" + suffix + ".rpt")
            check(path.read_text(encoding="utf-8"))
            reports["reports/vivado/" + path.name] = path.read_bytes()
    for path in (SYSTEM / "system_acceptance_report.json", COLLECTED / "traceability/artifact_verification.json",
                 ev / "ps_properties.txt", ev / "bd_address_map.txt", SYSTEM / "child_logs/methodology_routed.rpt"):
        reports["reports/" + path.name] = path.read_bytes()
    bit, hwh = COLLECTED / "load/ffn_overlay_wrapper.bit", COLLECTED / "load/ffn_overlay_wrapper.hwh"
    expected = {"bit_sha256": "8f438a07fa7cfa2f2db9e276f904388df7a06476cc090d55758ceb231dd34ab7",
                "hwh_sha256": "7254ea3c456098518917e89b836414592b34ffe3e7a24aec2d7ba772713cec85"}
    if sha(bit.read_bytes()) != expected["bit_sha256"] or sha(hwh.read_bytes()) != expected["hwh_sha256"]:
        raise ValueError("Final bit/HWH pair differs")
    reviewer.hwh_review(hwh)
    xsa = COLLECTED / "traceability/ffn_overlay_wrapper.xsa"
    with zipfile.ZipFile(xsa) as package:
        bits = [name for name in package.namelist() if name.endswith(".bit")]
        if len(bits) != 1 or package.read(bits[0]) != bit.read_bytes() or package.read("ffn_overlay.hwh") != hwh.read_bytes():
            raise ValueError("XSA same-export mismatch")
    attestation = {"schema": "r04v2.system_acceptance.v1", "status": "已验证", **expected,
                   "evidence_files": {name: sha(data) for name, data in reports.items()},
                   "scope": "Reviewed full PS+PL system build only; board programming/compute is separate",
                   "full_system_timing": result["gates"],
                   "ps_config_comparison": result["ps_config_comparison"],
                   "xsa_sha256": sha(xsa.read_bytes()), "not_board_acceptance": True}
    attestation_raw = (json.dumps(attestation, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    members["deploy/overlay/r04_v2_20261018.bit"] = bit.read_bytes()
    members["deploy/overlay/r04_v2_20261018.hwh"] = hwh.read_bytes()
    members.update({"system_acceptance/" + name: data for name, data in reports.items()})
    members["system_acceptance/system_acceptance.json"] = attestation_raw
    with zipfile.ZipFile(OUTPUT, "x", zipfile.ZIP_DEFLATED) as package:
        for name, data in members.items():
            package.writestr(name, data)
        package.writestr("payload_manifest.json", json.dumps({"files": {name: {"bytes": len(data), "sha256": sha(data)}
                                                                           for name, data in members.items()}}, indent=2))
    summary = {"status": "已验证", "payload": str(OUTPUT), "payload_sha256": sha(OUTPUT.read_bytes()),
               "acceptance_sha256": sha(attestation_raw), **expected, "system_delivery_files_rehashed": len(delivery["files"]),
               "board_programmed": False}
    with (ROOT / "board_environment/system_payload_summary.json").open("x", encoding="utf-8") as stream:
        json.dump(summary, stream, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
