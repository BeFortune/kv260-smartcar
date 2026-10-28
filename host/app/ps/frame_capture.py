"""Offline samples only: recomputed inputs and predictions, never send acknowledgements."""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from queue import Queue
from threading import Lock, Thread
import json

import cv2
import numpy as np

from .preprocess import preprocess_arrays


class FrameCapture:
    def __init__(self, root, every=15, max_samples=300, max_bytes=256*1024*1024,
                 queue_size=2, context=None):
        if any(type(value) is not int or value < 1
               for value in (every, max_samples, max_bytes, queue_size)):
            raise ValueError("Sampling and queue limits must be positive integers")
        self.root = Path(root)
        manifest = {
            "format_version": 1, "camera_order": ["left", "front", "right"],
            "image_format": "lossless PNG", "context": context,
            "preprocessing": {"crop_yx": [[150, 479], [0, 640]],
                              "resize_wh": [200, 66], "interpolation": "INTER_LINEAR",
                              "color": "BGR to YUV", "scale": "float32 /255 -0.5",
                              "layout": "N,camera,C,H,W", "shape": [1, 3, 3, 66, 200]},
            "crops": "BGR visualization only, not model input",
            "preprocessed_fp32_recomputed": "Recomputed by ps.preprocess.preprocess_arrays; not intercepted actual CPU input",
        }
        payload = self._json(manifest)
        if len(payload) > max_bytes:
            raise ValueError("max_bytes cannot hold manifest")
        self.root.mkdir()
        (self.root / "manifest.json").write_bytes(payload)
        (self.root / "index.jsonl").touch(exist_ok=False)
        self._lock, self._close_lock = Lock(), Lock()
        self._queue = Queue(queue_size)
        self._closed = False
        self.every, self.max_samples, self.max_bytes = every, max_samples, max_bytes
        self._seen = set()
        self._counts = dict(accepted=0, saved=0, queue_dropped=0, errors=0,
                            limit=int(len(payload) == max_bytes), bytes_written=len(payload),
                            disabled_reason="max_bytes" if len(payload) == max_bytes else None,
                            first_error=None, pending=0)
        self._worker = Thread(target=self._run, name="frame-capture", daemon=True)
        self._worker.start()

    @staticmethod
    def _json(value):
        return json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")

    def _wants(self, frame_id):
        return (type(frame_id) is int and frame_id > 0 and (frame_id - 1) % self.every == 0
                and frame_id not in self._seen and not self._closed
                and self._counts["disabled_reason"] is None)

    def wants(self, frame_id):
        with self._lock:
            return self._wants(frame_id)

    def submit(self, frame_id, images, metadata, tensors=None):
        with self._lock:
            if not self._wants(frame_id):
                return False
            if self._queue.full():
                self._seen.add(frame_id)
                self._counts["queue_dropped"] += 1
                return False
            try:
                if len(images) != 3 or any(not isinstance(image, np.ndarray) or
                        image.shape != (480, 640, 3) or image.dtype != np.uint8 for image in images):
                    raise ValueError("Requires left/front/right uint8 BGR 480x640 images")
                tensors = {} if tensors is None else tensors
                if not isinstance(tensors, dict) or any(
                        not isinstance(name, str) or not name or "/" in name or "\\" in name
                        or name in ("preprocessed_fp32_recomputed", "file")
                        or not isinstance(value, np.ndarray) or value.dtype.hasobject
                        for name, value in tensors.items()):
                    raise ValueError("Invalid tensor dictionary")
                copied_metadata = deepcopy(metadata)
                self._json(copied_metadata)
                owned = tuple(np.array(image, copy=True) for image in images)
                copied_tensors = {name: np.array(value, copy=True) for name, value in tensors.items()}
            except Exception as exc:
                self._error(exc)
                return False
            self._queue.put_nowait((frame_id, owned, copied_metadata, copied_tensors))
            self._counts["accepted"] += 1
            self._counts["pending"] += 1
            self._seen.add(frame_id)
            if self._counts["accepted"] >= self.max_samples:
                self._counts["disabled_reason"] = "max_samples"
                self._counts["limit"] += 1
        return True

    def _encode(self, frame_id, images, metadata, tensors):
        files, pixels = {}, {}
        folder = f"frame_{frame_id:06d}"
        for name, image in zip(("left", "front", "right"), images):
            crop = cv2.resize(image[150:479, 0:640], (200, 66), interpolation=cv2.INTER_LINEAR)
            for path, value in ((f"{name}.png", image), (f"crop_{name}.png", crop)):
                ok, encoded = cv2.imencode(".png", value)
                if not ok:
                    raise OSError("PNG encoding failed")
                files[path] = encoded.tobytes()
            pixels[name] = {"shape": list(image.shape), "sha256": sha256(image.tobytes()).hexdigest()}
        stream = BytesIO()
        np.savez(stream, preprocessed_fp32_recomputed=preprocess_arrays(*images), **tensors)
        files["inputs.npz"] = stream.getvalue()
        hashes = {f"{folder}/{name}": sha256(data).hexdigest() for name, data in files.items()}
        files["metadata.json"] = self._json({
            "frame_id": frame_id, "metadata": metadata, "predictions_not_send_ack": True,
            "input_files": list(hashes), "sha256": hashes, "original_pixels": pixels,
            "preprocessed_fp32_recomputed": "Recomputed, not intercepted actual CPU input",
            "crops": "BGR visualization only",
        })
        hashes = dict(hashes, **{f"{folder}/metadata.json": sha256(files["metadata.json"]).hexdigest()})
        return folder, files, self._json({"frame_id": frame_id, "path": folder, "sha256": hashes}) + b"\n"

    def _save(self, item):
        folder, files, index = self._encode(*item)
        size = sum(map(len, files.values())) + len(index)
        with self._lock:
            if self._counts["bytes_written"] + size > self.max_bytes:
                self._counts["disabled_reason"] = "max_bytes"
                self._counts["limit"] += 1
                return
        partial, final = self.root / (folder + ".partial"), self.root / folder
        if final.exists():
            raise FileExistsError("Frame directory already exists")
        partial.mkdir()
        for name, data in files.items():
            self._write(partial / name, data, "xb")
        partial.rename(final)
        self._write(self.root / "index.jsonl", index, "ab")
        with self._lock:
            self._counts["saved"] += 1
            if self._counts["bytes_written"] >= self.max_bytes:
                self._counts["disabled_reason"] = "max_bytes"
                self._counts["limit"] += 1

    def _write(self, path, data, mode):
        before = path.stat().st_size if mode == "ab" else 0
        try:
            with path.open(mode) as stream:
                if stream.write(data) != len(data):
                    raise OSError("Short capture write")
                stream.flush()
        finally:
            try:
                growth = path.stat().st_size - before
            except OSError:
                growth = 0
            with self._lock:
                self._counts["bytes_written"] += growth

    def _error(self, exc):
        self._counts["errors"] += 1
        self._counts["first_error"] = self._counts["first_error"] or {
            "type": type(exc).__name__, "errno": getattr(exc, "errno", None),
            "message": "Capture failed; inspect retained partial files (payload omitted)",
        }
        self._counts["disabled_reason"] = "error"

    def _run(self):
        while True:
            item = self._queue.get()
            try:
                if item is None:
                    return
                with self._lock:
                    skip = self._counts["disabled_reason"] in ("error", "max_bytes")
                if not skip:
                    self._save(item)
            except Exception as exc:
                with self._lock:
                    self._error(exc)
            finally:
                with self._lock:
                    if item is not None:
                        self._counts["pending"] -= 1
                self._queue.task_done()

    def close(self):
        with self._close_lock:
            if self._closed:
                return
            with self._lock:
                self._closed = True
            self._queue.join()
            self._queue.put(None)
            self._worker.join()

    def stats(self):
        with self._lock:
            return dict(self._counts)
