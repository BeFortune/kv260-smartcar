# 面向低算力平台的视觉自动驾驶车：YOLO + PS + PL

本仓库是 2026 年 AMD FPGA 创新设计赛道 **3.3 自主选题初级组** 的最新交付快照。唯一主方案为：**conv_front_int8 PL + PS partB + 真实 YOLO + 控制层 v2**。固定左 CSV 仅作独立保底附录。旧 FFN、旧仲裁和其他候选只留在 Git 历史、历史分支与 Pull Request 中。

## 最新结果先说结论

已完成 **635 组同帧全链离线仿真**：整数参考 PL、PS partB、真实 YOLO、v2 决策和 21 字节报文均实际执行。635/635 样本、635 次核调用、报文全部合法，6 个红灯观测全部停车。

**验收状态仍为失败**：第 4 次运行出现 1 次 `driver_stale`，帧龄 0.5462 秒，超过预先固定的 0.5 秒诊断上限；故障随后锁存 42 tick。没有调宽窗口或把锁存 tick 数写成 42 次独立超时。

转弯问题没有消除：以旧教师为参照，`>40` 的 198 组中，整数参考驾驶强反号 27 组（13.64%），幅度减半 150 组（75.76%），绝对幅度保留中位数 0.2663。它们表示模型间分歧，不是独立实车错误率。

本交付不包含 HLS 全链、真实 PL、实时相机/串口/电机或固定赛道验收结论。

## 官方参考目录

```text
README.md
docs/
  architecture.md          系统架构与数据流
  interface.md             YOLO / PL / PS / v2 接口
  hardware_setup.md        硬件与接线边界
  fixed_left_fallback.md   固定左保底附录
  reproduction.md          从零复现步骤
fpga/
  src/                     当前 conv_front_int8 源码与向量
  build/                   同源 bit / HWH
  report/                  HLS C、RTL、OOC 模块证据
host/
  model/                   partB、params、整数参考、YOLO身份说明
  app/                     PS、YOLO、v2、集成仿真与固定左源码
  deploy/                  依赖清单与离线运行说明
metrics/
  metrics.csv              最新指标汇总
  logs/                    635组逐样本全链记录
  scripts/                 清单、指标与校验脚本
  evidence/                小型机器证据
skill/
  README.md                可复用仿真工作流与失效条件
report/
  design_report.md         最新设计报告
delivery_manifest.json     逐文件来源、大小和 SHA-256
```

## 离线复核

在仓库根目录、已有 Python 依赖的 Bash 环境中执行：

```bash
python metrics/scripts/verify_delivery.py
python -m pytest host/app/tests -q
```

当前保留测试共 **342 项通过**。完整 635 组仿真还需要原始图像、固定输入合同和真实 YOLO 模型；这些原始录像和第三方 YOLO 不直接放在仓库中。具体输入身份、命令和停止条件见 [docs/reproduction.md](docs/reproduction.md)。

## 报告与证据

- [最新设计报告](report/design_report.md)
- [全链仿真摘要](metrics/evidence/integrated_sim/summary.json)
- [独立复核](metrics/evidence/integrated_sim/independent_verification.json)
- [反号与转弯幅度](metrics/evidence/integrated_sim/direction_metrics.json)
- [固定左保底说明](docs/fixed_left_fallback.md)

## 许可证

原创代码采用 [MIT License](LICENSE)。模型、录像、训练数据、第三方依赖及 AMD/Xilinx 生成内容不当然由 MIT 覆盖。真实 YOLO 使用固定上游提交和 SHA-256 获取，公开仓库前仍须核查全部当前内容及历史/PR 的再分发权。
