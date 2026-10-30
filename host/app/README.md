# 车端程序入口

本目录保留开发主源的相对结构：

- `ps/`：预处理、PL 接口、推理、采集、串口、手柄和控制循环。
- `tools/car/car_main.py`：车端主程序。
- `tools/car/pl_load.py`：哈希门禁与 Overlay 加载，属于硬件操作。
- `tools/car/cpu_check.py`：CPU 性能检查。
- `tools/car/frame_capture_replay.py`：录像/帧回放。
- `tools/car/README.md`：原部署与现场操作说明。
- `tools/car/systemd/`：三个服务单元。
- `ps/roadsign*.py`、`ps/control_arbiter.py`：默认不启用的旧路标监督路径。
- `ps/control_v2/`、`tools/control_v2_replay.py`：2026-10-30 独立离线控制候选，没有实车入口。
- `candidates/original_model_pspl/`：2026-10-29 原模型布局优化候选，未接入生产。
- `tests/`：可选路标、v2 和旧路径的定向测试。

文件仅从 `pynq_ffn` 白名单复制，未为交付目录重排业务代码。程序导入时不要求连接硬件；但显式调用 PL、相机、串口或主循环会改变设备状态。

部署打包从仓库根目录使用 `python host/deploy/make_bundle.py`，不要在 `host/app/` 内寻找另一个打包器。
