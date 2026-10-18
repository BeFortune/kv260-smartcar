# 第二轮归档勘误与结果口径

日期：2026-10-18。本文件是对既有原始证据的补充说明，不替换或重写旧日志、执行记录、冻结源码及压缩包。

## 1. 辅助记录把器件容量误写成了本核用量

**已验证。** `run_hls_stage.py` 在综合成功后使用 `tree.iter()` 扁平收集 XML 标签。`AreaEstimates/Resources` 和后面的 `AreaEstimates/AvailableResources` 都包含 `DSP`、`BRAM_18K`、`FF`、`LUT`、`URAM`；后出现的器件容量覆盖了前面的核用量。

因此，`hls/remote_final/*/records_v1/csynth.finished.json` 中的 `report_summary` 资源字段不作为核资源结论。这不改变记录里的真实命令、输入 SHA、退出码、时间或原始 XML SHA；也不代表综合失败。历史记录和本轮已执行的 runner 均保留原样。

v2 原始报告的准确口径为：

| 来源 | BRAM（18K等效块） | DSP | FF | LUT | URAM |
|---|---:|---:|---:|---:|---:|
| `v2/work/hls/syn/report/csynth.rpt` 顶层行 | 142 | 333 | 64,719 | 58,866 | — |
| `v2/work/hls/syn/report/ffn_accel_csynth.xml` 的 `AreaEstimates/Resources` | 142 | 333 | 64,719 | 58,860 | 0 |
| 同 XML 的 `AreaEstimates/AvailableResources`（器件容量，非用量） | 288 | 1,248 | 234,240 | 117,120 | 64 |

XML 与文本报告的综合 LUT 相差 6，原因**未验证**；不强行声称两个字段完全一致，不推断差值的内部来源。综合资源表继续明确引用 `csynth.rpt`，实现资源则引用布局布线后的报告。原始报告都在 `hls/remote_final/`，不为消除差异而修改。

未来若修复 runner，应分别读取明确的 XML 节点，并为 Resources/AvailableResources 同名标签建立回归测试；此修复**未执行**，也不把未来修复冒充本轮已经使用的版本。

## 2. BRAM 单位和 AXI 端口

**已验证。** v2 布线后 BRAM 的 `131` 是 18K 等效块，不是 131 个 36K tile：

- 64 个 RAMB36 + 3 个 RAMB18 = 131 个 18K 等效块。
- 换算为 65.5 个 36K Block RAM Tiles，占目标器件 144 个 tile 的约 45.49%。
- 布线后资源为 LUT 51,146、FF 46,657、DSP 333；这些是实现阶段值，不与综合估计混用。

IP 有两个 memory-mapped AXI master（内存映射主接口）：`m_axi_gmem0` 和 `m_axi_gmem1`，均为 64 位地址、32 位数据；另有 AXI-Lite 控制接口。四个 AR/AW 地址信号不等于四个 AXI master，也不是 AXI-stream DMA 设计。

## 3. 已完成和未完成的边界

- **已验证：** KV260 纯 CPU 的 ORT 六条件正反序实验；最终生产 Runtime 的 117 帧直接复验；r04 v2 主机数值、完整 HLS C 仿真、小范围 RTL、独立 IP OOC（脱离完整系统的布局布线）。
- **失败：** r04 v1 的目标时序。该失败版本和现场保留，不用 v2 结果覆盖。
- **未执行：** v2 完整 659/660-token RTL、新 PS+PL 系统集成及 bit/hwh/xsa、新核编程与板测、新软件和新核的组合性能、实时三摄及实车测试。

OOC 已结束并返回退出码 0，不再是“正在运行”。目标 10 ns，WNS +2.435662 ns、WHS +0.034 ns，未布线网络和路由错误均为 0；但外部输入/输出延迟约束不完整，不能作为 PS+PL 完整系统接口的时序签核。

ORT 约 5.3 倍改善是相对原来多会话空转争用严重的切图配置；最终生产 split-off 的 P50 为 79.6322 ms，仍慢于同轮完整图 CPU 的 65.2979 ms。HLS 约 3.25 倍是相对 r03 的静态内核周期比，不是板上整模型收益。二者不能相乘，也不能据此宣称已达到 15 Hz。

## 4. 归档与完整性

最终状态和本次本地逐 SHA 检查见 [final_status.json](final_status.json)，生成方法见 [finalize_round2.py](finalize_round2.py)。它单独核对源码、冻结包、取回的原始证据和退出码，不覆盖旧报告。

`inputs.npy` 未取回本机；只核对归档中已记录的输入哈希一致性，不声称本机重新哈希了该输入张量。归档中的 `hardware_execution: false` 指这轮未执行 PL 计算，不否认 CPU 测试实际运行于 KV260。
