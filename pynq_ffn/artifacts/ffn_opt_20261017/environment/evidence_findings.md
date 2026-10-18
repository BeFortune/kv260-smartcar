# FFN 优化前证据口径核对

日期：2026-10-17。范围：只读核对；未修改任何原始证据、源码、README 或 docs。本文只记录两处旧口径问题及其原始位置。

## 1. `golden_warm_ms` 实际上是唯一一次 golden 调用（warmup 调用本身）

原始代码 `pynq_ffn/artifacts/board_20261015/board_ffn_test.py:303-305`：

```python
303      golden = Runtime(ROOT / "bundle", mode="golden", threads=4)
304      golden.run(images_list[0])
305      evidence["golden_warm_ms"] = golden.last_timings_ms
```

`stage_timing()` 中 golden 只执行这一次 `run()`，之后没有第二次 golden 调用，也没有单独的正式计时循环。因此落盘值 `pynq_ffn/artifacts/board_20261015/pl_timing_20261015.json:248-249` 的 `golden_warm_ms.golden = 71.18068498675711` ms 是**首次/唯一一次 golden 调用的墙钟时间，即 warmup 调用本身**，不是“warmup 之后测得的稳定帧”。

受影响表述：

- `pynq_ffn/docs/04_验证记录.md:598`：“golden（原始整图 CPU，4 线程） | warm 后单帧 71.19 ms | 一次 warm 计时，非均值”。“warm 后”不准确，应改为“单次 golden.run 调用（唯一一次 warmup 调用）计时，n=1”。
- `pynq_ffn/artifacts/board_20261015/README.md:10`：“golden CPU ~71 ms/帧”。没有测过 warm 后的正式帧，不能表述为稳定每帧耗时。
- `pynq_ffn/README.md:112`：“golden 整图 CPU（~71 ms）”。同上，只能作为单次冷/首次调用参考。

影响：71.19 ms 含首次调用开销，不能直接当作稳态 CPU 基线与 15 Hz 余量依据；要得到稳态值，必须先 warmup，再对多次正式调用单独计时。

## 2. 173,252,285 cycles 与板上 1156 ms 不是同一口径

- HLS 静态估计：`pynq_ffn/artifacts/hls_20261015/retry04/hls_complete/tool_outputs/hls/syn/report/ffn_accel_csynth.rpt:32`，`Latency (cycles) max = 173252285`，目标时钟 `10.000 ns`，即 **1.733 s @100 MHz**。
- 板上实测硬件等待：`pynq_ffn/artifacts/board_20261015/pl_layers_20261015.json:107` 与 `:139`，camera_0/camera_1 的 `hardware_wait_ms` 分别为 **1156.416411** 与 **1156.409387** ms。
- 实际 PL 时钟：`pynq_ffn/artifacts/vivado_20261015/retry04/overlay/ffn_overlay_wrapper.hwh:3098-3101`，`PL0_REF_CTRL__ACT_FREQMHZ=99.999001`、`__DIVISOR0=10`、`__FREQMHZ=100`；Vivado 时序报告同为 10 ns。

按 100 MHz 换算，1156.4 ms 约为 **115.6M cycles**，是静态最大估计 173.25M cycles 的 **66.7%**。两者不是同一个数：`173252285 / 1.1564 s ≈ 149.8 MHz` 只是把两个不同测量凑到 150 MHz 的巧合，和 HWH/Vivado 的 100 MHz 事实矛盾。1-token cosim 的 345,767 cycles（`pynq_ffn/docs/04_验证记录.md:444`）对板上约 3.5 ms（同文件 `:580`）也支持 100 MHz。

## 3. 2026-10-18 运行时实读后的更正

**第二节将 HWH 的100 MHz 当作“实际PL时钟”、把150 MHz称作“巧合”的推断不成立。** 2026-10-18 新核加载后实际读取 `Clocks.fclk0_mhz=149.9985`、PL0寄存器 `0x01010a00`（DIV0=10、DIV1=1）及 IOPLL寄存器 `0x00015a00`（FBDIV=90、DIV2=1），板上源时钟约1.5 GHz，而非HWH预设的约1GHz。内核clock缓存仍显示约100MHz，也不是当前寄存器实测值。

这直接说明“设计/HWH标称100MHz”不能替代运行时PLL/分频核实。旧retry04的实际源PLL及实时fclk读数未保留足够证据，因此其真实频率未知；可以提出旧1156ms可能与约150MHz有关的假设，但不能追认为已证实，也不能按100MHz把它强换算成115.6M实测cycles。第二节数字保留为当时错误推断的纠错轨迹，不用于正式性能报告。

正确用法仍是分别引用HLS静态cycles和板上ms，并在任何正式性能测试前核实并约束实时PL时钟。当前新核150MHz板测原始记录完整保留，但100MHz实现条件下的正式板测验收为“阻塞”。证据见 `../../ffn_opt_20261018/session_evidence/clock_idle_read_result.json` 的归档副本以及 `../../ffn_opt_20261018/README.md`。
