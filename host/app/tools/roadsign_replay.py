"""Shared timestamp and statistics helpers for offline integrated simulation."""
import hashlib
import json
import math
from pathlib import Path
import time

import numpy as np


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(8 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class ReplayClock:
    def __init__(self):
        self.base = 0.0
        self.started = time.perf_counter()

    def set(self, t_mono):
        if type(t_mono) not in (int, float) or not math.isfinite(t_mono):
            raise ValueError("metadata.t_mono must be a finite capture completion time")
        self.base = t_mono
        self.started = time.perf_counter()

    def set_frames(self, grabbed, *, t_mono):
        values = list(grabbed)
        if len(values) != 3 or any(type(stamp) not in (int, float) or not math.isfinite(stamp) for stamp in values):
            raise ValueError("three finite camera timestamps required")
        if type(t_mono) not in (int, float) or not math.isfinite(t_mono):
            raise ValueError("metadata.t_mono must be finite")
        if t_mono < max(values):
            raise ValueError("metadata.t_mono precedes a grabbed frame")
        self.set(t_mono)

    def elapsed_s(self):
        return time.perf_counter() - self.started

    def __call__(self):
        return self.base + self.elapsed_s()


def metadata_for(sample):
    path = Path(sample["frame_dir"]) / "metadata.json"
    if sha256_file(path) != sample["metadata_sha256"]:
        raise ValueError("metadata hash changed before replay: " + str(path))
    value = json.loads(path.read_text(encoding="utf-8"))["metadata"]
    grabbed = [value.get("grabbed_left_mono"), value.get("grabbed_front_mono"), value.get("grabbed_right_mono")]
    if any(type(stamp) not in (int, float) or not math.isfinite(stamp) for stamp in grabbed):
        raise ValueError("invalid camera timestamps: " + sample["sample_id"])
    return value, grabbed


def summary(values):
    values = np.asarray(values, dtype=np.float64)
    if not values.size:
        return None
    return {
        "n": int(values.size),
        "mean": float(values.mean()),
        "p50": float(np.quantile(values, .5)),
        "p95": float(np.quantile(values, .95)),
        "p99": float(np.quantile(values, .99)),
        "max": float(values.max()),
    }
