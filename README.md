# KV260 三摄智能车：PS + PL 推理工程

本仓库是 2026 年 AMD FPGA 创新设计赛道 **3.3 自主选题初级组** 的当前交付快照。目标是把原 NUC 三摄驾驶模型迁到 KV260：PS（处理器）负责采图、预处理、后半段推理与通信，PL（可编程逻辑）执行前三层 INT8 卷积，STM32 继续负责电机执行。

**状态日期：2026-10-28。当前主线是 `kv260car + conv_front_int8`，不是早期 FFN 分图卸载。** 旧 FFN 实验保留在远程历史分支，其源码、bit/HWH 和指标不与本目录混用。

## 已验证与尚未完成

| 项目 | 状态与范围 |
|---|---|
| HLS C 仿真 | 已验证：124 组，5,356,800 个输出字节，0 差异 |
| RTL 协同仿真 | 已验证：4 个完整三摄事务，172,800 字节，0 差异；120 帧全量 RTL 未执行 |
| 单 IP OOC 实现 | 已验证：100 MHz 目标，WNS +1.806 ns、WHS +0.035 ns；不是整系统时序签核 |
| KV260 真实 PL 数值 | 已验证：2026-10-21，117 帧、5,054,400 字节，PL 对整数参考 0 差异 |
| 同轮性能 | PS+PL P50 19.65 ms，CPU FP32 25.50 ms，P50 约 1.30 倍；**CPU INT8 整图 P50 17.92 ms，仍更快** |
| 15 Hz 推理调度 | 已验证：2026-10-22，录像输入 20 秒 300 帧；不含实时相机、JPEG 解码或串口闭环 |
| 当前 PL 路线实车 | **未执行**。实时三摄 + PL + 串口 + 电机整链不能由上述结果推导为已验收 |
| 新模型现场任务效果 | **失败/阻塞**：现场大转弯差速明显不足，当前不能安全替换旧模型 CPU 保底路线 |

完整数字见 [metrics/metrics.csv](metrics/metrics.csv)，状态边界见 [STATUS.md](STATUS.md)，报告见 [report/design_report.md](report/design_report.md)。

## 目录

```text
README.md
docs/                    架构、接口和硬件搭建
fpga/
  src/                   当前 conv_front_int8 HLS 源码与测试向量
  build/                 同源 bit/HWH 与校验说明
  report/                HLS、OOC 和板测报告
host/
  model/                 PS 后半段 ONNX、量化参数和整数参考
  app/                   可运行的 ps/ 与 tools/car/ 程序
  deploy/                打包入口、依赖清单和部署说明
metrics/
  metrics.csv            指标汇总及证据路径
  logs/                  精选原始板测日志
  scripts/               交付、指标和校验脚本
  evidence/              小型 JSON/Markdown 证据
skill/                   技能交付状态与可复用清单
report/                  设计报告草稿
delivery_manifest.json   逐文件来源、大小和 SHA-256
```

这是按官方参考结构整理的交付副本；开发主源仍是相邻的 `pynq_ffn/`。`host/app/` 保留程序原有的相对目录，避免为了换目录名拆坏运行路径。

## 本机离线复核

前提：Windows Git Bash 或 Linux Bash，Python 3，仓库根目录为当前工作目录。以下命令不导入 PYNQ，也不访问板卡、相机、串口或车辆。

```bash
python metrics/scripts/verify_delivery.py
python host/deploy/make_bundle.py
```

第一条核对交付清单、文件哈希、文档链接和文件大小。第二条按当前目录重新生成车端 tar 包及外置 SHA-256，输出到已被 Git 忽略的 `host/deploy/dist/`。生成包不等于已在板上安装或验证。

HLS 复现入口见 [fpga/src/README.md](fpga/src/README.md)；车端安装与安全边界见 [host/deploy/README.md](host/deploy/README.md)。任何 Overlay 下载、串口发送、服务启动和车辆动作都需要单独现场授权。

## 许可证与第三方材料

原创代码采用 [MIT License](LICENSE)。模型、训练数据、录像、第三方依赖以及 AMD/Xilinx 生成内容不当然由该许可证覆盖。当前快照不包含原始训练集、现场录像、整板备份或第三方运行库；公开仓库前仍须逐项确认模型与证据的再分发权。
