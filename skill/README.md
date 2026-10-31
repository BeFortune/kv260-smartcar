# 可复用 PYNQ 技能包

更新：2026-10-30。本目录是竞赛 3.3 要求的技能包正文，仓库根 `skill/` 是唯一权威源；交付副本由同步步骤生成。它把大模型协作中可复用的检查方法、构建门禁、PL 调用方法和失败教训分开保存，**不把文件存在当成硬件验收**。

## 怎么使用

1. 先读 [amd-pynq/SKILL.md](amd-pynq/SKILL.md)，按当前阶段只加载一个通用模块。
2. 换题目或换板卡时，只复用 A/B/C 的步骤、判据和记录格式；先重新核对器件、ABI、缓冲区和时钟，不能照搬案例常量。
3. 需要本队接法时再读 `cases/`。案例中的地址、张量、USB 口位、哈希和报文都不是通用默认值。
4. 每次执行留下日期、机器、工具版本、命令、退出码、输入/产物哈希和证据路径。没有证据就写“未验证”或“未执行”。
5. 阅读技能不授权导入 PYNQ、下载 Overlay、改时钟、打开相机/串口、启动控车进程或让电机动作；这些操作须另行确认。

## 目录导航

```text
skill/
  README.md
  amd-pynq/
    SKILL.md
    modules/
      a_env_check.md             A：环境检查与排障（通用）
      b_hls_vivado_build.md      B：HLS/Vivado 构建与产物核对（通用）
      c_python_pl_call.md        C：Python 调用 PL 与验收（通用）
      d_pitfalls.md              触发条件、排查步骤和根因
      e_integrated_sim.md        E：YOLO+PL+PS 同帧离线全链验收
    cases/
      kv260_conv_front_int8.md   本队卷积前端案例（专用）
      smartcar_protocol.md       本辆车通信案例（专用）
```

## 状态总表

状态只使用“已验证 / 失败 / 阻塞 / 未执行”；“已验证”后面仍必须看范围和日期。大部分通用方法已在本队环境验证；没有第二块板卡或他人环境复现证据，不能写成跨板复现成功。

| 文件 | 状态 | 范围与边界 |
|---|---|---|
| `README.md` | 已验证 | 2026-10-30 离线结构、七字段、链接和脱敏检查通过；不代表硬件验收 |
| `amd-pynq/SKILL.md` | 已验证 | 2026-10-30 frontmatter 与路由离线检查通过；不代表硬件验收 |
| `modules/a_env_check.md` | 已验证 | 本队已有环境/口位/时钟观察；换板复现未执行 |
| `modules/b_hls_vivado_build.md` | 已验证 | 当前核的 C/RTL/OOC 证据；从零重建和系统签核不能由此推出 |
| `modules/c_python_pl_call.md` | 已验证 | 真实 PL 数值与录像计时；实时传感、串口和长时稳定未执行 |
| `modules/d_pitfalls.md` | 已验证 | 失败记录与部分离线门禁；未把恢复模板当板上验证 |
| `modules/e_integrated_sim.md` | 失败（全链验收） | 635 组实际完成并留证，但 1 次过期导致 42 个锁存 tick；方法复核有效，不写成通过 |
| `cases/kv260_conv_front_int8.md` | 已验证 | 分层指标见正文；完整 120 帧 RTL、实时链路和实车未执行 |
| `cases/smartcar_protocol.md` | 未执行 | 离线格式与用户确认配置已记录；当前车端物理串口、电机和实车未执行 |

## 后续努力方向

实时三摄、真实串口、电机闭环，以及缩小 PS+PL 与 CPU INT8 的端到端差距，作为后续努力方向；这些目标不改变当前“未执行”或“失败”的验证状态。

## 证据入口

- 当前交付范围：[交付 README](../README.md)、[状态表](../STATUS.md)、[指标总表](../metrics/metrics.csv)。
- 系统与接口：[架构](../docs/architecture.md)、[接口](../docs/interface.md)、[硬件搭建](../docs/hardware_setup.md)。
- 构建与模块验证：[HLS 入口](../fpga/src/README.md)、[报告索引](../fpga/report/README.md)、[HLS/OOC 摘要](../fpga/report/hls_fixed_acceptance01.json)。
- 当前全链：[635 组摘要](../metrics/evidence/integrated_sim/summary.json)、[独立复核](../metrics/evidence/integrated_sim/independent_verification.json)、[反号与转弯指标](../metrics/evidence/integrated_sim/direction_metrics.json)。
- 保底附录：[固定左说明](../docs/fixed_left_fallback.md) 与 [固定左证据](../metrics/evidence/fixed_left/final_status.json)。
- 历史失败教训：开发主源 `pynq_ffn/docs/04_验证记录.md` 第 17、19、20、22 节。它是历史证据，不与当前 bit/HWH、模型或指标混用；当前交付副本不冒充包含整份历史工程。

## 七字段与脱敏约定

每个通用模块、案例和踩坑项都写“适用场景、输入 / 前提、使用方法、输出、失效条件、已验证效果、来源失败或观察”。验证层级分开写 HLS C 仿真、RTL 协同仿真、单 IP OOC、系统实现、真实 PL 和整机链路。技能包不放密码、私钥、许可证正文、未公开权重或原始数据。

## 本入口的七字段

- **适用场景：** 需要查找本队 PYNQ 方法、验证状态或专用案例时。
- **输入 / 前提：** 先明确任务阶段和授权边界，再选择对应模块；阅读不要求设备。
- **使用方法：** 从 `amd-pynq/SKILL.md` 路由到 A/B/C/D，必要时再读案例。
- **输出：** 得到可执行的检查步骤、证据索引、状态表和未执行边界。
- **失效条件：** 换板、换 ABI 或没有对应证据时，案例常量和历史结论不能直接复用。
- **已验证效果：** 2026-10-30 离线结构与链接检查通过；硬件效果只在各模块注明范围内成立。
- **来源失败或观察：** 原交付目录只有占位说明且正文缺失，因此补建唯一权威源并同步副本。

## 本轮离线检查记录

- 2026-10-30 已完成当时 8 个文件的结构、七字段、相对链接、YAML 和敏感信息检查；本次新增第 9 个 `modules/e_integrated_sim.md`，并按最新全链结果更新案例和链接。最新交付复核见 `../metrics/evidence/local_validation.md`。
