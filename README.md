> 2026-10-19：当前路线与状态见 [STATUS.md](STATUS.md)。

# kv260-smartcar
CRAIC smart car: NUC to KV260 migration (PYNQ / ONNX / FPGA acceleration), STM32 keeps motor control

## PYNQ FFN migration

The [pynq_ffn](pynq_ffn/README.md) project contains the CPU model partitioning,
replaceable FFN backend, HLS reference kernel, KV260 overlay, and validation evidence.

- Four FFN layers have run on a real KV260 with trained weights. A 117-frame
  recorded-data replay matched the CPU reference's integer wheel-speed outputs.
- The current serial FP32 reference kernel is for correctness, not acceleration:
  measured hybrid inference is about 2.5 seconds per frame. See the
  [verification record](pynq_ffn/docs/04_验证记录.md) for test scope and timing limitations.
- No live camera, motor, serial, or vehicle-control test is included.
- Start with the [project README](pynq_ffn/README.md) and
  [board validation evidence](pynq_ffn/artifacts/board_20261015/README.md).

Original project code is licensed under the [MIT License](pynq_ffn/LICENSE),
Copyright (c) 2026 BeFortune. Third-party models, datasets, dependencies, and
AMD/Xilinx-generated material remain subject to their respective licenses.
