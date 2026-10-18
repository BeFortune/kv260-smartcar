# 2026-10-18 r03 证据对账（本地只读，未连设备/未改源码）

## 结论

本地可核实部分支持 r03 主张：当前源哈希与给定值一致；r03 host 报告的 19 组合成 + 173 真实帧对 baseline DLL 逐位全等，bit mismatch 与整数轮速差均为 0。但 r02b 不能当旧 r02 二进制的复现；cosim 1,279,573 cycles 与 95 分钟在本地无任何证据文件；多份清单/文档仍是 4-lane 快照。

## 一、已核实（1 基引用）

1. 源哈希一致：`pynq_ffn/hls/ffn_accel.cpp` = `829c9ec63604e327ad4ea13855b405113ea74fea6a62721d512ded6341b0b9b6`；`ffn_accel.hpp` = `715f0ca11cc5e9920569654f7296c8443a0186f988557bd27ec1af3f476c7c35`。`hpp:8` output_lanes=16，`cpp:47-48` cyclic 16，`cpp:83/109` PIPELINE II=4，`cpp:100-103` 单遍 GELU。
2. `r03_bitwise_payload.json:28-31` 绑定 `candidate_r03_host/ffn_host.dll`，sha256 `5cdddc8f0f6289a8bc2cacd9f25a30e312bc8e8e26bfd0065f74487045d9cfdc`，与盘上文件一致；DLL mtime 20:00 晚于 cpp 19:59、hpp 19:55。
3. 19 组合成（`:33` 起）：19/19 pass，bit_mismatches 合计 **0**，262,656 元素，max_abs 0.0，有限值全真；含 1/660 token 边界、重载/重访、消相消。
4. 173 帧（`:214` 起；4 段 100/53/10/10）：173/173 frame pass。四层逐帧 bit_mismatches 合计 **0**，覆盖 **29,407,232** 元素；speed_integer_mismatches 合计 **0**。逐位比较为 float32 按 uint32 精确比较（`tools/validate_cpp_parallel.py:62`），不是只看 max_abs。
5. 误差口径：层次 `max_abs_error=0.0` 是候选 vs baseline DLL；对 ORT 的 1.04e-7~1.94e-7、pipeline 1.04e-7~1.94e-7 是另一参考（`validate_cpp_parallel.py:183-210`）。最终输出对 golden 最大误差：speed_norm 8.94e-8、speed_raw 6.103515625e-5、region_logits 8.15e-8、region_probs 1.49e-8；整数截断差异 0。
6. 原模型 SHA256 `ad58d1fc9168da94b9ce076ba8cc9754c371ead87974da8bc9cc601ad1650d5b`，zip `52d10ff0ab25d80fa648abb2bd0a308eb0234db12255e30d43d75caba832b30a`；r02b/r03 两份一致（r03`:212-213`）。baseline DLL `9279c4d8…558a` 与盘上一致。
7. `r02b_bitwise_payload.json:29-30` 绑定 `candidate_r02_host/ffn_host.dll` = `5af39904…45f7`，与盘上一致；19+173 结果与 r03 相同（0/0）。

## 二、问题与口径风险

1. 旧 r02 证据失效：`r02_payload_173.json:28-30` 与 `r02_validate_cpp_real.json:5` 记录 `5aba8da8…4474`，同路径现为 `5af399…45f7`。旧二进制已被覆盖、不可恢复，这两份只能标“历史、路径已不对应当前文件”，不要重建或改旧报告。
2. r02b 只绑定 DLL，无编译命令/源哈希记录；目录名仍是 `candidate_r02_host`。r02b 与 r03 DLL 的 `.text` 尺寸 8872 vs 8984，属不同构建；不能声称 r02b 复验了某个 4-lane cpp。旧 r02 的 `5aba8da8` 与新 `5af399` 结果同为全 pass，但不能互证同一源码。
3. 本地不存在 `a09c…` r02 源哈希：`candidate_r02/hls/ffn_accel.cpp` 实为 `c9c2dcbd…`（4-lane、II=1，`candidate_r02/manifest.json` 同）。a09c 仅有口头事实，缺本地证据。
4. r03 payload 未内嵌 cpp/hpp 哈希，源→DLL 链依赖时间戳与远程 r03 记录（`1eea3088…` 源包本地也不存在）。建议后续 payload 增源哈希（本轮不改）。
5. cosim 数字缺失：`1,279,573` 与“95 分钟”在 `ffn_opt_20261017`、README、docs 中均检索不到；`remote_hls_r01.md:29` 仍写“进行中”。若引用须写成：1,279,573 是缩小 30-token 用例的**仿真周期（模拟时间）**，95 分钟是**墙钟**；两者都不是吞吐或实板性能，且 659/660-token 未覆盖。
6. `remote_hls_r01.md:27` 的 11,773,385 cycles、14.7× 是 csynth **静态周期数/静态比值**（@100 MHz≈117.7 ms），不是实板加速；+0.28 ns slack、DSP262/BRAM137 也是静态结果。
7. 清单落后：`delivery_manifest.json:6-8` 仍是 4-lane `c9c2…/d959…`，`:103-114` 写 hls 阻塞、csim/csynth/cosim 未执行；`staged_hls/manifest.json:8-9` 仍 output_lanes=4、requested_II=1。二者是 17:00 快照，不代表 r03。
8. 注意“19 项 testbench（2035 token）”与“19 组合成（2052 token）”是不同测试，勿混用数字。

## 三、需更新文档清单（未改动）

- `pynq_ffn/README.md:3,103,113`（4 lane、未 HLS）
- `pynq_ffn/DELIVERY.md:35`（HLS 未完成）
- `pynq_ffn/docs/04_验证记录.md:613-643`（第 18 节 4-lane；`:635` 未执行）
- `docs/agents/project_context.md:3,44,45,46,49,60`
- `pynq_ffn/artifacts/ffn_opt_20261017/delivery_manifest.json:6-8,103-114`
- `pynq_ffn/artifacts/ffn_opt_20261017/staged_hls/manifest.json:8-9`
- `pynq_ffn/artifacts/ffn_opt_20261017/remote_hls_r01.md:27,29,33`
- `r02_validate_cpp_real.json:5`、`r02_payload_173.json:30`（标注哈希/路径已失效）

## 四、未核实

远程 csim/csynth/cosim 原始日志、ABI 偏移、1,279,573 cycles 与 95 分钟均不在本地；本报告只对本地文件与哈希负责。

## 五、原始证据取回后核对（2026-10-18）

以下补齐第四节中缺失的远程原始证据；上文对本地盘上文件的结论不变。

### 5.1 清单与源哈希

- `hls_evidence/manifest.json` 列 **193** 个文件（另有 manifest 自身，共 194）；逐文件复算 sha256 与字节数：**0 mismatch、0 missing**；6 个可选文件（r01/r02/r03b 的 small TB 与 cosim cfg）已按 `missing_optional_files` 声明未取。
- r01 cpp `c9c2dcbd…`（与本地 staged 4-lane 一致）；r02 cpp **`a09cfaa926f7be604e8d857d60569cdc616b7afb3b0bbad450487c7697ecfc3d`**，与 r01 的 diff 正是删除两处 `II=1` 并把 GELU 改为单遍；r03/r03b cpp `829c9e…`、hpp `715f…`（与本地当前源一致）。
- r03 与 r03b 的 `csynth.xml`（`99d6da8c…`）和 `ffn_accel.v`（`998ec377…`）哈希完全相同 → r03b 是同源再编译/实现，不是新源。

### 5.2 r03 csim / cosim 范围与后检查

- csim 用完整 `testbench.cpp`：19 项全 PASS，含 659/660、权重重载/重访、zero、bias-exact-erf、0/661/UINT_MAX；`CSim done with 0 errors`（`logs/hls_run_csim.log:45-65`），状态 CS_PASSED。
- cosim 用 `testbench_cosim_small.cpp`：12 个 transaction，覆盖 1/4 token、4 套权重、zero、bias、3 个非法计数；**30 valid tokens，无 659/660**（该文件末行自述）。12 项 PASS，结束 `C/RTL co-simulation finished: PASS`（`logs/hls_run_cosim.log:35-47,1027`）。
- `hls/sim/report/ffn_accel_cosim.rpt:11`：Verilog Pass，latency min/avg/max 3/106,608/153,897 cycles，**Total Execution Time 1,279,573 cycles**。这是 12 次事务的仿真周期合计；xsim 记录 simulation time 12,796,375 ns、run elapsed 01:33:57（`xsim.log:457-458`），Vitis 命令 20:17:39→21:52:53（约 95 分 14 秒墙钟）。故 1,279,573 是模拟时间量，不是墙钟，也不是吞吐。
- 后检查：生成 `xffn_accel_hw.h` 偏移仍为 CTRL 0x00、x 0x10/0x14、y 0x1c/0x20、w1 0x28/0x2c、b1 0x34/0x38、w2 0x40/0x44、b2 0x4c/0x50、num_tokens 0x58，六指针 64-bit；`ffn_accel.v:178,190` GMEM0/1 ADDR_WIDTH=64。ABI 未变。

### 5.3 r03b compile / --impl / 资源

- r03b 源同 `829c9e`，在 `D:/f3` 重新 `--compile`：22:31:34→22:32:23 CS_PASSED；`--impl`：22:45:01→22:59:54 CS_PASSED（`hlscompile_summary:203/214`、`hlsrun_impl_summary:226/237`）。
- OOC 实现（`bd_0_wrapper`，非整机 BD）：WNS **2.609004 ns**、WHS **0.032047 ns**、TPWS 0、unrouted 0（`logs/hls_run_impl.log:1821`）；`export_impl.rpt:46-60` “Timing was met”，post-synth CP 5.281 ns、post-route CP 7.391 ns。
- routed 资源：LUT 23,065 (19.69%)、FF 24,044 (10.26%)、DSP 262 (20.99%)、BRAM 66.5 tiles / 133 RAMB18 (46.18%)（`utilization_routed.rpt:36,41,109,125`）。
- csynth 估计：latency 11,773,385 cycles @100 MHz≈0.118 s/660-token 层；BRAM_18K 137 (47%)、DSP 262 (20%)、FF 33,593、LUT 28,420（`ffn_accel_csynth.rpt:80-84`）；FC1（`LOOP_82_9`）与 FC2（`LOOP_108_14`）均实际/目标 **II=4/4**、slack +0.28；GELU（`LOOP_100_12`）II=1、slack +0.24；顶层 Estimated 7.300 ns + Uncertainty 2.70 → slack 0.00。实现后资源低于估计，时序 +2.609 ns。
- **14.7× 仍是静态周期比**：173,252,285 / 11,773,385 = 14.71，不是实板加速，也不是与 1156 ms 板测的比值。

### 5.4 补齐的是哪条链

- `a09c…` 补的是**远程 r02 源身份链**（r02 build 目录 → r02 csim/csynth）；本地 `r02b_bitwise_payload.json` 的 DLL `5af399…` 仍无编译命令/源哈希，不能称其为 a09c 的直接产物；旧 `5aba8da8…` 仍不可恢复。
- `829c9e…` 补的是 **r03 源→综合→实现链**：本地当前源 = r03 = r03b 源，且 r03/r03b 的 csynth 与 RTL 哈希一致。本地 host DLL `5cdddc…` 与 cpp 的绑定仍仅靠时间戳与 payload DLL 哈希，无编译记录。

### 5.5 新发现的口径问题

1. `hls_config_cosim_small.cfg` 首行写 “One-token smoke … NOT covered”，与实际 12 项/30 valid-token TB 不符（仍无 659/660）；引用以 TB 为准，cfg 注释应改。
2. `remote_hls_r01.md:27` 写 LUT 28,422，报告为 28,420；`:29` 仍写“进行中”，应更新为已完成（Pass，95 分 14 秒）。估计资源 47/20/14/24% 与实现 46.18/20.99/10.26/19.69% 并列时须注明“估计 vs 实现”。
3. 193 份清单不含 r02 `testbench_cosim_small.cpp` 等可选文件，不是缺失失败。

### 5.6 可用于最终报告的小结

2026-10-17 远程 2026.1 工具链完成 r03（16 lane、II=4）全流程：完整 C 仿真 19/19 PASS；缩小范围 RTL cosim 12 项 / 30 token PASS（1,279,573 仿真 cycles，约 95 分钟墙钟，未覆盖 659/660）；r03b 同源 `--compile` 与 `--impl` 均 CS_PASSED，OOC WNS +2.609004 ns、WHS +0.032047 ns，LUT 23,065 / FF 24,044 / DSP 262 / BRAM 66.5 tiles；静态 11,773,385 cycles，为旧核 173,252,285 的 14.7×。尚未生成系统 BD/bit/hwh，未上板，不能报实板加速或实时闭环。
