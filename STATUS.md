# 当前路线与状态（2026-10-19）

## 结论

四轮在真实 KV260 上完成的 FFN 卸载都没有跑赢纯 CPU。团队决定不再把“独立 FFN 卸载”作为加速主线，改为两条并行研究路线：

- **路线 A：新建更适合 FPGA 的 CNN 模型**（`pynq_ffn/artifacts/model_train_*`、`model_review_v2_20261019`）。
- **路线 B：改造 Transformer 的硬件落点/流水线**（当前文档化方案是 `pynq_ffn/docs/05_pl_reselection_plan_20261018.md`：PL 做归一化、布局、patch Conv 与嵌入，attention 与 FFN 留 CPU）。

两条路线都还没有产生“已上板、已加速、达到 15 Hz”的结论。

## 1. 为什么转向：FFN 板测事实

同一 KV260、录像离线输入、117 帧、warmup 3、4 线程；单位 ms。

| 版本 | 日期 | P50 / 对照 | 结果 |
|---|---|---|---|
| retry04 串行核 | 2026-10-15 | split-PL 整帧约 2497，golden 约 71 | 慢，失败 |
| r03 @150 MHz | 2026-10-18 | split-PL 301.1576，golden 65.5679 | 慢 4.59 倍，失败 |
| r03 @100 MHz | 2026-10-18 | split-PL 372.2187，golden 64.7386 | 慢 5.75 倍，失败 |
| r04 v2 @100 MHz | 2026-10-18 | split-PL 170.030144，split-ORT 74.866631，golden 65.210875 | 慢 2.607 倍，失败 |

另外，2026-10-17 四路输出候选只在主机验证；当时 HLS 工具阻塞，未上板，不计入板测。

慢因不是单一 DDR/Python：r03 有效吞吐约 4 MAC/clk；r04 v2 的 camera 层 PL wait 约 40.93 ms/层，同轮 CPU 约 7.7 ms/层；调用边界、搬运和 PS 剩余计算都计入。详细分解见 `pynq_ffn/artifacts/r04_v2_board_20261018/board_findings.md` 与验证记录第 22 节。

## 2. 路线 A：新建 CNN 模型

目标：三摄输入、连续卷积为主、参数和 MAC 可控，便于 HLS/DPU 流水线化。

- **v1（2026-10-18）**：共享 5 层 Conv+ReLU、三摄特征拼接、小型回归头；528,522 参数，静态约 39.85M MAC/组三摄。test MAE 38.9851，优于固定 (200,200) 的 43.8792，但小差速区间反而更差。
- **v2（2026-10-19）**：网络不变，改用六包 30,373 条、按 `archive+segment` 分层，无独立 test；val raw MAE 9.480974。与原 Transformer CPU 逐帧比较：左右总体平均绝对差 25.2791、RMSE 33.3251；两侧都 ≤5 的帧 16.03%，整数完全相同的仅 13/6,202（0.21%）。不能称数值等价或直接替换。
- **离线复查（2026-10-19）**：84 个验证段/6,202 条全量回放；发现大差速与连续反向失配、150 基础值片段偏向 200、相邻输出突跳，以及原左摄 JPEG 花屏/内容断层。没有独立 test，也没有实车安全验收，不建议直接替换车上模型。
- **未执行**：CNN 的 HLS/C 仿真、DPU/Vitis AI 编译、板上性能、15 Hz 闭环。

判断：CNN 路线目前完成的是“训练 + 离线对照/复查”，还不是可替换模型；下一步应先确定物理可接受误差和独立测试集，再决定网络、量化与硬件实现。

## 3. 路线 B：改造 Transformer 的硬件落点/流水线

当前唯一文档化方案是 `pynq_ffn/docs/05_pl_reselection_plan_20261018.md`（2026-10-18，待审阅）：

- 停止把独立 FFN 卸载作为主线。
- 保留原 OpenCV crop、resize、BGR2YUV 在 PS，避免在 PL 里精确复刻像素语义。
- PL 新前端：原归一化语义 → 按原 CHW/patch 顺序取数 → FP32 patch Conv（3→128，stride 6×10）→ 原顺序位置/相机嵌入。
- PS 后端：单一 ORT 图运行原 self-attention、cross-attention、FFN 和输出头。
- 不改权重、patch 定义、相机顺序和四输出语义；不量化、不重训、不做近似 GELU。
- 先做纯 CPU 分项测量和新切点等价；只有整链净收益 ≥10% 才进入 HLS；否则如实记录“PL 收益仍未成立”。

状态：仅方案与调研，未执行 CPU 分项、未写 HLS、未系统集成/上板，也没有新硬件操作授权。

说明：如果“改变 Transformer”指的是改注意力结构、量化或剪枝，目前没有已批准的实施方案；这些会改变模型合同，需要单独立项和验收。

## 4. 当前边界

- 正式 `pynq_ffn/hls/ffn_accel.cpp/.hpp` 仍是 r03，未被 r04 替换。
- 板上当前是 r04 v2 Overlay（idle，CTRL=4）；旧 r03 工件保留但未回退。
- 没有接相机/串口/电机，没有刷写/重启/改 CMA/内核，没有实车动作。
- 不能把 r04 的 170.03 ms、CNN 的离线误差或任何静态估计拼成“已达到 15 Hz/整车加速”。

## 5. 证据入口

- FFN 板测：`pynq_ffn/artifacts/r04_v2_board_20261018/`；`pynq_ffn/docs/04_验证记录.md` 第 22 节
- CNN v1/v2：`pynq_ffn/artifacts/model_train_v1_20261018/`、`pynq_ffn/artifacts/model_train_v2_20261019/`
- CNN 复查：`pynq_ffn/artifacts/model_review_v2_20261019/`
- PL 重新选点：`pynq_ffn/docs/05_pl_reselection_plan_20261018.md`
- GitHub：FFN r04 证据在 `history/r04-20261018` 分支；模型训练大文件（v1 约 3.5 GB、复查约 460 MB）未推送。
