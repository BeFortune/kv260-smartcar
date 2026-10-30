"""Small isolated seam: two camera FFNs prepared, never a production runtime.

A 3PS+2FFN bundle calls run only for camera_0/1; stage_2 retains both cross FFNs.
An optional four-FFN legacy bundle supplies cpu_forward for the two 4-token cuts.
The adapter deliberately has no forward(layer,x,weights) compatibility method:
passing mutable weights on the hot path would undo the immutable binding contract.
"""
from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

import numpy as np

from prepared_backend import freeze_weights

CAMERA_LAYERS = frozenset({"camera_0", "camera_1"})
CROSS_LAYERS = frozenset({"cross_0", "cross_1"})
LAYER_SHAPES = MappingProxyType({"camera_0": (3, 220, 128), "camera_1": (3, 220, 128),
                                 "cross_0": (1, 4, 128), "cross_1": (1, 4, 128)})


class CameraOnlyAdapter:
    def __init__(self, prepared, layer_weights, *, cpu_forward=None):
        keys = set(layer_weights) if isinstance(layer_weights, Mapping) else set()
        if keys not in (set(CAMERA_LAYERS), set(CAMERA_LAYERS | CROSS_LAYERS)):
            raise ValueError("Expected both camera layers, optionally both CPU cross layers")
        if CROSS_LAYERS <= keys and not callable(cpu_forward):
            raise ValueError("Legacy cross layers require an explicit CPU forward callable")
        self._prepared = prepared
        self._cpu_forward = cpu_forward
        self._cross_weights = {}
        self.prepared_info = {}
        for layer in ("camera_0", "camera_1"):
            self.prepared_info[layer] = prepared.prepare(layer, layer_weights[layer])
        for layer in ("cross_0", "cross_1"):
            if layer in keys:
                self._cross_weights[layer] = freeze_weights(layer_weights[layer])[0]
        self.last_route = None

    def run(self, layer, x):
        if layer not in LAYER_SHAPES:
            raise ValueError("Unknown model FFN layer")
        if (not isinstance(x, np.ndarray) or x.dtype != np.float32
                or x.shape != LAYER_SHAPES[layer] or not np.isfinite(x).all()):
            raise ValueError("Model FFN input shape, dtype or finiteness is invalid")
        if layer in CAMERA_LAYERS:
            self.last_route = "prepared_camera"
            result = self._prepared.run(layer, x)
        else:
            if layer not in self._cross_weights:
                raise ValueError("Cross FFNs are inside stage_2 in the camera-only bundle")
            self.last_route = "cpu_cross"
            result = self._cpu_forward(layer, x, self._cross_weights[layer])
        if (not isinstance(result, np.ndarray) or result.dtype != np.float32
                or result.shape != x.shape or not np.isfinite(result).all()):
            raise ValueError("FFN returned an invalid FP32 result")
        return result
