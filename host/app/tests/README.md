# 交付副本定向测试

这些测试来自开发主源，默认不访问真实相机、串口、PYNQ 或车辆。

- `test_control_v2_*.py`：v2 状态机、worker、sender、controller 与回放，共 59 项。
- 其余 8 个路标/仲裁/CarLoop/主入口文件：旧路径定向回归，共 83 项。
- `artifacts/roadsign_migration_20261027/run01/fixtures/constant_pedestrian.onnx` 是 1.3 KB 的常量测试模型，不是真实 YOLO。
- `test_car_main.py` 仅把工件路径改指交付根的 `fpga/build/` 和 `host/model/`，避免重复复制同一 bit/model；测试逻辑和断言未改。
- `tools/field_replay.py` 是 `roadsign_replay.py` 的导入依赖。它内部其他历史模型/现场路径不代表本包已包含那些数据，也不影响本次定向测试。

本轮已在交付副本实际得到 59 + 83 通过、0 跳过。测试通过不等于真实检测准确率、15 Hz 或实车安全验收。
