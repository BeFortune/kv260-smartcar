"""用已有三摄录像验证存图链路。纯本机：旧 CPU 模型或整数参考，不开任何设备。

示例（pynq_ffn 目录）：
  python tools/car/frame_capture_replay.py --recording artifacts/sample_recording_1779374497 \
    --backend cpu --model ../ros_craic_car/src/base_control/resource/model_current.onnx --out NEW
  python tools/car/frame_capture_replay.py --archive ../Database/record_data_5.16.zip \
    --segment 1778852165 --backend reference --out NEW
"""
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cv2
import numpy as np

import car_main
from ps.cpu_infer import CpuOriginalInfer, MODEL_SHA256
from ps.frame_capture import FrameCapture
from ps.pl_infer import PINNED_SHA256, reference_infer
from ps.preprocess import preprocess_arrays, read_records

REF = ROOT / "hls/conv_front_int8/ref_int8.py"
PARAMS = ROOT / "hls/conv_front_int8/params.json"
PARTB = ROOT / "artifacts/route_b_quant_20261020/cut2_01_board/s16_j1c2_cut2int8_ps.onnx"
CPU_MODEL = ROOT.parent / "ros_craic_car/src/base_control/resource/model_current.onnx"
ROLES = ("left", "front", "right")


def digest_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def pixel_hashes(images):
    return {role: hashlib.sha256(image.tobytes()).hexdigest() for role, image in zip(ROLES, images)}


def directory_frames(recording):
    path = Path(recording)
    if path.is_dir() and not (path / "auto_data.csv").exists():
        path = path / "joystick.csv"
    for record in read_records([path]):
        paths = (record.left, record.front, record.right)
        images = [cv2.imread(str(p), cv2.IMREAD_COLOR) for p in paths]
        yield images, {"segment": record.segment, "csv_line": record.csv_line,
                       "paths": [str(p) for p in paths],
                       "recorded_targets": [record.recorded_left, record.recorded_right]}


def archive_frames(path, segment):
    with zipfile.ZipFile(path) as archive:
        candidates = [name for name in archive.namelist()
                      if PurePosixPath(name).parent.name == segment
                      and PurePosixPath(name).name in ("auto_data.csv", "joystick.csv")]
        auto = [name for name in candidates if PurePosixPath(name).name == "auto_data.csv"]
        candidates = auto or candidates
        if len(candidates) != 1:
            raise ValueError(f"expected one recording CSV for {segment}, got {candidates}")
        manifest = candidates[0]
        base = PurePosixPath(manifest).parent
        rows = csv.reader(io.StringIO(archive.read(manifest).decode("utf-8-sig")))
        for row_number, row in enumerate(rows, 1):
            if not row or not any(v.strip() for v in row):
                continue
            if len(row) not in (5, 6):
                raise ValueError(f"invalid recording row {row_number}")
            paths = []
            for value in (row[1], row[0], row[2]):
                rel = PurePosixPath(value.strip().replace("\\", "/"))
                if rel.is_absolute() or ".." in rel.parts:
                    raise ValueError("image path leaves the recording")
                paths.append(str(base / rel))
            images = [cv2.imdecode(np.frombuffer(archive.read(p), np.uint8), cv2.IMREAD_COLOR) for p in paths]
            yield images, {"segment": segment, "csv_line": row_number,
                           "archive": str(path), "paths": paths,
                           "recorded_targets": [float(row[3]), float(row[4])]}


class ReplaySource:
    def __init__(self, clock):
        self.clock = clock
        self.images = None
        self.grabbed = None

    def latest(self):
        return [(image, self.grabbed) for image in self.images]


def verify_outputs(out, expected, infer_factory):
    capture_root = out / "snapshots"
    indexed = [json.loads(line) for line in (capture_root / "index.jsonl").read_text(encoding="utf-8").splitlines()]
    with (out / "frames.csv").open(encoding="utf-8", newline="") as stream:
        csv_rows = {int(row["frame_id"]): row for row in csv.DictReader(stream)}
    comparisons = []
    infer = infer_factory()
    for item in indexed:
        frame_id = item["frame_id"]
        folder = capture_root / item["path"]
        meta = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
        row = meta["metadata"]
        images = [cv2.imread(str(folder / f"{role}.png"), cv2.IMREAD_COLOR) for role in ROLES]
        if pixel_hashes(images) != expected[frame_id]["pixels"]:
            raise AssertionError(f"PNG pixel mismatch for {frame_id}")
        for path, sha in item["sha256"].items():
            if digest_file(capture_root / path) != sha:
                raise AssertionError(f"file hash mismatch: {path}")
        for key in ("t_mono", "speed_left", "speed_right", "grabbed_left_mono", "grabbed_front_mono", "grabbed_right_mono"):
            if row[key] != float(csv_rows[frame_id][key]):
                raise AssertionError(f"CSV/metadata mismatch: {frame_id} {key}")
        saved_speeds = np.array([row["speed_left"], row["speed_right"]])
        rerun_speeds = np.array(infer(*images))
        delta = float(np.abs(saved_speeds - rerun_speeds).max())
        if delta > 0.001:
            raise AssertionError(f"saved image prediction mismatch: {delta}")
        with np.load(folder / "inputs.npz", allow_pickle=False) as tensors:
            np.testing.assert_array_equal(tensors["preprocessed_fp32_recomputed"], preprocess_arrays(*images))
            if hasattr(infer, "kernel"):
                np.testing.assert_array_equal(tensors["pl_input_int8_actual"], infer.kernel.inbuf)
                np.testing.assert_array_equal(tensors["pl_output_int8_actual"], infer.kernel.outbuf)
        comparisons.append({"frame_id": frame_id, "prediction_max_abs": delta})
    if not comparisons:
        raise AssertionError("no saved samples to verify")
    return {"samples": len(comparisons), "pixel_mismatch": 0, "tensor_mismatch": 0,
            "metadata_mismatch": 0, "prediction_max_abs": max(c["prediction_max_abs"] for c in comparisons),
            "comparisons": comparisons}


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = cli.add_mutually_exclusive_group(required=True)
    source.add_argument("--recording", type=Path)
    source.add_argument("--archive", type=Path)
    cli.add_argument("--segment")
    cli.add_argument("--backend", choices=("cpu", "reference"), default="cpu")
    cli.add_argument("--model", type=Path, default=CPU_MODEL)
    cli.add_argument("--save-every", type=car_main.positive_int, default=15)
    cli.add_argument("--save-max", type=car_main.positive_int, default=300)
    cli.add_argument("--save-max-mib", type=car_main.positive_int, default=256)
    cli.add_argument("--limit", type=car_main.positive_int)
    cli.add_argument("--out", required=True, type=Path)
    args = cli.parse_args(argv)
    if args.archive and not args.segment:
        cli.error("--archive requires --segment")
    args.out.mkdir(parents=True, exist_ok=False)
    report = {"status": "未执行", "scope": "host offline recording replay; no camera, serial, HID or PL",
              "backend": args.backend, "source": str(args.archive or args.recording), "segment": args.segment,
              "clock_scope": "replay host read times, not recording capture times; not a performance benchmark",
              "source_code_sha256": digest_file(__file__)}
    capture = frames_log = source_iter = None
    try:
        cv2.setNumThreads(1)
        if args.backend == "cpu":
            factory = lambda: CpuOriginalInfer(args.model, threads=1)
            hashes = {"original_cpu": MODEL_SHA256}
        else:
            factory = lambda: reference_infer(REF, PARAMS, PARTB, threads=1)
            hashes = dict(PINNED_SHA256)
        infer = factory()
        capture = FrameCapture(args.out / "snapshots", every=args.save_every, max_samples=args.save_max,
                               max_bytes=args.save_max_mib * 1024 * 1024,
                               context={"backend": args.backend, "model_sha256": hashes,
                                        "source": report["source"], "serial_enabled": False,
                                        "hardware_execution": False})
        frames_log = car_main.FrameLog(args.out / "frames.csv")
        source = ReplaySource(time.monotonic)
        timed_cameras = car_main.TimedCameras(source, time.monotonic)
        timed_infer = car_main.TimedInfer(infer, timed_cameras, frames_log, serial_enabled=False, capture=capture)
        source_iter = archive_frames(args.archive, args.segment) if args.archive else directory_frames(args.recording)
        expected = {}
        for frame_id, (images, origin) in enumerate(source_iter, 1):
            if args.limit and frame_id > args.limit:
                break
            if any(image is None or image.shape != (480, 640, 3) for image in images):
                raise ValueError(f"bad recording image at frame {frame_id}")
            source.images, source.grabbed = images, time.monotonic()
            triplet = timed_cameras.latest()
            timed_cameras.pending["recording_source"] = origin
            speeds = timed_infer(*(image for image, _ in triplet))
            expected[frame_id] = {"pixels": pixel_hashes(images), "speeds": list(speeds), "origin": origin}
        capture.close()
        frames_log.close()
        frames_log = None
        report["frames_replayed"] = len(expected)
        report["capture"] = capture.stats()
        if report["capture"]["errors"]:
            raise RuntimeError("snapshot writer failed; inspect capture stats")
        report["verification"] = verify_outputs(args.out, expected, factory)
        report["status"] = "已验证"
    except Exception as error:
        report["status"] = "失败"
        report["first_error"] = f"{type(error).__name__}: {error}"
    finally:
        if source_iter is not None:
            source_iter.close()
        if capture is not None:
            capture.close()
            report["capture"] = capture.stats()
        if frames_log is not None:
            frames_log.close()
        report["hardware_modules_imported"] = [m for m in ("pynq", "serial") if m in sys.modules]
        if report["hardware_modules_imported"]:
            report["status"] = "失败"
            report["first_error"] = "unexpected hardware module import"
        (args.out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "已验证" else 1


if __name__ == "__main__":
    raise SystemExit(main())
