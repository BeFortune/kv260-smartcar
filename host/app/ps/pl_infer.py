"""路线 B 推理层：三张 640x480 BGR 图 -> (左速度, 右速度)。

数据流和 2026-10-21 上板验收（117 帧 PL 对整数参考 0 差异）完全相同：
  查表预处理 -> int8 输入缓冲 -> conv0-2 核（PL 或整数参考）-> 反量化 -> ORT 后半段 conv3-4+PS
输入顺序是左、前、右（和 NUC `Predict(img_l, img_f, img_r)` 一致）。

本文件导入时不 import pynq，也不碰任何硬件。核由调用方注入：
  - ReferenceKernel：整数参考代替 PL，本机离线测试用。
  - attach_loaded_pl()：板上用已经加载的 bit，只核对、不下载。这一步要 root 和单独授权。
"""

import hashlib
import importlib.util
import json
import os
import time
from pathlib import Path

import cv2
import numpy as np

from .car_loop import FatalError

IP_NAME = "conv_front_int8_0"
CTRL, IN_LO, OUT_LO = 0x00, 0x10, 0x1C
CTRL_ADDR = 0xA0000000
IN_BYTES, OUT_BYTES = 118800, 43200
IN_SHAPE, OUT_SHAPE = (3, 3, 66, 200), (3, 64, 9, 25)
INPUT_SCALE = np.float32(0.003921568859368563)
OUT_SCALE, OUT_ZP = np.float32(0.0806707814335823), -18

# 上板验收时钉死的文件哈希（pl_board_run01 report.json）
PINNED_SHA256 = {
    "bit": "8ab0f411cc3ddee74045a753b379d265a121871dc656a53ef25b7c2edfe8da24",
    "params": "889703bc0ea80da1a1f49f647c11714b249ee1eaea3149894f35dbbf4eb86a3f",
    "ref": "02feef12ea147f5b772a726ebd3c90d5f1e66bd33ff9fcefa50c3618a040edb4",
    "partb": "61bcec8f01ffca61ecc611f12bdf61a177f8ee0e9dc0febd256e02b8132cecdb",
}


class KernelHung(FatalError):
    """核超时。硬件可能还在访问缓冲，不能复用，主程序必须退出。"""


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def check_pinned(name, path):
    got = sha256(path)
    if got != PINNED_SHA256[name]:
        raise RuntimeError(f"{name} sha256 mismatch: {path} is {got}")


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_input_lut():
    """uint8 像素 -> int8 量化值。和 preprocess.py 的 addWeighted(1/255, -0.5) 再量化逐值一致。"""
    ramp = np.arange(256, dtype=np.uint8).reshape(1, 256, 1).repeat(3, axis=2)
    f = cv2.addWeighted(ramp, 1 / 255., ramp, 0., -.5, dtype=cv2.CV_32F)
    if not (np.array_equal(f[..., 0], f[..., 1]) and np.array_equal(f[..., 0], f[..., 2])):
        raise RuntimeError("addWeighted is channel dependent; LUT invalid")
    return np.clip(np.rint(f[0, :, 0].astype(np.float32) / INPUT_SCALE), -128, 127).astype(np.int8)


def dequant_table():
    codes = np.arange(256, dtype=np.int16).astype(np.int8).astype(np.float32)
    return ((codes - np.float32(OUT_ZP)) * OUT_SCALE).astype(np.float32)


def check_params(params):
    """量化参数要和这里写死的常数一致，否则查表和反量化都会错。"""
    if np.float32(params["input_scale"]) != INPUT_SCALE or params["input_zp"] != 0:
        raise RuntimeError("params input quantization differs from the LUT constants")
    last = params["layers"][-1]
    if np.float32(last["conv_scale"]) != OUT_SCALE or last["conv_zp"] != OUT_ZP:
        raise RuntimeError("params output quantization differs from the dequant constants")


def pre_lut(images, lut, out):
    """左、前、右三张图写进 int8 输入缓冲。裁剪和缩放与 preprocess_arrays 相同。"""
    view = out.reshape(IN_SHAPE)
    for i, image in enumerate(images):
        if image is None or image.shape != (480, 640, 3) or image.dtype != np.uint8:
            raise ValueError("need three uint8 BGR images of 640x480")
        resized = cv2.resize(image[150:479, 0:640, :], (200, 66), interpolation=cv2.INTER_LINEAR)
        view[i] = np.take(lut, cv2.cvtColor(resized, cv2.COLOR_BGR2YUV)).transpose(2, 0, 1)


class ReferenceKernel:
    """整数参考代替 PL。只用于离线测试，速度远慢于 PL。"""

    def __init__(self, ref, params):
        self.ref, self.params = ref, params
        self.inbuf = np.empty(IN_BYTES, dtype=np.int8)
        self.outbuf = np.empty(OUT_BYTES, dtype=np.int8)
        self.starts, self.hung = 0, False

    def run(self):
        self.outbuf[:] = self.ref.run(self.inbuf.reshape(IN_SHAPE), self.params).reshape(-1)
        self.starts += 1
        return self.outbuf

    def idle(self):
        return True, 4


class PLKernel:
    """ap_ctrl_hs 驱动，寄存器偏移与上板验收的驱动相同。"""

    def __init__(self, overlay, timeout_s=1.0):
        import pynq  # 只在板上、取得授权后才会走到这里
        self.ip = getattr(overlay, IP_NAME)
        self.timeout_s = timeout_s
        ctrl = self.ip.read(CTRL)
        if not ctrl & 4 or ctrl & 0x81:
            raise RuntimeError("kernel not idle before use: ctrl=%#x" % ctrl)
        self.inbuf = pynq.allocate(shape=(IN_BYTES,), dtype=np.int8)
        self.outbuf = pynq.allocate(shape=(OUT_BYTES,), dtype=np.int8)
        for buf, off in ((self.inbuf, IN_LO), (self.outbuf, OUT_LO)):
            addr = int(buf.device_address)
            if addr % 8:
                raise RuntimeError("buffer not 8-byte aligned")
            self.ip.write(off, addr & 0xFFFFFFFF)
            self.ip.write(off + 4, addr >> 32)
        self.starts, self.hung = 0, False

    def run(self):
        self.inbuf.flush()
        self.ip.write(CTRL, 1)
        self.starts += 1
        deadline = time.perf_counter() + self.timeout_s
        while True:
            ctrl = self.ip.read(CTRL)
            if ctrl & 2:  # ap_done，读一次即清
                break
            if time.perf_counter() > deadline:
                # 超时不代表硬件已停止访问内存：标记 hung，调用方不得释放或复用缓冲。
                self.hung = True
                raise TimeoutError("kernel did not finish within %.1fs; ctrl=%#x" % (self.timeout_s, ctrl))
        self.outbuf.invalidate()
        return self.outbuf

    def idle(self):
        ctrl = self.ip.read(CTRL)
        return bool(ctrl & 4) and not ctrl & 0x81, ctrl


def attach_loaded_pl(bit_path, timeout_s=1.0):
    """用已经加载在 PL 里的 bit。哈希对不上就停，绝不下载、不写 /lib/firmware。

    需要 root 和 XILINX_XRT=/usr。开机自动下载 bit 是另一步，不在这里。
    """
    if os.geteuid() != 0:
        raise RuntimeError("PL access needs root")
    if os.environ.get("XILINX_XRT") != "/usr" or "XCL_EMULATION_MODE" in os.environ:
        raise RuntimeError("XILINX_XRT=/usr required, and no XCL_EMULATION_MODE")
    check_pinned("bit", bit_path)
    from pynq.pl_server import global_state
    state = json.loads((Path(global_state.STATE_DIR) / "global_pl_state.json").read_text(encoding="utf-8"))
    loaded = state.get("bitfile_name")
    if not loaded or sha256(loaded) != PINNED_SHA256["bit"]:
        raise RuntimeError("loaded bitstream is not the pinned bit; refusing to reprogram")
    from pynq import Overlay
    overlay = Overlay(str(bit_path), download=False)
    if overlay.dtbo is not None or overlay.partial:
        raise RuntimeError("unexpected dtbo or partial image")
    if overlay.ip_dict[IP_NAME]["phys_addr"] != CTRL_ADDR:
        raise RuntimeError("control address is not %#x" % CTRL_ADDR)
    return PLKernel(overlay, timeout_s)


def make_session(path, threads=4):
    import onnxruntime as ort
    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_EXTENDED
    options.log_severity_level = 3
    return ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])


class RouteBInfer:
    """car_loop 的 infer：infer(left, front, right) -> (左速度, 右速度)。

    每次调用后 last_ms 里是各段耗时（毫秒），供性能日志用。
    """

    def __init__(self, kernel, partb_path, threads=4):
        check_pinned("partb", partb_path)
        self.kernel = kernel
        self.lut = build_input_lut()
        self.dq = dequant_table()
        self.session = make_session(partb_path, threads)
        inputs = self.session.get_inputs()
        if len(inputs) != 1 or list(inputs[0].shape) != list(OUT_SHAPE):
            raise RuntimeError(f"part B input is not {OUT_SHAPE}: {[(i.name, i.shape) for i in inputs]}")
        self.input_name = inputs[0].name
        self.last_ms = {}

    def __call__(self, left, front, right):
        if self.kernel.hung:
            raise KernelHung("PL kernel hung earlier; refusing to reuse its buffers")
        t0 = time.perf_counter()
        pre_lut((left, front, right), self.lut, self.kernel.inbuf)
        t1 = time.perf_counter()
        try:
            out = self.kernel.run()
        except TimeoutError as error:
            raise KernelHung(str(error)) from error
        t2 = time.perf_counter()
        feat = np.take(self.dq, np.asarray(out).view(np.uint8)).reshape(OUT_SHAPE)
        t3 = time.perf_counter()
        speed = self.session.run(None, {self.input_name: feat})[0].reshape(-1)
        t4 = time.perf_counter()
        self.last_ms = {"pre": (t1 - t0) * 1e3, "kernel": (t2 - t1) * 1e3, "dequant": (t3 - t2) * 1e3,
                        "partb": (t4 - t3) * 1e3, "total": (t4 - t0) * 1e3}
        if speed.shape != (2,):
            raise RuntimeError(f"part B output shape {speed.shape}, expected 2 speeds")
        return float(speed[0]), float(speed[1])


def reference_infer(ref_path, params_path, partb_path, threads=4):
    """离线组装：整数参考当核。不需要 pynq。"""
    check_pinned("ref", ref_path)
    check_pinned("params", params_path)
    ref = load_module("ref_int8", ref_path)
    params = json.loads(Path(params_path).read_text(encoding="utf-8"))
    check_params(params)
    return RouteBInfer(ReferenceKernel(ref, params), partb_path, threads)
