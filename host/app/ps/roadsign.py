"""路标 ONNX 推理与输出解析。控制路径不做 NMS，与上游参数语义一致。"""
from dataclasses import dataclass
import hashlib
from pathlib import Path
import time

import numpy as np

CLASS_NAMES = (
    "ROADSIGN_left_turn",
    "ROADSIGN_right_turn",
    "ROADSIGN_side_walk",
    "ROADSIGN_limit_on",
    "ROADSIGN_limit_off",
    "ROADSIGN_green_light",
    "ROADSIGN_red_light",
    "ROADSIGN_change_line",
    "ROADSIGN_warning",
    "ROADSIGN_pedestrian",
    "ROADSIGN_cone_bucket",
)


@dataclass(frozen=True)
class Detection:
    class_id: int
    class_name: str
    confidence: float
    width: float
    height: float


@dataclass(frozen=True)
class RoadSignResult:
    detections: tuple
    params: dict


def _accepted_size(class_id, width, height):
    if class_id in (0, 1):
        return width > 40 and height > 41
    if class_id == 6:
        return width > 55 and height > 55
    if class_id == 3:
        return width > 35 and height > 35
    if class_id == 2:
        return width > 55 and height > 55
    if class_id == 4:
        return width > 60 and height > 60
    if class_id == 7:
        return width > 50 or height > 50
    if class_id == 8:
        return width > 20 and height > 50
    return width > 80 and height > 80


def preprocess_roadsign(image, *, input_width, input_height):
    """与上游一致：直接 resize、BGR→RGB、/255、NCHW，不做 letterbox。"""
    import cv2
    if image is None or image.shape != (480, 640, 3) or image.dtype != np.uint8:
        raise ValueError("路标检测需要 640x480 uint8 BGR 图像")
    if input_width <= 0 or input_height <= 0:
        raise ValueError("路标模型输入尺寸必须为正数")
    resized = cv2.resize(image, (input_width, input_height), interpolation=cv2.INTER_LINEAR)
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
    scaled = cv2.addWeighted(rgb, 1 / 255., rgb, 0., 0., dtype=cv2.CV_32F)
    return np.ascontiguousarray(scaled.transpose(2, 0, 1)[None], dtype=np.float32)


def parse_roadsign_output(output, *, input_width, input_height, image_shape):
    output = np.asarray(output, dtype=np.float32)
    if not np.isfinite(output).all():
        raise ValueError("路标输出含非有限值")
    if output.ndim != 3 or output.shape[0] != 1:
        raise ValueError("路标输出必须是三维 FP32，且 batch=1")
    dim1, dim2 = output.shape[1:]
    transposed = dim1 < dim2
    anchors = dim2 if transposed else dim1
    params_count = dim1 if transposed else dim2
    can_v5 = params_count >= 6 and 0 < params_count - 5 <= len(CLASS_NAMES)
    can_v8 = params_count >= 5 and 0 < params_count - 4 <= len(CLASS_NAMES)
    if not can_v5 and not can_v8:
        raise ValueError(f"不支持的路标输出形状 {output.shape}")
    has_objectness = anchors >= 10000 if can_v5 and can_v8 else can_v5
    class_offset = 5 if has_objectness else 4
    class_count = params_count - class_offset
    height, width = image_shape[:2]
    if (isinstance(width, bool) or isinstance(height, bool)
            or not isinstance(width, (int, np.integer))
            or not isinstance(height, (int, np.integer))
            or width <= 0 or height <= 0):
        # 上游 cv::resize 不接受 0/负尺寸；公开解析入口同样 fail-closed，
        # 避免非法 image_shape 被静默当成“没有路标”。
        raise ValueError(f"路标图像尺寸必须是正整数：width={width!r} height={height!r}")
    if input_width <= 0 or input_height <= 0:
        raise ValueError("路标模型输入尺寸必须为正数")
    # 与上游 float32 运算逐位一致：先 float32 相除得到 scale，再做 float32 乘法。
    # 用 double（raw * width / input_width）会在尺寸阈值附近翻转接受/拒绝结果，
    # 见 artifacts/.../run01/parser_parity01 的 scale_div_* 用例。
    scale_x = np.float32(width) / np.float32(input_width)
    scale_y = np.float32(height) / np.float32(input_height)
    detections = []

    def value(anchor, attr):
        return float(output[0, attr, anchor] if transposed else output[0, anchor, attr])

    for anchor in range(anchors):
        confidence = value(anchor, 4) if has_objectness else 0.0
        if has_objectness and confidence <= 0.7:
            continue
        scores = [value(anchor, class_offset + index) for index in range(class_count)]
        class_id = int(np.argmax(scores))
        score = scores[class_id]
        if class_id >= len(CLASS_NAMES) or score <= 0.5:
            continue
        if not has_objectness:
            if score <= 0.7:
                continue
            confidence = score
        box_width = float(np.float32(value(anchor, 2)) * scale_x)
        box_height = float(np.float32(value(anchor, 3)) * scale_y)
        if not _accepted_size(class_id, box_width, box_height):
            continue
        detections.append(Detection(class_id, CLASS_NAMES[class_id], confidence,
                                    box_width, box_height))
    params = {name: False for name in CLASS_NAMES}
    for detection in detections:
        params[detection.class_name] = True
    return RoadSignResult(tuple(detections), params)


class RoadSignError(RuntimeError):
    """模型加载、推理或输出合同失败；调用方不得把异常当成“没有路标”。"""


@dataclass(frozen=True)
class RoadSignFrame:
    result: RoadSignResult
    grabbed: float
    completed: float
    latency_ms: float
    model_sha256: str


def _sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(8 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class RoadSignONNX:
    """PS 上的路标检测器；只使用 CPUExecutionProvider，不隐式启用其他后端。"""

    def __init__(self, model_path, *, expected_sha256, threads=1, clock=time.monotonic):
        import onnxruntime as ort
        self.model_path = Path(model_path)
        if not self.model_path.is_file():
            raise RoadSignError(f"路标模型不存在：{self.model_path}")
        self.sha256 = _sha256_file(self.model_path)
        if self.sha256 != expected_sha256:
            raise RoadSignError("路标模型 SHA256 与钉死值不一致")
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_EXTENDED
        options.log_severity_level = 3
        try:
            self.session = ort.InferenceSession(str(self.model_path), sess_options=options,
                                                providers=["CPUExecutionProvider"])
        except Exception as error:
            raise RoadSignError(f"路标 ONNX 加载失败：{error}") from error
        if self.session.get_providers() != ["CPUExecutionProvider"]:
            raise RoadSignError("路标模型没有使用 CPUExecutionProvider")
        inputs, outputs = self.session.get_inputs(), self.session.get_outputs()
        if len(inputs) != 1 or not outputs:
            raise RoadSignError("路标模型必须有一个输入和至少一个输出")
        shape = list(inputs[0].shape)
        if len(shape) != 4 or shape[0] not in (1, "1") or shape[1] not in (3, "3") or inputs[0].type != "tensor(float)":
            raise RoadSignError(f"路标输入不是 NCHW FP32：{shape}")
        self.input_width = shape[3] if isinstance(shape[3], int) and shape[3] > 0 else 640
        self.input_height = shape[2] if isinstance(shape[2], int) and shape[2] > 0 else 640
        self.input_name, self.output_name = inputs[0].name, outputs[0].name
        self.clock = clock
        self.last_ms = {}

    def detect(self, image, *, grabbed):
        if not np.isfinite(grabbed):
            raise RoadSignError("路标原图时间戳不是有限值")
        started = self.clock()
        try:
            tensor = preprocess_roadsign(image, input_width=self.input_width,
                                         input_height=self.input_height)
            output = self.session.run([self.output_name], {self.input_name: tensor})[0]
            result = parse_roadsign_output(output, input_width=self.input_width,
                                           input_height=self.input_height,
                                           image_shape=image.shape)
        except Exception as error:
            raise RoadSignError(f"路标推理失败：{error}") from error
        completed = self.clock()
        self.last_ms = {"total": (completed - started) * 1000}
        return RoadSignFrame(result, float(grabbed), completed,
                             self.last_ms["total"], self.sha256)
