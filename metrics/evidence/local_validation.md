# 本轮本地离线验证记录

日期：2026-10-28
执行环境：Windows，Git Bash，工作区为本次新建交付副本。未连接或操作 KV260、相机、串口和车辆，未安装依赖。

## 结果

| 检查 | 状态 | 实际结果 |
|---|---|---|
| 白名单复制与 manifest | 已验证 | `metrics/scripts/build_delivery.py` 退出 0，逐文件记录来源、大小和 SHA-256 |
| 交付离线校验 | 已验证 | `metrics/scripts/verify_delivery.py` 退出 0；覆盖清单哈希、Python 语法、Markdown 本地链接、GitHub 大小限制、绝对 Windows 路径和高置信凭据模式 |
| 指标重建 | 已验证 | `metrics/scripts/build_metrics.py` 从随附证据生成 50 行指标 |
| 车端打包 | 已验证 | `host/deploy/make_bundle.py` 退出 0，生成 36 个 tar 成员（含 MANIFEST） |
| 车端包哈希 | 已验证 | 35 个清单文件的内部大小/SHA-256 全部一致，外置 tar SHA-256 一致 |
| 纯离线 pytest | 已验证（开发主源） | `test_packet.py + test_car_loop.py`：250 passed，0.49 s |
| HLS 主机 C++ | 已验证（交付副本） | TDM g++ 10.3.0 重新编译；4 cases，172,800 elements，0 mismatches |
| PL Python/ORT 回归 | 未执行 | 项目 venv 缺 pytest，系统 Python 缺 cv2；本轮不安装依赖 |
| HLS/Vivado/板上复验 | 未执行 | 本轮只做文件整理和离线验证，历史板测状态见各自证据 |

## 实际命令

```bash
python metrics/scripts/build_metrics.py
python metrics/scripts/build_delivery.py
python metrics/scripts/verify_delivery.py
python host/deploy/make_bundle.py
python -m pytest -q ../pynq_ffn/tests/test_packet.py ../pynq_ffn/tests/test_car_loop.py
```

HLS 主机构建使用本机已有的 TDM g++ 10.3.0，输入为 `fpga/src/conv_front_int8.cpp`、`testbench.cpp` 和 `fpga/src/vectors/`；输出在已忽略的 `build_runs/`。tar 包另用 Python 逐成员核对 `MANIFEST.json` 和外置 `.sha256`。

## 口径说明

- 250 项 pytest 在开发主源运行；交付复制文件另由 manifest 核对。部分 supervisor 测试使用主源的可选模块，本交付不包含 roadsign supervisor，因此这 250 项不称为本包全覆盖。
- 第一轮交付自检发现含旧 Windows 路径的非必要证据、一个文档链接和路径检测误判，已在交付副本中修正或移除；原始开发主源和历史证据未改。
- 离线通过不等于竞赛、板卡或实车验收完成。
