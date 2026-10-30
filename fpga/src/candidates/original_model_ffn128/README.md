# 128 输出通道 FFN 候选（仅主机验证）

该候选基于旧 r04 v2，将输出并行通道从 16 提到 128，保留 FP32 累加顺序、exact-erf GELU 和 AXI 接口。

已有证据仅为 g++ 主机比较：38 对有效调用、700,672 个 float32，位差 0。**未执行 Vitis HLS C 仿真、综合、RTL、资源/时序实现，也没有新 bit 或真实 PL 结果。** `build_hls.cfg` 是候选构建输入，不代表构建已经通过。

此目录不是当前 `conv_front_int8` 的源码，不得混用其 bit/HWH、模型或性能指标。机器可读结果见 `metrics/evidence/original_model/kernel128_test_results.json`。
