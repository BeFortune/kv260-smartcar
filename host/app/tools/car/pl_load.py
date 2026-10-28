"""开机下载 PL bit（car-pl-load.service，root）。只下载钉死的那个 bit，下载方式和 2026-10-21 上板验收相同。

用户 2026-10-21 已同意：开机允许自动重新下载一次 PL bit，只用验过的。
  1 核对 bit/hwh 的 SHA256；哈希不对退出码 3，不下载。
  2 读时钟：PL0 必须约 100 MHz，否则不下载。
  2b 先调用 PYNQ 自己的冷启动检查（initial_global_state_file_boot_check）。原因（2026-10-23 重启实测）：
     冷启动后 PL 状态是 unknown，PYNQ 会在下载过程中删掉 global_pl_state.json；但如果建 Overlay 时
     旧 json 还在、且指向内容相同的 bit，parser 会走缓存，下载时就不会重写 json，下载后 json 没了。
     提前让 PYNQ 该删就删，parser 走正常解析，下载时 gen_cache 写新 json，和 2026-10-21 首次下载一样。
  3 这次开机已经下载过（/run 里的标记 boot_id 一致）且 PYNQ 记录的已加载 bit 就是这个：跳过。
  4 Bitstream.download(Overlay(download=False), parser)：不调用 Overlay.download()，所以不会按 HWH 改时钟。
  5 下载后核对：PYNQ 记录的已加载 bit 哈希、PL 时钟寄存器没变、PL0 仍约 100 MHz、核空闲。
  6 写 /run/kv260car/pl_loaded.json（tmpfs，重启自动清掉），car_main 只认这次开机的标记。
不改时钟、不碰相机串口。PYNQ 下载时会把 bit 转成 /lib/firmware/conv_front_int8_20261021.bin 再写进 PL，
这个文件 2026-10-21 上板时已经生成。下载前后都核对它的 SHA256 必须是验过的那份：
下载前已经是别的内容就不下载（不覆盖），下载后变了就报错。内容不变，等于没改。
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import warnings

EXIT_OK, EXIT_ERROR, EXIT_CONFIG = 0, 1, 3
PINNED = {
    "bit": "8ab0f411cc3ddee74045a753b379d265a121871dc656a53ef25b7c2edfe8da24",
    "hwh": "c2b5c8138087c8e398d509df40e1c854311ebcca249f7a0e232c107d8db6d067",
}
IP_NAME = "conv_front_int8_0"
CTRL_ADDR = 0xA0000000
KNOWN_WARNING = "_current_metadata.pkl"  # PYNQ 3.0.1 gen_cache 未关闭文件的 ResourceWarning，无害
MARKER = Path("/run/kv260car/pl_loaded.json")
BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")
FIRMWARE_BIN = Path("/lib/firmware/conv_front_int8_20261021.bin")
FIRMWARE_SHA256 = "6f67e30387f59f5def6bcf1385607549b5a7dc25f2d0d3cbbcba397a24ec7996"  # 2026-10-21 板上实测


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def say(kind, **data):
    print(json.dumps({"utc": datetime.now(timezone.utc).isoformat(), "kind": kind, **data},
                     ensure_ascii=False, default=str), flush=True)


def clock_snapshot(clocks):
    inst = clocks._instance
    return {"fclk0_mhz": float(clocks.fclk0_mhz), "cpu_mhz": float(clocks.cpu_mhz),
            "pl_raw": [int(r[:]) for r in inst.PL_CLK_CTRLS]}


def write_marker(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    cli.add_argument("--bit", type=Path, required=True)
    cli.add_argument("--marker", type=Path, default=MARKER)
    a = cli.parse_args(argv)
    hwh = a.bit.with_suffix(".hwh")
    boot_id = BOOT_ID_PATH.read_text(encoding="utf-8").strip()
    say("start", bit=str(a.bit), boot_id=boot_id)

    try:
        got = {"bit": sha256(a.bit), "hwh": sha256(hwh)}
    except OSError as error:
        say("config_error", error=f"{type(error).__name__}: {error}")
        return EXIT_CONFIG
    if got != PINNED:
        say("config_error", error="bit/hwh sha256 mismatch; not downloading", got=got, expected=PINNED)
        return EXIT_CONFIG
    if os.geteuid() != 0:
        say("config_error", error="needs root")
        return EXIT_CONFIG
    if os.environ.get("XILINX_XRT") != "/usr" or "XCL_EMULATION_MODE" in os.environ:
        say("config_error", error="XILINX_XRT=/usr required, and no XCL_EMULATION_MODE")
        return EXIT_CONFIG

    from pynq import Bitstream, Clocks, Overlay
    from pynq.pl_server import global_state
    state_path = Path(global_state.STATE_DIR) / "global_pl_state.json"
    had_state = state_path.exists()
    global_state.initial_global_state_file_boot_check()
    say("pynq_state", existed_before=had_state, exists_after_boot_check=state_path.exists())

    def loaded_sha():
        try:
            loaded = json.loads(state_path.read_text(encoding="utf-8")).get("bitfile_name")
            return sha256(loaded) if loaded else None
        except (OSError, ValueError):
            return None

    try:
        old = json.loads(a.marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = None
    if old and old.get("boot_id") == boot_id and loaded_sha() == PINNED["bit"]:
        say("skip", reason="already downloaded this boot")
        return EXIT_OK

    before = clock_snapshot(Clocks)
    say("clocks_before", **before)
    if abs(before["fclk0_mhz"] - 100.0) > 0.5:
        say("error", error="PL0 is not ~100 MHz; not downloading")
        return EXIT_ERROR
    overlay = Overlay(str(a.bit), download=False)
    if overlay.dtbo is not None or overlay.partial:
        say("error", error="unexpected dtbo or partial image")
        return EXIT_ERROR
    if overlay.ip_dict[IP_NAME]["phys_addr"] != CTRL_ADDR:
        say("error", error="control address mismatch")
        return EXIT_ERROR

    fw_before = sha256(FIRMWARE_BIN) if FIRMWARE_BIN.exists() else None
    if fw_before not in (None, FIRMWARE_SHA256):
        say("error", error="existing firmware bin is not the verified one; not overwriting", got=fw_before)
        return EXIT_ERROR

    t0 = time.monotonic()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        Bitstream.download(overlay, overlay.parser)
    seconds = time.monotonic() - t0
    messages = [str(w.message) for w in caught]
    unknown = [m for m in messages if KNOWN_WARNING not in m]
    say("downloaded", seconds=round(seconds, 3), warnings=messages)
    if unknown:
        say("error", error="unreviewed programming warning", warnings=unknown)
        return EXIT_ERROR
    if loaded_sha() != PINNED["bit"]:
        say("error", error="PYNQ state does not name the pinned bit after download")
        return EXIT_ERROR
    fw_after = sha256(FIRMWARE_BIN) if FIRMWARE_BIN.exists() else None
    if fw_after != FIRMWARE_SHA256:
        say("error", error="firmware bin after download is not the verified one", before=fw_before, after=fw_after)
        return EXIT_ERROR
    after = clock_snapshot(Clocks)
    say("clocks_after", **after)
    if after["pl_raw"] != before["pl_raw"] or abs(after["fclk0_mhz"] - 100.0) > 0.5:
        say("error", error="PL clock registers changed or PL0 not ~100 MHz")
        return EXIT_ERROR
    ctrl = int(getattr(overlay, IP_NAME).read(0x00))  # 只读控制寄存器，不启动核
    if not ctrl & 4 or ctrl & 0x81:
        say("error", error="kernel not idle after download", ctrl=hex(ctrl))
        return EXIT_ERROR

    write_marker(a.marker, {"boot_id": boot_id, "bit": str(a.bit), "bit_sha256": PINNED["bit"],
                            "hwh_sha256": PINNED["hwh"], "fclk0_mhz": after["fclk0_mhz"],
                            "download_s": round(seconds, 3), "ctrl": ctrl,
                            "firmware_sha256_before": fw_before, "firmware_sha256_after": fw_after,
                            "utc": datetime.now(timezone.utc).isoformat()})
    say("ok", marker=str(a.marker))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
