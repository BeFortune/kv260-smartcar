#!/bin/bash
# 在 KV260 上安装一个已解开的版本目录。用法（在解开的版本目录里）：sudo bash tools/car/install.sh
# 做的事都可撤销：
#   - 把本目录复制到 /opt/kv260car/<版本>/，/opt/kv260car/current 指向它（旧版本保留，可切回）
#   - 核对 MANIFEST.json 里每个文件的 SHA256
#   - 装三个 unit 到 /etc/systemd/system/，daemon-reload；装 /usr/local/bin/carctl
# 不会做：enable 开机自启、启动服务、下载 bit、重启。开机自启要另外执行 `carctl enable`。
# 撤销：sudo carctl disable; sudo rm /etc/systemd/system/car-{main,pl-load,xpad-bind}.service \
#       /usr/local/bin/carctl; sudo systemctl daemon-reload; sudo rm -r /opt/kv260car
set -euo pipefail
SRC=$(cd "$(dirname "$0")/../.." && pwd)
VERSION=$(basename "$SRC")
# 版本名只允许 make_bundle.py 生成的格式，防止奇怪的目录名进到下面的 rm -rf / mv
[[ "$VERSION" =~ ^kv260car_[0-9]{8}_[0-9]{6}$ ]] || { echo "unexpected version dir name: $VERSION"; exit 1; }
DEST=/opt/kv260car/$VERSION
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
[ -f "$SRC/MANIFEST.json" ] || { echo "no MANIFEST.json in $SRC"; exit 1; }

/usr/local/share/pynq-venv/bin/python3 - "$SRC" <<'PY'
import hashlib, json, sys
from pathlib import Path
root = Path(sys.argv[1])
m = json.loads((root / "MANIFEST.json").read_text(encoding="utf-8"))
bad = [r for r, e in m["files"].items()
       if hashlib.sha256((root / r).read_bytes()).hexdigest() != e["sha256"]]
if bad:
    sys.exit(f"sha256 mismatch: {bad}")
print(f"manifest ok: {len(m['files'])} files, version {m['version']}")
PY

mkdir -p /opt/kv260car
if [ "$SRC" != "$DEST" ]; then
  rm -rf "$DEST.tmp"
  cp -a "$SRC" "$DEST.tmp"
  rm -rf "$DEST"
  mv "$DEST.tmp" "$DEST"
fi
chown -R root:root "$DEST"
ln -sfn "$DEST" /opt/kv260car/current.new
mv -T /opt/kv260car/current.new /opt/kv260car/current

for unit in car-main car-pl-load car-xpad-bind; do
  install -m 0644 "$DEST/tools/car/systemd/$unit.service" "/etc/systemd/system/$unit.service"
done
install -m 0755 "$DEST/tools/car/carctl.sh" /usr/local/bin/carctl
mkdir -p /home/ubuntu/car_runs
chown ubuntu:ubuntu /home/ubuntu/car_runs
systemctl daemon-reload
echo "installed $VERSION -> /opt/kv260car/current"
echo "units installed but NOT enabled; enable with: sudo carctl enable"
systemctl --no-pager is-enabled car-main car-pl-load car-xpad-bind || true
