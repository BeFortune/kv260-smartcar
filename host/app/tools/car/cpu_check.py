"""板上单独测纯 CPU 原模型的耗时，不碰相机、串口、PL，也不给车发任何字节。

用法（KV260 上）：
  /usr/local/share/pynq-venv/bin/python3 cpu_check.py
  /usr/local/share/pynq-venv/bin/python3 cpu_check.py --frames 30 --threads 3

它做的事：核对模型 SHA -> 建 ORT session -> 用三张合成图跑 warmup 2 次 + N 次，
打印预处理/模型/合计的 P50 和 P95，以及最后一次的左右速度。

注意：4 线程会吃满 4 个 A53 核。车正在跑的时候执行会抢相机解码的 CPU，
可能让帧龄越过 200 ms 触发停车，所以请在 car-main 停下之后跑。
"""
import argparse
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
for _candidate in (HERE, *HERE.parents):
    if (_candidate / "ps" / "cpu_infer.py").is_file():
        sys.path.insert(0, str(_candidate))
        break

from ps import cpu_infer  # noqa: E402
from ps.packet import encode_side_speed  # noqa: E402


def synthetic_images(seed=20261022):
    """横向渐变、纵向渐变、固定种子噪声，覆盖 0-255，和 car_main.self_test_images 同源。"""
    h, w = 480, 640
    left = np.broadcast_to(np.linspace(0, 255, w, dtype=np.float32)[None, :, None], (h, w, 3))
    front = np.broadcast_to(np.linspace(0, 255, h, dtype=np.float32)[:, None, None], (h, w, 3))
    right = np.random.default_rng(seed).integers(0, 256, size=(h, w, 3), dtype=np.uint8)
    return [np.ascontiguousarray(np.rint(left).astype(np.uint8)),
            np.ascontiguousarray(np.rint(front).astype(np.uint8)),
            right]


def summarize(values):
    ordered = sorted(values)
    return ordered[len(ordered) // 2], ordered[min(len(ordered) - 1, int(0.95 * (len(ordered) - 1)))]


def main(argv=None):
    parser = argparse.ArgumentParser(description="纯 CPU 原模型自检与耗时")
    parser.add_argument("--model", type=Path, default=Path(
        "/home/ubuntu/kv260_v1_pynq_20261011/model/model_current.onnx"))
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--frames", type=int, default=20)
    args = parser.parse_args(argv)

    print(f"model   {args.model}")
    print(f"sha256  {cpu_infer.check_model(args.model)}")
    infer = cpu_infer.CpuOriginalInfer(args.model, args.threads)
    print(f"threads {args.threads}   frames {args.frames}")

    images = synthetic_images()
    for _ in range(2):                       # warmup 不计入统计
        infer(*images)

    totals, pres, models, speeds = [], [], [], None
    for _ in range(args.frames):
        speeds = infer(*images)
        totals.append(infer.last_ms["total"])
        pres.append(infer.last_ms["pre"])
        models.append(infer.last_ms["partb"])

    pre_p50, pre_p95 = summarize(pres)
    model_p50, model_p95 = summarize(models)
    total_p50, total_p95 = summarize(totals)
    print("预处理 ms  P50 {:.2f}  P95 {:.2f}".format(pre_p50, pre_p95))
    print("模型   ms  P50 {:.2f}  P95 {:.2f}".format(model_p50, model_p95))
    print("合计   ms  P50 {:.2f}  P95 {:.2f}   对应上限 {:.2f} Hz / {:.2f} Hz".format(
        total_p50, total_p95, 1000.0 / total_p50, 1000.0 / total_p95))
    print("最后一次速度  {:.2f} / {:.2f}".format(speeds[0], speeds[1]))
    try:
        frame = encode_side_speed(*speeds)
        print("能编成帧  " + frame.hex(" "))
    except Exception as error:  # noqa: BLE001
        print(f"编帧失败  {type(error).__name__}: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
