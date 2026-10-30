"""离线回放三摄驾驶 + 前视路标 + 控制仲裁；不打开相机、串口或 PL。"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
import time
import traceback

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
WORKSPACE = PROJECT.parent
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from ps.cpu_infer import CpuOriginalInfer  # noqa: E402
from ps.roadsign import RoadSignONNX  # noqa: E402
from ps.roadsign_supervisor import RoadSignStale, RoadSignSupervisor  # noqa: E402
from tools.field_replay import load_pinned_images  # noqa: E402


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(8 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def dump(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def summary(values):
    values = np.asarray(values, dtype=np.float64)
    if not values.size:
        return None
    return {"n": int(values.size), "mean": float(values.mean()),
            "p50": float(np.quantile(values, .5)), "p95": float(np.quantile(values, .95)),
            "p99": float(np.quantile(values, .99)), "max": float(values.max())}


class ReplayClock:
    """历史取三摄完成时刻 + 本次 CPU 墙钟耗时；不是历史决定或板上时钟。"""

    def __init__(self):
        self.base = 0.0
        self.started = time.perf_counter()

    def set(self, t_mono):
        if type(t_mono) not in (int, float) or not math.isfinite(t_mono):
            raise ValueError("metadata.t_mono 必须是有限的取三摄完成时间")
        self.base = t_mono
        self.started = time.perf_counter()

    def set_frames(self, grabbed, *, t_mono):
        values = list(grabbed)
        if len(values) != 3 or any(type(stamp) not in (int, float) or not math.isfinite(stamp)
                                   for stamp in values):
            raise ValueError("三摄抓帧时间必须是三个有限数值")
        if type(t_mono) not in (int, float) or not math.isfinite(t_mono):
            raise ValueError("metadata.t_mono 必须是有限的取三摄完成时间")
        if t_mono < max(values):
            raise ValueError("metadata.t_mono 早于抓帧时间，存在未来帧")
        self.set(t_mono)

    def elapsed_s(self):
        return time.perf_counter() - self.started

    def __call__(self):
        return self.base + self.elapsed_s()


def metadata_for(sample):
    path = Path(sample["frame_dir"]) / "metadata.json"
    if sha256_file(path) != sample["metadata_sha256"]:
        raise ValueError("metadata 哈希在回放前发生变化：" + str(path))
    value = json.loads(path.read_text(encoding="utf-8"))["metadata"]
    grabbed = [value.get("grabbed_left_mono"), value.get("grabbed_front_mono"),
               value.get("grabbed_right_mono")]
    if any(type(stamp) not in (int, float) or not math.isfinite(stamp) for stamp in grabbed):
        raise ValueError("三摄抓帧时间必须是有限数值：" + sample["sample_id"])
    return value, grabbed


def run(args):
    contract_path = args.contract.resolve()
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    samples = contract["samples"]
    if args.limit is not None:
        if args.limit < 1:
            raise ValueError("--limit 必须为正数")
        samples = samples[:args.limit]
    if not samples:
        raise ValueError("没有可回放样本")
    old_path = WORKSPACE / "ros_craic_car/src/base_control/resource/model_current.onnx"
    replay_clock = ReplayClock()
    driver = CpuOriginalInfer(old_path, threads=args.driver_threads)
    detector = RoadSignONNX(args.roadsign_model, expected_sha256=args.roadsign_sha256,
                            threads=args.roadsign_threads, clock=replay_clock)
    supervisor = RoadSignSupervisor(detector, max_result_age_s=args.max_result_age_s,
                                    clock=replay_clock)
    rows, errors, stale = [], [], []
    baseline_max = 0.0
    current_run = None
    terminal_sample = None
    with (args.out_dir / "predictions.jsonl").open("x", encoding="utf-8", newline="\n") as stream:
        for index, sample in enumerate(samples):
            decision = None
            if sample["run"] != current_run:
                supervisor.reset()
                current_run = sample["run"]
            images = load_pinned_images(sample)
            metadata, grabbed = metadata_for(sample)
            replay_clock.set_frames(grabbed, t_mono=metadata.get("t_mono"))
            driver_left, driver_right = driver(*images)
            recorded = sample["recorded_old"]
            baseline_max = max(baseline_max, abs(driver_left - recorded["left"]),
                               abs(driver_right - recorded["right"]))
            if baseline_max > .001:
                raise RuntimeError(f"旧驾驶模型身份门禁失败：max_abs={baseline_max}")
            row = {"sample_id": sample["sample_id"], "run": sample["run"],
                   "frame_id": sample["frame_id"], "grabbed": grabbed,
                   "recorded_old": [recorded["left"], recorded["right"]],
                   "driver": [driver_left, driver_right], "driver_last_ms": dict(driver.last_ms),
                   "driver_identity_max_abs_so_far": baseline_max}
            try:
                decision = supervisor.evaluate(list(zip(images, grabbed)), driver_left, driver_right)
                row["decision"] = {"left": decision.left, "right": decision.right,
                                   "stop": decision.stop, "reason": decision.reason,
                                   "enter_wait_start": decision.enter_wait_start}
            except RoadSignStale as error:
                stale.append(sample["sample_id"])
                row["decision"] = {"left": 0.0, "right": 0.0, "stop": True,
                                   "reason": "supervisor_stale", "error": str(error)}
            except Exception as error:  # noqa: BLE001 - 保留逐帧失败后停止汇总
                errors.append({"sample_id": sample["sample_id"],
                               "type": type(error).__name__, "error": str(error)})
                row["decision"] = {"left": 0.0, "right": 0.0, "stop": True,
                                   "reason": "supervisor_error", "error": str(error)}
            elapsed_s = replay_clock.elapsed_s()
            decision_time = replay_clock.base + elapsed_s
            row["timing"] = {
                "recorded_capture_t_mono": metadata["t_mono"],
                "recorded_decision_t_mono": None,
                "capture_age_ms": [(metadata["t_mono"] - stamp) * 1000 for stamp in grabbed],
                "current_cpu_elapsed_ms": elapsed_s * 1000,
                "replay_decision_t_mono": decision_time,
            }
            frame = supervisor.last_frame
            if frame is not None:
                row["roadsign"] = {
                    "model_sha256": frame.model_sha256,
                    "latency_ms": frame.latency_ms,
                    "grabbed_t_mono": frame.grabbed,
                    "replay_completed_t_mono": frame.completed,
                    "age_ms": (decision_time - frame.grabbed) * 1000,
                    "params": frame.result.params,
                    "detections": [{"class_id": item.class_id, "class_name": item.class_name,
                                    "confidence": item.confidence, "width": item.width,
                                    "height": item.height} for item in frame.result.detections],
                }
            rows.append(row)
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
            stream.flush()
            if decision is not None and decision.enter_wait_start:
                # 工具没有人工启动事件：保留终态并判停，不替实车恢复/重启。
                terminal_sample = sample["sample_id"]
                break
            if (index + 1) % 25 == 0 or index + 1 == len(samples):
                print(f"roadsign_replay {index+1}/{len(samples)}", flush=True)
    reasons = Counter(row["decision"]["reason"] for row in rows)
    detector_times = [row["roadsign"]["latency_ms"] for row in rows if "roadsign" in row]
    detector_ages = [row["roadsign"]["age_ms"] for row in rows if "roadsign" in row]
    status = "已验证" if not errors and not stale and baseline_max <= .001 else "失败"
    result = {
        "schema": "roadsign_offline_replay_v1", "status": status,
        "scope": "recorded PNG + real old CPU driver + selected roadsign ONNX; no camera/serial/PL/vehicle",
        "samples": len(rows), "selected_limit": args.limit,
        "requested_samples": len(samples), "unprocessed_samples": len(samples) - len(rows),
        "baseline_identity_max_abs": baseline_max, "baseline_tolerance": .001,
        "decision_reasons": dict(reasons), "stop_count": sum(row["decision"]["stop"] for row in rows),
        "stale_count": len(stale), "stale_samples": stale, "errors": errors,
        "detector_latency_ms": summary(detector_times), "detection_age_ms": summary(detector_ages),
        "max_result_age_s": args.max_result_age_s,
        "finish": {
            "coverage": "exercised" if reasons.get("finish") else "not exercised",
            "scope": "工具未调用 set_replay_direction；未触发时不覆盖 finish。触发 enter_wait_start 只判停，"
                     "不 reset、不恢复、不跨帧或跨 run 重启；历史帧间隔不是真实等待或板测。",
            "terminal_sample": terminal_sample,
            "halt_reason": "enter_wait_start" if terminal_sample is not None else None,
        },
        "inputs": {
            "contract": {"path": str(contract_path), "sha256": sha256_file(contract_path)},
            "old_driver": {"path": str(old_path), "sha256": sha256_file(old_path)},
            "roadsign_model": {"path": str(args.roadsign_model), "sha256": sha256_file(args.roadsign_model)},
        },
        "driver_threads": args.driver_threads, "roadsign_threads": args.roadsign_threads,
        "not_executed": ["real PL", "camera", "serial", "motor", "closed-loop", "vehicle"],
        "note": "离线回放只证明本次模型/输入/规则行为，不证明真实检测准确率、实时性能或车辆安全。",
        "timing_note": "历史 t_mono 是 metadata 中实际取三摄完成时刻；原决定完成时刻未记录，标 null，不猜测。"
                       "回放决定时间=历史 t_mono + 当前 perf_counter CPU 墙钟耗时（含驾驶/检测/仲裁），"
                       "帧龄保留历史采集龄；PNG 读盘、历史帧间隔不算当前推理耗时，也不据此给真实吞吐或 KV260 板测结论。",
    }
    dump(args.out_dir / "summary.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), flush=True)
    return 0 if status == "已验证" else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--roadsign-model", type=Path, required=True)
    parser.add_argument("--roadsign-sha256", required=True)
    parser.add_argument("--max-result-age-s", type=float, required=True)
    parser.add_argument("--driver-threads", type=int, default=4)
    parser.add_argument("--roadsign-threads", type=int, default=1)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    args.roadsign_model = args.roadsign_model.resolve()
    args.out_dir = args.out_dir.resolve()
    if len(args.roadsign_sha256) != 64 or any(c not in "0123456789abcdef" for c in args.roadsign_sha256):
        parser.error("--roadsign-sha256 必须是 64 位小写十六进制")
    if not math.isfinite(args.max_result_age_s) or args.max_result_age_s <= 0:
        parser.error("--max-result-age-s 必须是有限正数")
    if args.driver_threads < 1 or args.roadsign_threads < 1:
        parser.error("线程数必须为正数")
    args.out_dir.mkdir(parents=True, exist_ok=False)
    dump(args.out_dir / "command.json", {"argv": sys.argv if argv is None else argv,
         "started_at": datetime.now(timezone.utc).isoformat(),
         "scope": "offline replay only; no device access"})
    try:
        return run(args)
    except BaseException as error:
        dump(args.out_dir / "failure.json", {"status": "失败", "type": type(error).__name__,
                                             "first_error": str(error)})
        with (args.out_dir / "failure_traceback.txt").open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(traceback.format_exc())
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
