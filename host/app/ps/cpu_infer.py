"""纯 CPU 原模型推理后端：三张 640x480 BGR 图 -> (左速度, 右速度)。

这就是 NUC 上的原方案：`preprocess_arrays` -> `model_current.onnx` -> `speed_raw`。
不 import pynq、不碰 PL、不开任何设备，可以直接注入 CarLoop 替换 RouteBInfer。

和 PL 路径的关系：查表预处理（`pl_infer.build_input_lut`）是把同一个
`preprocess_arrays`（resize + BGR2YUV + addWeighted）量化成 int8 的结果，
两条路的输入语义和输出语义一样，只有中间 conv0-2 是 PL 还是 CPU 的差别。

输出取 `speed_raw`：NUC 的 `predict` 和 `ps/replay.py` 用的都是它，`packet.py`
按 C++ 的向零截断编帧。`speed_norm` 不用。
"""

import hashlib
import time
from pathlib import Path

import numpy as np

from .preprocess import preprocess_arrays

MODEL_SHA256 = "ad58d1fc9168da94b9ce076ba8cc9754c371ead87974da8bc9cc601ad1650d5b"
INPUT_SHAPE = (1, 3, 3, 66, 200)
OUTPUT_NAME = "speed_raw"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def check_model(path):
    """原模型的 SHA 必须和 NUC 迁移时钉死的一致，不一致就不跑。"""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"original model not found: {path}")
    got = sha256(path)
    if got != MODEL_SHA256:
        raise RuntimeError(f"model sha256 mismatch: {path} is {got}")
    return got


def make_session(path, threads=4):
    """和 runtime.py 的 golden 模式同一套 session 选项，保证数值口径一致。"""
    import onnxruntime as ort
    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_EXTENDED
    options.log_severity_level = 3
    return ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])


class CpuOriginalInfer:
    """CarLoop 的 infer：infer(left, front, right) -> (左速度, 右速度)。

    last_ms 的键和 RouteBInfer 一样，这样 frames.csv 的列不用改：
      pre    预处理
      kernel PL 核（纯 CPU 没有，记 None）
      dequant 反量化（纯 CPU 没有，记 None）
      partb  这里指整模型推理
      total  合计
    """

    def __init__(self, model_path, threads=4):
        self.model_path = Path(model_path)
        self.sha256 = check_model(self.model_path)
        self.threads = threads
        self.session = make_session(self.model_path, threads)
        inputs = self.session.get_inputs()
        if len(inputs) != 1 or list(inputs[0].shape) != list(INPUT_SHAPE) or inputs[0].type != "tensor(float)":
            raise RuntimeError(f"model input is not {INPUT_SHAPE} float32: "
                               f"{[(i.name, i.shape, i.type) for i in inputs]}")
        names = [output.name for output in self.session.get_outputs()]
        if OUTPUT_NAME not in names:
            raise RuntimeError(f"model has no {OUTPUT_NAME} output: {names}")
        self.input_name = inputs[0].name
        self.last_ms = {}

    def __call__(self, left, front, right):
        t0 = time.perf_counter()
        images = preprocess_arrays(left, front, right)
        t1 = time.perf_counter()
        speed = self.session.run([OUTPUT_NAME], {self.input_name: images})[0].reshape(-1)
        t2 = time.perf_counter()
        if speed.shape != (2,):
            raise RuntimeError(f"{OUTPUT_NAME} shape {speed.shape}, expected 2 speeds")
        if not np.isfinite(speed).all():
            raise RuntimeError(f"{OUTPUT_NAME} is not finite: {speed}")
        self.last_ms = {"pre": (t1 - t0) * 1e3, "kernel": None, "dequant": None,
                        "partb": (t2 - t1) * 1e3, "total": (t2 - t0) * 1e3}
        return float(speed[0]), float(speed[1])
