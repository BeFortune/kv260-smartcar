"""Bit-exact integer implementation of conv_front_int8 (not an ORT simulator)."""
import json
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent


def load_params():
    return json.loads((HERE / "params.json").read_text(encoding="utf-8"))


def quantize_input(images, params=None):
    p = load_params() if params is None else params
    x = np.asarray(images, dtype=np.float32)
    if x.shape not in ((1, 3, 3, 66, 200), (3, 3, 66, 200)) or not np.all(np.isfinite(x)):
        raise ValueError("expected finite float32 [1,3,3,66,200] or [3,3,66,200]")
    return np.ascontiguousarray(np.clip(np.rint(x.reshape(3, 3, 66, 200) / np.float32(p["input_scale"]))
                                        + p["input_zp"], -128, 127), dtype=np.int8)


def dequant_output(q, params=None):
    p = load_params() if params is None else params
    if q.shape != (3, 64, 9, 25) or q.dtype != np.int8:
        raise ValueError("expected int8 [3,64,9,25]")
    layer = p["layers"][2]
    return np.ascontiguousarray((q.astype(np.float32) - np.float32(layer["conv_zp"])) * np.float32(layer["conv_scale"]))


def round_even_ratio(values, multipliers, shifts):
    """Exact signed rational rounding; magnitude rounding avoids negative shifts."""
    products = np.asarray(values, dtype=np.int64) * np.asarray(multipliers, dtype=np.int64)
    shifts = np.asarray(shifts, dtype=np.int64)
    mag = np.abs(products)
    whole = np.right_shift(mag, shifts)
    rem = mag - np.left_shift(whole, shifts)
    denom = np.left_shift(np.int64(1), shifts)
    up = (2*rem > denom) | ((2*rem == denom) & ((whole & 1) != 0))
    return np.where(products < 0, -(whole+up), whole+up)


def run(q, params=None, trace=False):
    p = load_params() if params is None else params
    if q.shape != (3, 3, 66, 200) or q.dtype != np.int8:
        raise ValueError("kernel input must be int8 [3,3,66,200]")
    result = q
    tensors = {"input_q": q}
    for i, layer in enumerate(p["layers"]):
        w = np.asarray(layer["weights"], dtype=np.int32)
        b = np.asarray(layer["bias"], dtype=np.int32)
        k = layer["kernel"]
        x = result.astype(np.int32) - layer["input_zp"]
        padded = np.pad(x, ((0, 0), (0, 0), (k//2, k//2), (k//2, k//2)))
        windows = np.lib.stride_tricks.sliding_window_view(padded, (k, k), axis=(2, 3))[:, :, ::2, ::2]
        n, _, h, width, _, _ = windows.shape
        columns = np.ascontiguousarray(windows.transpose(0, 2, 3, 1, 4, 5)).reshape(-1, w.shape[1]*k*k)
        # int32 matmul is exact: generator proves worst-case absolute sum < 2**31.
        acc = (columns @ w.reshape(w.shape[0], -1).T + b).reshape(n, h, width, -1).transpose(0, 3, 1, 2)
        cq = np.clip(round_even_ratio(acc, np.asarray(layer["multipliers"])[None, :, None, None],
                                     np.asarray(layer["shifts"])[None, :, None, None]) + layer["conv_zp"], -128, 127)
        tensors["conv%d_q" % i] = cq.astype(np.int8)
        relu = np.maximum(cq, layer["conv_zp"])
        if i < 2:
            relu = np.clip(round_even_ratio(relu-layer["conv_zp"], layer["relu_multiplier"], layer["relu_shift"])
                           + layer["relu_zp"], -128, 127)
        result = np.ascontiguousarray(relu, dtype=np.int8)
        tensors["relu%d_q" % i] = result
    return (result, tensors) if trace else result


def self_test():
    v = np.array([-7, -5, -3, -1, 0, 1, 3, 5, 7], dtype=np.int64)
    assert np.array_equal(round_even_ratio(v, 1, 1), [-4, -2, -2, 0, 0, 0, 2, 2, 4])
    assert np.array_equal(round_even_ratio(v, 1, 0), v)
    p = load_params()
    x = np.zeros((3, 3, 66, 200), dtype=np.float32)
    x.flat[:6] = np.array([-1000, -.5, .5, 1000, 0, 1], dtype=np.float32) * np.float32(p["input_scale"])
    assert quantize_input(x, p).flat[:6].tolist() == [-128, 0, 0, 127, 0, 1]
    print("reference rounding / saturation self-tests passed")


if __name__ == "__main__":
    self_test()
