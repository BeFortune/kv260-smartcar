# HLS 源码与复现入口

本目录只包含当前 `conv_front_int8` 基线：C++/HPP、量化权重与参数、整数参考、testbench、四组测试向量和 `build/hls.cfg`。不包含早期 FFN、本机 `.exe`、Python 缓存或 AMD 工具生成的庞大工程。

## 数值契约

- 输入：`int8[3][3][66][200]`，左/前/右。
- 输出：`int8[3][64][9][25]`。
- 保留 Conv Q、整数 ReLU、Relu Q 的独立舍入；正负 ties 均按最近偶数处理。
- `params.json` 与 `weights_gen.hpp` 必须和当前模型配套，不能与其他候选混用。

## HLS 工具流程

目标器件在 `build/hls.cfg` 中为 `xck26-sfvc784-2LV-c`，时钟目标 10 ns。推荐在 Vitis 2026.1 工具机上，从本目录的 `build/` 依次执行 csim、csynth、cosim，并为每轮使用新的 work/record 目录。

以下是**命令模板，未在本交付副本重新执行**；工具可执行文件名和工作目录需先在本机核实：

```bash
vitis-run --mode hls --config hls.cfg --work_dir NEW_WORK_DIR --csim
v++ --compile --mode hls --config hls.cfg --work_dir NEW_WORK_DIR
vitis-run --mode hls --config hls.cfg --work_dir NEW_WORK_DIR --cosim
```

`NEW_WORK_DIR` 是占位符，三条命令需替换为同一个本轮新目录后再执行。csim/cosim 非零、csynth 非零或报告缺失都应停止；不要把历史 4 事务 RTL 结果写成本次全量验证。

## 已有证据的范围

- HLS C：124 组，0 字节差异。
- RTL：4 个完整三摄事务，0 字节差异。
- 综合静态估计：412,297 cycles/三摄事务。
- 真实 KV260 核等待 P50：4.1608 ms。

这些数字属于各自阶段，不能互相替代。报告见 `../report/`。
