from __future__ import annotations

import math
import numpy as np

WEIGHT_SHAPES = {"w1": (128, 256), "b1": (256,), "w2": (256, 128), "b2": (128,)}
LAYER_SHAPES = {"camera_0": (3, 220, 128), "camera_1": (3, 220, 128),
                "cross_0": (1, 4, 128), "cross_1": (1, 4, 128)}


def validate_ffn(layer_name, x, weights):
    if layer_name not in LAYER_SHAPES:
        raise ValueError("Unknown FFN layer")
    if not isinstance(x, np.ndarray) or x.dtype != np.float32 or x.shape != LAYER_SHAPES[layer_name] or not np.isfinite(x).all():
        raise ValueError("Invalid FFN input shape, dtype or values")
    if set(weights) != set(WEIGHT_SHAPES):
        raise ValueError("Invalid FFN weight keys")
    for key, shape in WEIGHT_SHAPES.items():
        array = weights[key]
        if not isinstance(array, np.ndarray) or array.dtype != np.float32 or array.shape != shape or not np.isfinite(array).all():
            raise ValueError("Invalid FFN weight shape, dtype or values")


class NumpyFFN:
    """FP32 reference using the exported Erf-GELU, not a tanh approximation."""

    def forward(self, layer_name, x, weights):
        validate_ffn(layer_name, x, weights)
        z = np.matmul(x, weights["w1"]) + weights["b1"]
        scaled = z / np.float32(np.sqrt(2))
        erf = np.fromiter((math.erf(float(v)) for v in scaled.flat), dtype=np.float32, count=scaled.size).reshape(scaled.shape)
        activated = (z * (erf + np.float32(1))) * np.float32(.5)
        return np.ascontiguousarray(np.matmul(activated, weights["w2"]) + weights["b2"], dtype=np.float32)
