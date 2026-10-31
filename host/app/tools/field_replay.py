"""Shared offline image-pin loader for integrated simulation. No model or vehicle path."""
import hashlib
from pathlib import Path

import cv2
import numpy as np

ROLES = ("left", "front", "right")


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def load_pinned_images(sample):
    images = []
    for role in ROLES:
        pin = sample["images"][role]
        data = Path(pin["path"]).read_bytes()
        if len(data) != pin["bytes"] or sha256_bytes(data) != pin["file_sha256"]:
            raise ValueError("original image changed after prepare: " + sample["sample_id"] + "/" + role)
        image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None or image.shape != (480, 640, 3) or image.dtype != np.uint8:
            raise ValueError("image decode/shape/type failure: " + sample["sample_id"] + "/" + role)
        if sha256_bytes(image.tobytes()) != pin["pixel_sha256"]:
            raise ValueError("decoded pixel hash mismatch: " + sample["sample_id"] + "/" + role)
        images.append(image)
    return tuple(images)
