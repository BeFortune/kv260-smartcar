# 2026-10-17 远程 HLS 构建 r01→r03（4090内网，Netcatty 通道）

## 环境

- 机器：4090内网（Netcatty 会话，主机名 JasonWenDsek，192.168.110.248，与 KV260 同网段）
- 工具：`D:/Learning/AMDDesignTools/2026.1/{Vivado,Vitis}/bin`（Vivado 2026.1 Build 6511674 / HLS Build 6493734，ENTERPRISE 许可有效期至 2026-12-14），不在 PATH，按完整路径调用
- 构建 root：`D:/ffn_build_opt/{r01,r02,r03}/`（新目录；历史 `D:/ffn_build` 未触碰）

## r01（4 通道 + 强制 II=1）

- 源包 sha256 `c5bdeb6a…`，远程逐文件哈希一致
- **C simulation 通过**：`CS_PASSED`，19/19 PASS、0 errors、24 s；首次在 `hls::erff` 分支下验证候选数学
- **C synthesis 完成但有 II=1 时序陷阱**：660-token 27,553,325 cycles（名义 6.29×），FC1/FC2 归约 II=1 但 **Slack −18.43 ns**（关键路径 ~28 ns）；GELU 组内串行 4×erf 每 token ~23.8K cycles；资源 DSP 40 / BRAM 136
- **结论：不进入 cosim/实现。教训：csynth 的 achieved II 必须与 Slack 同读，II=1 配负 slack 是假性能**

## r02（4 通道，去掉 II 请求，GELU 单遍流水）

- 改动：删除两处 `PIPELINE II=1`；GELU 从组内串行改为整个 256 维单遍流水 pass（每输出数学顺序不变）
- 本地验证：19 组合成 + 173 帧四层 **逐位 0 差异**、整数轮速 0 分歧（`r02b_bitwise_payload.json`）
- 远程 csim 19/19 PASS；csynth：34,287,305 cycles；GELU pass II=1 干净（erf 流水实例 242 DSP、Slack +0.24）；**FC1 归约自动选 II=3、Slack −6.00**（无 II 请求时 HLS 仍为吞吐牺牲时序）→ 不合格

## r03（16 通道 + 显式 II=4）——当前候选

- 改动：`output_lanes=16`、权重 cyclic factor=16、两处归约显式 `PIPELINE II=4`（匹配 fadd_32ns 延迟 4）；每输出仍按 i 递增串行累加
- 本地验证：完整 testbench 19/19 PASS；19 组合成 + 173 帧四层 **逐位 0 差异**、整数轮速 0 分歧（`r03_bitwise_payload.json`）；源包 sha256 `1eea3088…`，远程哈希一致
- 远程 csim：19/19 PASS、0 errors
- **C synthesis（时序干净）**：660-token **11,773,385 cycles**（@100 MHz ≈ 117.7 ms/层；旧核 173.25M cycles / 实测 1156 ms → **周期数 14.7×**）；FC1/FC2 归约实际 II=4、pipeline Slack **+0.28**；GELU pass II=1、Slack +0.24；顶层 Slack 0.00；资源 DSP 262 (20%) / BRAM 137 (47%) / FF 33593 / LUT 28422
- **ABI 核对（生成 RTL 实测）**：`xffn_accel_hw.h` 全部偏移与 retry04 一致（CTRL 0x00、x 0x10/0x14、y 0x1c/0x20、w1 0x28/0x2c、b1 0x34/0x38、w2 0x40/0x44、b2 0x4c/0x50、num_tokens 0x58）；六指针 BITS=64、num_tokens=32；`ffn_accel.v` 中 `C_M_AXI_GMEM{0,1}_ADDR_WIDTH=64`。PYNQ 后端无需改动
- **RTL cosim（进行中）**：缩小范围 30-token 版（复用 retry04 的 `testbench_cosim_small.cpp`，覆盖 1/4 token、四套权重重载/重访、zero/bias-only、非法计数）；C TB 侧 12 项已 PASS，XSIM RTL 仿真运行中。旧核因每 token 26.2 万周期无法完成同等 cosim，r03 每 token ~1.78 万周期使其可行。**659/660-token RTL 用例仍不在覆盖内，如实记录**

## 待办

- cosim 完成确认 → `--impl` 导出 IP → Vivado 系统 BD（重用 retry04 脚本流程，IP 换 r03）→ bit/hwh → KV260 板测（单独确认板卡访问）

