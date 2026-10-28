"""实时监看小车上位机的输出速度。只读日志文件，不碰相机、串口、PL，也不给车发任何指令。

数据来源：
  <run>/frames.csv    每帧推理一行，t_mono 是 time.monotonic 秒。用它算真实帧率和各段耗时。
  <run>/events.jsonl  状态变化、heartbeat（累计 frames_sent）、停车帧。

为什么用 t_mono 而不是文件写入时间：FrameLog 每 15 行才 flush 一次，落到磁盘有时间抖动；
t_mono 是每帧真实时间戳，算出来的频率不受缓冲影响。

用法：
  python3 carwatch.py                  # 每秒刷新一行，按最近 5 秒算频率
  python3 carwatch.py --interval 2 --window 10
  python3 carwatch.py --once           # 打一次汇总就退出，适合留证据
  python3 carwatch.py --run /home/ubuntu/car_runs/run_...

频率口径：
  窗口Hz  最近 window 秒内的频率（当前速度）
  总Hz    本次运行从头到最新一帧的平均频率
  串口累计 实际写成功的累计帧数（来自 heartbeat）；未发出 = 推理了但没发出的帧数
"""
import argparse
import csv
import json
import sys
import time
from collections import deque
from pathlib import Path

DEFAULT_RUNS = Path("/home/ubuntu/car_runs")


def boot_prefix():
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()[:8] or None
    except OSError:
        return None


def pick_run(runs_dir, explicit=None):
    """优先本次开机的目录（板子没有实时时钟，按修改时间会挑到上一次开机的），否则按修改时间取最新。"""
    if explicit:
        return Path(explicit)
    runs_dir = Path(runs_dir)
    if not runs_dir.is_dir():
        return None
    runs = [p for p in runs_dir.iterdir() if p.is_dir() and p.name.startswith("run_")]
    if not runs:
        return None
    boot = boot_prefix()
    if boot:
        same_boot = [p for p in runs if boot in p.name.split("_")]
        if same_boot:
            runs = same_boot
    return max(runs, key=lambda p: p.stat().st_mtime)


def number(row, key):
    try:
        return float(row[key])
    except (KeyError, TypeError, ValueError):
        return None


class FrameReader:
    """增量读 frames.csv。只处理以换行结尾的完整行，留下半行等下次。"""

    def __init__(self, path, keep=8192):
        self.path = Path(path)
        self.offset = 0
        self.partial = b""
        self.header = None
        self.recent = deque(maxlen=keep)
        self.count = 0
        self.first_t = None
        self.last_t = None
        self.last_row = None

    def _consume(self, text):
        if not text.strip():
            return
        if self.header is None:
            self.header = next(csv.reader([text]))
            return
        row = dict(zip(self.header, next(csv.reader([text]))))
        t = number(row, "t_mono")
        if t is None:
            return
        self.recent.append(row)
        self.count += 1
        self.last_row = row
        if self.first_t is None:
            self.first_t = t
        self.last_t = t

    def poll(self):
        try:
            size = self.path.stat().st_size
        except OSError:
            return
        if size < self.offset:          # 文件被换掉或截断：从头重来
            self.offset, self.partial, self.header = 0, b"", None
            self.recent.clear()
            self.count, self.first_t, self.last_t, self.last_row = 0, None, None, None
        if size == self.offset:
            return
        with self.path.open("rb") as stream:
            stream.seek(self.offset)
            data = stream.read(size - self.offset)
        self.offset = size
        lines = (self.partial + data).split(b"\n")
        self.partial = lines.pop()
        for line in lines:
            self._consume(line.decode("utf-8", "replace"))

    def window(self, seconds):
        if self.last_t is None:
            return []
        floor = self.last_t - seconds
        return [row for row in self.recent if (number(row, "t_mono") or 0) >= floor]


class EventReader:
    """events.jsonl 很小，每次只读尾部 64 KB，够拿最后的状态和 heartbeat。"""

    def __init__(self, path):
        self.path = Path(path)
        self.state = None
        self.heartbeat = None

    def poll(self):
        try:
            raw = self.path.read_bytes()[-65536:]
        except OSError:
            return
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue                  # 被截断的第一行
            kind = record.get("kind")
            if kind == "heartbeat":
                self.heartbeat = record
            elif kind == "state":
                self.state = record.get("new")


def stats(rows, key):
    values = [number(row, key) for row in rows]
    values = [v for v in values if v is not None]
    if not values:
        return None
    values.sort()
    return {"n": len(values), "mean": sum(values) / len(values),
            "p95": values[min(len(values) - 1, int(0.95 * (len(values) - 1)))],
            "max": values[-1]}


def rate(rows):
    if len(rows) < 2:
        return None
    first, last = number(rows[0], "t_mono"), number(rows[-1], "t_mono")
    if first is None or last is None or last <= first:
        return None
    return (len(rows) - 1) / (last - first)


def report(frames, events, window_s):
    win = frames.window(window_s)
    overall = None
    if frames.count > 1 and frames.first_t is not None and frames.last_t > frames.first_t:
        overall = (frames.count - 1) / (frames.last_t - frames.first_t)
    ages = [number(row, f"age_{role}_ms") for row in win for role in ("left", "front", "right")]
    ages = [a for a in ages if a is not None]
    last = frames.last_row or {}
    heartbeat = events.heartbeat or {}
    sent, logged = heartbeat.get("frames_sent"), heartbeat.get("frames_logged")
    return {
        "elapsed_s": 0.0 if frames.first_t is None else frames.last_t - frames.first_t,
        "state": events.state or "(无状态)",
        "win_hz": rate(win),
        "all_hz": overall,
        "frames": frames.count,
        "infer": stats(win, "infer_total_ms"),
        "kernel": stats(win, "kernel_ms"),
        "pre": stats(win, "pre_ms"),
        "dequant": stats(win, "dequant_ms"),
        "partb": stats(win, "partb_ms"),
        "grab": stats(win, "grab_decode_ms"),
        "age_max_ms": max(ages) if ages else None,
        "sent": sent,
        "behind": None if sent is None or logged is None else logged - sent,
        "rejected": heartbeat.get("frames_rejected"),
        "stops": heartbeat.get("stop_frames"),
        "speed_left": number(last, "speed_left"),
        "speed_right": number(last, "speed_right"),
    }


ROW = "{:>8}  {:<10} {:>8} {:>8}  {:>21} {:>6} {:>8} {:>8} {:>6}  {:>7} / {:>7}"
HEADER = ROW.format("已跑s", "状态", "窗口Hz", "总Hz", "推理ms 均值/P95/最大", "PL核ms",
                    "帧龄max", "串口累计", "未发出", "左速度", "右速度")


def col(value, spec, width):
    return " " * width if value is None else format(value, spec).rjust(width)


def line(data):
    infer = data["infer"]
    if infer:
        infer_text = "{:6.2f}/{:6.2f}/{:6.2f}".format(infer["mean"], infer["p95"], infer["max"])
    else:
        infer_text = "    --/    --/    --"
    kernel = data["kernel"]["mean"] if data["kernel"] else None
    return ROW.format(format(data["elapsed_s"], "8.1f"), data["state"][:10],
                      col(data["win_hz"], "6.2f", 8), col(data["all_hz"], "6.2f", 8),
                      infer_text, col(kernel, "6.2f", 6),
                      col(data["age_max_ms"], "6.1f", 8),
                      col(data["sent"], "d", 8), col(data["behind"], "d", 6),
                      col(data["speed_left"], "7.2f", 7), col(data["speed_right"], "7.2f", 7))


def summary(data, window_s):
    blocks = [HEADER, line(data)]
    parts = []
    for key, label in (("pre", "预处理"), ("kernel", "PL核"), ("dequant", "反量化"),
                       ("partb", "PS后半段"), ("infer", "推理总计"), ("grab", "取帧解码")):
        block = data[key]
        if block:
            parts.append("{} {:.2f}/{:.2f}/{:.2f} ms".format(label, block["mean"], block["p95"], block["max"]))
    if parts:
        blocks.append("  分段耗时 均值/P95/最大：" + "；".join(parts))
    if data["sent"] is not None:
        blocks.append("  串口累计成功 {} 帧，推理 {} 帧，差 {} 帧；停车帧 {} 次，编码被拒 {} 帧".format(
            data["sent"], data["frames"], data["behind"], data["stops"], data["rejected"]))
    blocks.append("  窗口 {:.1f}s 内的频率：{}".format(
        window_s, "无数据" if data["win_hz"] is None else "{:.2f} Hz".format(data["win_hz"])))
    return "\n".join(blocks)


def main(argv=None):
    parser = argparse.ArgumentParser(description="实时监看小车上位机输出速度（只读）")
    parser.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS)
    parser.add_argument("--run", type=Path, help="指定运行目录，默认自动挑本次开机的目录")
    parser.add_argument("--interval", type=float, default=1.0, help="刷新间隔秒，默认 1")
    parser.add_argument("--window", type=float, default=5.0, help="算频率用的滑动窗口秒，默认 5")
    parser.add_argument("--once", action="store_true", help="只打一次汇总就退出")
    args = parser.parse_args(argv)

    frames, events, current_run = None, None, None
    try:
        while True:
            run_dir = pick_run(args.runs_dir, args.run)
            if run_dir is None:
                print("找不到运行目录：{}（car-main 还没建，或路径不对）".format(args.runs_dir),
                      file=sys.stderr, flush=True)
                if args.once:
                    return 2
                time.sleep(args.interval)
                continue
            if run_dir != current_run:
                current_run = run_dir
                frames = FrameReader(run_dir / "frames.csv")
                events = EventReader(run_dir / "events.jsonl")
                print("# 监看 {}   窗口 {:.1f}s   刷新 {:.1f}s   (Ctrl+C 退出)".format(
                    run_dir, args.window, args.interval), flush=True)
                if not args.once:
                    print(HEADER, flush=True)
            frames.poll()
            events.poll()
            data = report(frames, events, args.window)
            print(summary(data, args.window) if args.once else line(data), flush=True)
            if args.once:
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
