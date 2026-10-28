"""KV260 小车主程序。开机由 car-main.service 启动，也可以手动运行。

两个推理后端，用 --backend 或 /etc/kv260car/backend 选（命令行优先，文件里写 pl 或 cpu，默认 pl）：
  pl   PL 跑 conv0-2 + ORT 跑后半段（当前默认，出事故前的正式方案）
  cpu  原模型整图走 ORT CPU（NUC 原方案，用于对照和兜底），不碰 PL、不等 pl_loaded.json

流程：
  1 读口位、手柄配置，核对模型文件哈希。后端是 pl 时校验 bit/ref/params/partb，
    是 cpu 时校验原模型 model_current.onnx。哈希不对退出码 3（systemd 不重启）。
  2（仅 pl）等 car-pl-load.service 在这次开机里把已验过的 bit 下载好：看 /run/kv260car/pl_loaded.json
    里的 boot_id 是不是本次开机。没好就一直等，不碰 PL、不开相机和串口、不发任何字节。
  3（仅 pl）attach_loaded_pl（download=False），再做 PL 自检：三张合成图走同一个查表预处理，
    PL 输出和整数参考逐字节比较。不一致退出码 3。
     （cpu）直接建 ORT session，不下载、不 attach、不写任何 PL 寄存器。
  4 car_loop：等设备（手柄 -> 相机口位 -> 串口 -> 三路出帧）-> 等手柄 Back 启动 -> 跑。
    可选 --roadsign-config：PS 对前视帧做 YOLO，再在推理后、编码前仲裁停车/限速/恢复；
    检测过期或异常走 supervisor_error 故障路径。未提供配置时不加载 YOLO，原路径不变。
    Back 再按一次停车回等待；右摇杆按下急停；手柄断开、相机/串口/推理出错都补停车帧。
  5 收到 SIGTERM/SIGINT（systemctl stop、Ctrl+C）：这一轮结束后补停车帧再退出。第二次信号立刻中断。
日志：每次启动建一个目录 <runs-dir>/run_<UTC 时间>_<boot_id 前8位>/：
  普通运行只保留最近 --keep-runs 个，包含 snapshots 的运行不自动删除。
  events.jsonl  状态变化、错误、停车帧，以及每 60 秒一次心跳（帧数、相机、手柄、串口统计）
  frames.csv    每次推理一行：三路帧龄、取帧+解码、推理各段耗时、左右速度
  --save-frames 默认关闭；开启后异步保存实际推理图片、时间戳、预测和可复算张量，按帧间隔/数量/容量限制。
  --dry-run 不创建串口或手柄，只做相机推理；--seconds 可限制诊断循环时长。真实相机仍会开启。
PL 核超时卡死：补停车帧后进程停住不退出（硬件可能还在访问缓冲，不能释放）。这时要重启板子。
"""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import sys
import time
import traceback

import numpy as np

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
for _candidate in (HERE, *HERE.parents):
    if (_candidate / "ps" / "pl_infer.py").is_file():
        sys.path.insert(0, str(_candidate))
        break

from ps import cpu_infer  # noqa: E402
from ps import pl_infer  # noqa: E402
from ps.camera_ports import load_camera_config, verify_on_board  # noqa: E402
from ps.capture import TriCamera, resolver_from_config  # noqa: E402
from ps.car_loop import CarLoop, EventLog, FatalError  # noqa: E402
from ps.devices import DeviceCheck  # noqa: E402
from ps.joystick import Joystick, load_joystick_config  # noqa: E402
from ps.serial_link import SerialLink  # noqa: E402
from ps.serial_port import load_serial_config, resolve_on_board  # noqa: E402
from ps.roadsign_supervisor import load_roadsign_config, make_roadsign_supervisor  # noqa: E402

MARKER = Path("/run/kv260car/pl_loaded.json")
EXIT_OK, EXIT_ERROR, EXIT_CONFIG = 0, 1, 3
AUTO_START_FLAG = Path("/etc/kv260car/auto_start")  # 临时开关：文件存在就不用手柄、上电直接开跑
BACKEND_FLAG = Path("/etc/kv260car/backend")        # 内容 pl 或 cpu；命令行 --backend 优先
DEFAULT_CPU_MODEL = Path("/home/ubuntu/kv260_v1_pynq_20261011/model/model_current.onnx")
MODEL_FILES = {
    "bit": "conv_front_int8_20261021.bit",
    "ref": "ref_int8.py",
    "params": "params.json",
    "partb": "s16_j1c2_cut2int8_ps.onnx",
}
FRAME_FIELDS = ("t_mono", "grab_decode_ms", "age_left_ms", "age_front_ms", "age_right_ms",
                "pre_ms", "kernel_ms", "dequant_ms", "partb_ms", "infer_total_ms",
                "speed_left", "speed_right", "serial_enabled", "frame_id",
                "grabbed_left_mono", "grabbed_front_mono", "grabbed_right_mono",
                "host_frame_skew_ms", "snapshot_queued")


def fmt_error(error):
    return f"{type(error).__name__}: {error}"


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


class StopFlag:
    """信号处理：第一次只置标志，让 car_loop 在这一轮结束后补停车帧退出；第二次立刻中断。"""

    def __init__(self):
        self.reason = None

    def __call__(self, signum, frame):
        if self.reason is not None:
            raise KeyboardInterrupt(f"second signal {signum}")
        self.reason = signal.Signals(signum).name

    def __bool__(self):
        return self.reason is not None


def make_run_dir(runs_dir, boot_id, keep):
    runs_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = runs_dir / f"run_{stamp}_{boot_id[:8]}"
    suffix = 1
    while run_dir.exists():
        suffix += 1
        run_dir = runs_dir / f"run_{stamp}_{boot_id[:8]}_{suffix}"
    run_dir.mkdir()
    prune_runs(runs_dir, keep, current=run_dir)
    return run_dir


def prune_runs(runs_dir, keep, current=None):
    """保留最近 keep 个普通日志目录；含存图数据的运行须人工归档，不自动删除。"""
    runs = sorted((p for p in runs_dir.iterdir() if p.is_dir() and p.name.startswith("run_")
                   and not (p / "snapshots").exists()),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    kept = 0
    for path in runs:
        if path == current or kept < keep:
            kept += 1
            continue
        shutil.rmtree(path, ignore_errors=True)


def marker_problem(marker_path, boot_id):
    """None 表示这次开机已经由 car-pl-load 下载并核对过钉死的 bit。"""
    try:
        data = json.loads(Path(marker_path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "car-pl-load has not confirmed the bit this boot (no marker)"
    except (OSError, ValueError) as error:
        return f"marker unreadable: {fmt_error(error)}"
    if data.get("boot_id") != boot_id:
        return f"marker is from another boot ({data.get('boot_id')})"
    if data.get("bit_sha256") != pl_infer.PINNED_SHA256["bit"]:
        return "marker names a different bit"
    return None


def wait_for_pl(marker_path, boot_id, log, stop, poll_s=2.0, sleep=time.sleep):
    """等到标记文件对上本次开机。stop 置位就返回 None。同一个原因只记一次。"""
    last = None
    while not stop:
        problem = marker_problem(marker_path, boot_id)
        if problem is None:
            return json.loads(Path(marker_path).read_text(encoding="utf-8"))
        if problem != last:
            log("pl_not_ready", problem=problem)
            last = problem
        sleep(poll_s)
    return None


def self_test_images(seed=20261022):
    """三张确定的 640x480 图：横向渐变、纵向渐变、固定种子的噪声。覆盖 0-255 全部像素值。"""
    h, w = 480, 640
    left = np.broadcast_to(np.linspace(0, 255, w, dtype=np.float32)[None, :, None], (h, w, 3))
    front = np.broadcast_to(np.linspace(0, 255, h, dtype=np.float32)[:, None, None], (h, w, 3))
    right = np.random.default_rng(seed).integers(0, 256, size=(h, w, 3), dtype=np.uint8)
    return [np.ascontiguousarray(np.rint(left).astype(np.uint8)),
            np.ascontiguousarray(np.rint(front).astype(np.uint8)),
            np.ascontiguousarray(right)]


def pl_self_test(kernel, ref, params):
    """PL 输出对整数参考逐字节比较。返回 (不同的字节数, 总字节数)。核超时抛 KernelHung。"""
    pl_infer.pre_lut(self_test_images(), pl_infer.build_input_lut(), kernel.inbuf)
    try:
        out = np.array(kernel.run(), copy=True)
    except TimeoutError as error:
        raise pl_infer.KernelHung(str(error)) from error
    expect = ref.run(np.array(kernel.inbuf).reshape(pl_infer.IN_SHAPE), params).reshape(-1)
    return int(np.count_nonzero(out != expect)), int(out.size)


class DiagnosticOutput:
    """诊断时丢弃所有控制报文，包括退出时的停车帧；不持有串口。"""

    def __init__(self):
        self.opens = 0
        self.packets_discarded = 0

    def write(self, data):
        self.packets_discarded += 1
        return len(data)

    def close(self):
        pass


class DiagnosticEvents:
    """沿用主循环验证编码，但日志中绝不将内存丢弃计作串口发送。"""

    def __init__(self, log):
        self.log = log

    def __call__(self, kind, **data):
        data["serial_enabled"] = False
        if "frames_sent" in data:
            data["diagnostic_packets"] = data.pop("frames_sent")
            data["frames_sent"] = 0
        if "stop_frames" in data:
            data["diagnostic_stop_packets"] = data.pop("stop_frames")
            data["stop_frames"] = 0
        if kind in ("stop_frame", "stop_frame_failed"):
            kind = "diagnostic_" + kind
        self.log(kind, **data)


class CameraOnlyCheck:
    def __init__(self, verify, cameras):
        self.verify, self.cameras = verify, cameras

    def check(self):
        try:
            self.verify()
            return self.cameras.ensure_running()
        except Exception as error:
            return f"camera ports: {error}"


class FrameLog:
    def __init__(self, path, flush_every=15):
        self.stream = open(path, "x", encoding="utf-8", newline="")
        self.writer = csv.DictWriter(self.stream, fieldnames=FRAME_FIELDS, extrasaction="ignore")
        self.writer.writeheader()
        self.rows, self.flush_every = 0, flush_every

    def write(self, row):
        self.writer.writerow(row)
        self.rows += 1
        if self.rows % self.flush_every == 0:
            self.stream.flush()

    def close(self):
        self.stream.flush()
        self.stream.close()


class TimedCameras:
    """包住 cameras.latest()，记下取帧+解码耗时和三路帧龄，留给 TimedInfer 写进同一行。"""

    def __init__(self, inner, clock):
        self.inner, self.clock = inner, clock
        self.pending = {}

    def latest(self):
        t0 = self.clock()
        frames = self.inner.latest()
        t1 = self.clock()
        self.pending = {"t_mono": t1, "grab_decode_ms": (t1 - t0) * 1e3,
                        **{f"age_{role}_ms": (t1 - grabbed) * 1e3
                           for role, (_, grabbed) in zip(("left", "front", "right"), frames)},
                        **{f"grabbed_{role}_mono": grabbed
                           for role, (_, grabbed) in zip(("left", "front", "right"), frames)},
                        "host_frame_skew_ms": (max(g for _, g in frames) - min(g for _, g in frames)) * 1e3}
        return frames


class TimedInfer:
    def __init__(self, inner, cams, frames, serial_enabled=True, capture=None):
        self.inner, self.cams, self.frames = inner, cams, frames
        self.serial_enabled, self.capture = serial_enabled, capture
        self.frame_id = 0

    def __call__(self, left, front, right):
        speeds = self.inner(left, front, right)
        self.frame_id += 1
        row = dict(self.cams.pending)
        ms = self.inner.last_ms
        row.update({"pre_ms": ms.get("pre"), "kernel_ms": ms.get("kernel"), "dequant_ms": ms.get("dequant"),
                    "partb_ms": ms.get("partb"), "infer_total_ms": ms.get("total"),
                    "speed_left": speeds[0], "speed_right": speeds[1],
                    "serial_enabled": self.serial_enabled, "frame_id": self.frame_id,
                    "snapshot_queued": False})
        if self.capture is not None and self.capture.wants(self.frame_id):
            tensors = {}
            kernel = getattr(self.inner, "kernel", None)
            if kernel is not None:
                # 核缓冲下次推理会复用；submit 必须在本轮返回前复制。
                tensors = {"pl_input_int8_actual": kernel.inbuf,
                           "pl_output_int8_actual": kernel.outbuf}
            metadata = {**row, "snapshot_queued": True}
            row["snapshot_queued"] = self.capture.submit(self.frame_id, (left, front, right), metadata,
                                                         tensors=tensors)
        self.frames.write(row)
        return speeds


class Hardware:
    """真实设备。测试里换成假的。"""

    marker = MARKER

    def __init__(self):
        self.clock, self.sleep = time.monotonic, time.sleep

    def boot_id(self):
        return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()

    def wait_for_pl(self, log, stop):
        return wait_for_pl(self.marker, self.boot_id(), log, stop, sleep=self.sleep)

    def attach(self, bit_path):
        return pl_infer.attach_loaded_pl(bit_path)

    def make_cams(self, config):
        return TriCamera(resolver_from_config(config))

    def make_link(self, line):
        return SerialLink(line)

    def make_js(self, config):
        return Joystick(config)

    def verify_cams(self, config):
        return verify_on_board(config)

    def resolve_serial(self, config):
        return resolve_on_board(config)

    def park(self):
        while True:
            time.sleep(3600)


def model_paths(models_dir):
    return {name: Path(models_dir) / filename for name, filename in MODEL_FILES.items()}


def close_parts(parts, log):
    for name in ("cams", "link", "js", "capture", "frames"):
        part = parts.pop(name, None)
        if part is None:
            continue
        try:
            part.close()
            if name == "capture":
                log("frame_capture_closed", **part.stats())
        except Exception as error:  # noqa: BLE001
            log("close_error", part=name, error=fmt_error(error))


def run(args, hw, stop):
    boot_id = hw.boot_id()
    run_dir = make_run_dir(Path(args.runs_dir), boot_id, args.keep_runs)
    log = EventLog(run_dir / "events.jsonl")
    parts = {}
    try:
        return _run(args, hw, stop, boot_id, run_dir, log, parts)
    except FatalError as error:
        kernel = parts.get("kernel")
        log("fatal", error=fmt_error(error), kernel_hung=getattr(kernel, "hung", None))
        close_parts(parts, log)
        if kernel is not None and kernel.hung:
            log("parked", note="PL kernel hung; buffers held; reboot the board")
            hw.park()
        return EXIT_ERROR
    except KeyboardInterrupt as error:
        log("interrupted", error=fmt_error(error))
        return EXIT_OK
    except Exception as error:  # noqa: BLE001
        log("crash", error=fmt_error(error), traceback=traceback.format_exc())
        return EXIT_ERROR
    finally:
        close_parts(parts, log)
        log("exit", run_dir=str(run_dir))
        log.close()


def _run(args, hw, stop, boot_id, run_dir, log, parts):
    if args.dry_run:
        log = DiagnosticEvents(log)
    log("start", pid=os.getpid(), boot_id=boot_id, run_dir=str(run_dir), backend=args.backend,
        self_sha256=file_sha256(__file__), args={k: str(v) for k, v in vars(args).items()})
    if args.seconds is not None and not args.dry_run:
        log("config_error", error="--seconds is only supported with --dry-run")
        return EXIT_CONFIG
    models = None
    kernel = None
    try:
        cam_config = load_camera_config(args.camera_config)
        roadsign_config = load_roadsign_config(args.roadsign_config) if args.roadsign_config else None
        serial_config = None if args.dry_run else load_serial_config(args.serial_config)
        js_config = None if args.dry_run else load_joystick_config(args.joystick_config)
        if args.backend == "pl":
            models = model_paths(args.models)
            for name, path in models.items():
                pl_infer.check_pinned(name, path)
            ref = pl_infer.load_module("ref_int8", models["ref"])
            params = json.loads(models["params"].read_text(encoding="utf-8"))
            pl_infer.check_params(params)
        else:
            cpu_infer.check_model(args.model)
    except Exception as error:  # noqa: BLE001
        log("config_error", error=fmt_error(error))
        return EXIT_CONFIG
    log("config_ok", backend=args.backend, cameras=[(c["role"], c["port"]) for c in cam_config["cameras"]],
        serial=None if serial_config is None else serial_config["port"],
        joystick=None if js_config is None else js_config["port"],
        toggle_button=None if js_config is None else js_config["toggle_button"],
        stop_button=None if js_config is None else js_config["stop_button"],
        serial_enabled=not args.dry_run)
    if roadsign_config is not None:
        log("roadsign_config", model=str(roadsign_config.model_path), sha256=roadsign_config.sha256,
            max_result_age_s=roadsign_config.max_result_age_s, threads=roadsign_config.threads)
    try:
        supervisor = (make_roadsign_supervisor(roadsign_config, clock=hw.clock)
                      if roadsign_config is not None else None)
    except Exception as error:  # noqa: BLE001
        log("config_error", error=fmt_error(error))
        return EXIT_CONFIG

    if args.backend == "pl":
        marker = hw.wait_for_pl(log, stop)
        if marker is None:
            log("stopped", signal=stop.reason, where="waiting for PL")
            return EXIT_OK
        log("pl_marker", **marker)

        kernel = parts["kernel"] = hw.attach(models["bit"])
        bad, total = pl_self_test(kernel, ref, params)
        log("pl_self_test", mismatch_bytes=bad, bytes=total)
        if bad:
            return EXIT_CONFIG
        infer = pl_infer.RouteBInfer(kernel, models["partb"])
    else:
        log("cpu_backend", model=str(args.model), sha256=cpu_infer.MODEL_SHA256, threads=args.threads)
        infer = cpu_infer.CpuOriginalInfer(args.model, args.threads)

    cams = parts["cams"] = hw.make_cams(cam_config)
    link = parts["link"] = DiagnosticOutput() if args.dry_run else hw.make_link(serial_config["line"])
    frames = parts["frames"] = FrameLog(run_dir / "frames.csv")
    if args.dry_run:
        js = None
        log("dry_run", serial_enabled=False, note="inference only; no serial or joystick opened")
        devices = CameraOnlyCheck(lambda: hw.verify_cams(cam_config), cams)
    else:
        if args.auto_start:
            js = None
            log("auto_start", note="joystick disabled; car starts as soon as cameras + serial are ready")
        else:
            js = parts["js"] = hw.make_js(js_config)
        devices = DeviceCheck(lambda: hw.verify_cams(cam_config), lambda: hw.resolve_serial(serial_config),
                              cams, link, joystick=js)
    capture = None
    if args.save_frames:
        from ps.frame_capture import FrameCapture
        context = {"backend": args.backend, "serial_enabled": not args.dry_run,
                   "camera_order": [c["role"] for c in cam_config["cameras"]],
                   "camera_ports": cam_config["cameras"], "boot_id": boot_id,
                   "model_sha256": dict(pl_infer.PINNED_SHA256) if args.backend == "pl"
                   else {"original_cpu": cpu_infer.MODEL_SHA256},
                   "source_sha256": file_sha256(__file__),
                   "roadsign_model_sha256": None if roadsign_config is None else roadsign_config.sha256,
                   "timestamp_scope": "host cap.read return, not exposure time; reused images retain original time"}
        capture = parts["capture"] = FrameCapture(run_dir / "snapshots", every=args.save_every,
                                                   max_samples=args.save_max,
                                                   max_bytes=args.save_max_mib * 1024 * 1024, context=context)
        log("frame_capture_enabled", every=args.save_every, max_samples=args.save_max,
            max_bytes=args.save_max_mib * 1024 * 1024, format="lossless PNG", path=str(run_dir / "snapshots"))
    timed_cams = TimedCameras(cams, hw.clock)
    timed_infer = TimedInfer(infer, timed_cams, frames, serial_enabled=not args.dry_run, capture=capture)
    if js is None:
        loop = CarLoop(devices, timed_cams, timed_infer, link, lambda: True, log,
                       clock=hw.clock, sleep=hw.sleep, supervisor=supervisor)
    else:
        loop = CarLoop(devices, timed_cams, timed_infer, link, js.start_trigger, log,
                       clock=hw.clock, sleep=hw.sleep, stop_request=js.stop_request, on_wait_start=js.clear,
                       supervisor=supervisor)

    next_beat = [hw.clock() + args.heartbeat_s]
    deadline = None if args.seconds is None else hw.clock() + args.seconds

    def should_stop():
        now = hw.clock()
        if deadline is not None and now >= deadline and not stop:
            stop.reason = "duration reached"
        if now >= next_beat[0]:
            next_beat[0] = now + args.heartbeat_s
            log("heartbeat", state=loop.state, **loop.stats, frames_logged=frames.rows,
                cameras=cams.stats(), serial_opens=link.opens,
                kernel_starts=None if kernel is None else kernel.starts,
                serial_enabled=not args.dry_run,
                frame_capture=None if capture is None else capture.stats(),
                joystick=None if js is None else
                {"device": js.device, "opens": js.opens, "presses": dict(js.presses)})
        return bool(stop)

    try:
        loop.run(should_stop=should_stop)
    finally:
        if args.dry_run:
            log("diagnostic_output", serial_opened=False, serial_bytes_written=0,
                packets_discarded=link.packets_discarded)
    log("stopped", signal=stop.reason, **loop.stats, frames_logged=frames.rows)
    return EXIT_OK


def resolve_backend(explicit):
    """命令行 --backend 优先；其次读 /etc/kv260car/backend；都没有就 pl。"""
    if explicit:
        return explicit
    try:
        text = BACKEND_FLAG.read_text(encoding="utf-8").strip().lower()
    except OSError:
        return "pl"
    if not text:
        return "pl"
    if text not in ("pl", "cpu"):
        raise ValueError(f"{BACKEND_FLAG} says {text!r}; write pl or cpu")
    return text


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def positive_seconds(value):
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("must be finite and greater than zero")
    return seconds


def build_cli():
    cli = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    cli.add_argument("--runs-dir", default="/home/ubuntu/car_runs")
    cli.add_argument("--models", type=Path, default=HERE / "models")
    cli.add_argument("--camera-config", type=Path)
    cli.add_argument("--serial-config", type=Path)
    cli.add_argument("--joystick-config", type=Path)
    cli.add_argument("--keep-runs", type=int, default=30)
    cli.add_argument("--heartbeat-s", type=float, default=60.0)
    cli.add_argument("--backend", choices=("pl", "cpu"),
                     help="推理后端；不给就读 /etc/kv260car/backend，再不给就是 pl")
    cli.add_argument("--model", type=Path, default=DEFAULT_CPU_MODEL,
                     help="cpu 后端用的原模型（默认 NUC 迁移包里的 model_current.onnx）")
    cli.add_argument("--threads", type=int, default=4, help="cpu 后端的 ORT 线程数，默认 4")
    cli.add_argument("--roadsign-config", type=Path,
                     help="可选路标 supervisor JSON；不提供就不加载 YOLO、不改变原控制路径")
    cli.add_argument("--save-frames", action="store_true", help="保存抽样推理输入；默认关闭")
    cli.add_argument("--save-every", type=positive_int, default=15, help="每N次推理保存一组三摄图，默认15")
    cli.add_argument("--save-max", type=positive_int, default=300, help="最多接受保存的图组数，默认300")
    cli.add_argument("--save-max-mib", type=positive_int, default=256, help="存图文件总预算MiB，默认256")
    cli.add_argument("--dry-run", action="store_true",
                     help="只采图和推理：不创建/打开串口，不读手柄，所有控制报文仅在内存丢弃")
    cli.add_argument("--seconds", type=positive_seconds,
                     help="仅用于 --dry-run：进入设备等待循环后的运行时长，单位秒；省略则持续运行")
    cli.add_argument("--auto-start", action="store_true",
                     help="临时：不用手柄，设备齐了就直接开跑（也可放一个 /etc/kv260car/auto_start 文件开启）")
    return cli


def main(argv=None):
    args = build_cli().parse_args(argv)
    try:
        args.backend = resolve_backend(args.backend)
    except ValueError as error:
        # 退出码 3 = 配置错误，systemd 不自动重启，避免刷屏
        print(f"backend config error: {error}", file=sys.stderr)
        return EXIT_CONFIG
    if not args.auto_start and AUTO_START_FLAG.exists():
        args.auto_start = True
    stop = StopFlag()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    return run(args, Hardware(), stop)


if __name__ == "__main__":
    raise SystemExit(main())
