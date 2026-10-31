# 离线运行与依赖

本目录只提供当前仿真方案的运行依赖，不提供生产控车安装包或自启服务。

- `requirements-runtime.txt`：推理与预处理依赖。
- `requirements-prepare.txt`：模型/数据准备依赖。
- `requirements-test.txt`：离线测试依赖。

先在仓库根目录执行 `python metrics/scripts/verify_delivery.py` 和 `python -m pytest host/app/tests -q`。完整 635 仿真输入与命令见 `../../docs/reproduction.md`。

本轮未创建新环境或安装依赖。真实 Overlay、相机、串口、服务和车辆动作不在当前交付范围内。
