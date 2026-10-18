from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
import math

import cv2
import numpy as np

INPUT_SHAPE = (1, 3, 3, 66, 200)


@dataclass(frozen=True)
class Record:
    segment: str
    frame_index: int
    csv_line: int
    directory: Path
    front: Path
    left: Path
    right: Path
    recorded_left: float
    recorded_right: float


def checked_image_path(directory, raw):
    directory = Path(directory).resolve()
    relative = Path(raw.strip().replace("\\", "/"))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("CSV image paths must remain within their recording directory")
    path = (directory / relative).resolve()
    if directory not in path.parents or not path.is_file():
        raise ValueError("Missing or unconfined image")
    return path


def read_records(recordings, limit=None):
    if limit is not None and limit < 1:
        raise ValueError("Limit must be positive")
    records, seen = [], set()
    for selected in map(Path, recordings):
        manifest = (selected / "auto_data.csv" if selected.is_dir() else selected).resolve()
        if manifest in seen:
            raise ValueError("Duplicate recording")
        seen.add(manifest)
        directory = manifest.parent
        with manifest.open(encoding="utf-8-sig", newline="") as stream:
            frame_index = 0
            for line_number, row in enumerate(csv.reader(stream), 1):
                if not row or all(not value.strip() for value in row):
                    continue
                if len(row) not in (5, 6):
                    raise ValueError("Expected front,left,right,left_speed,right_speed[,region]")
                left, right = float(row[3]), float(row[4])
                if not math.isfinite(left) or not math.isfinite(right):
                    raise ValueError("Non-finite recorded speed")
                front_path, left_path, right_path = [checked_image_path(directory, v) for v in row[:3]]
                frame_index += 1
                records.append(Record(directory.name, frame_index, line_number, directory, front_path, left_path, right_path, left, right))
                if limit is not None and len(records) >= limit:
                    return records
    if not records:
        raise ValueError("No recording frames")
    return records


def preprocess_arrays(left, front, right):
    converted = []
    for image in (left, front, right):
        if image is None or image.shape != (480, 640, 3) or image.dtype != np.uint8:
            raise ValueError("Requires three uint8 BGR images of size 640x480")
        resized = cv2.resize(image[150:479, 0:640, :], (200, 66), interpolation=cv2.INTER_LINEAR)
        yuv = cv2.cvtColor(resized, cv2.COLOR_BGR2YUV)
        scaled = cv2.addWeighted(yuv, 1 / 255., yuv, 0., -.5, dtype=cv2.CV_32F)
        converted.append(scaled.transpose(2, 0, 1))
    return np.ascontiguousarray(np.stack(converted, axis=0)[None, ...], dtype=np.float32)


def preprocess_record(record):
    return preprocess_arrays(*(cv2.imread(str(p), cv2.IMREAD_COLOR) for p in (record.left, record.front, record.right)))


def legacy_packet_preview(left_speed, right_speed):
    if not math.isfinite(left_speed) or not math.isfinite(right_speed):
        return ""
    left, right = int(left_speed), int(right_speed)
    if left_speed < 0 or right_speed < 0 or left > 999 or right > 999:
        return ""
    return (f"%@1{left:03d}1{left:03d}1{right:03d}1{right:03d}1".encode("ascii") + bytes((255, 255))).hex(" ")
