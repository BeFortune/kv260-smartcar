# FFN 的 PS/PL 接口与流水线设计

## 1. 第一版目标

第一版先做 **float32、exact-erf GELU、与现有 ONNX 数值一致** 的单 IP 参考实现。它是正确性和接口基线，不是已经优化好的实时加速器。四层顺序复用同一 IP；首版不要求四个独立核。

PS 执行五段 ONNX Runtime 子图；调用 `backend.forward(layer_name, x, weights)`。输出保持输入外形：camera 为 `[3,220,128]`，cross 为 `[1,4,128]`。PL 只见连续的 `[num_tokens,128]` 行主序数组。

| 参数 | 形状/类型 | 说明 |
|---|---|---|
| `x` | float32 `[T,128]` | LayerNorm 后的 FFN 输入，已由 PS 准备 |
| `y` | float32 `[T,128]` | FFN 本体输出，不含外部残差 |
| `w1` | float32 `[128,256]` | 右乘、C 行主序 |
| `b1` | float32 `[256]` | 第一层偏置 |
| `w2` | float32 `[256,128]` | 右乘、C 行主序 |
| `b2` | float32 `[128]` | 第二层偏置 |
| `num_tokens` | uint32 | `1..660`；生产调用通常 660 或 4 |

固定 layer_name：`camera_0`、`camera_1`、`cross_0`、`cross_1`。同名权重不可静默替换；更换模型应重建后端并核验整个 bundle。

## 2. 第一版采用 AXI master，不必再串一个 AXI DMA

硬件接口：

- `s_axi_control`：AXI4-Lite，PS 写地址/长度并启动。
- `m_axi_gmem0`：IP 直接访问 DDR 中的输入和输出。
- `m_axi_gmem1`：IP 直接读取 DDR 权重和偏置。
- `ap_clk/ap_rst_n`：同一受控 PL 时钟域和正确复位。
- 控制协议 `ap_ctrl_hs`；第一版轮询完成，不开自动重启。

这不是 AXI4-Stream 内核，因此没有理由再机械加一个 AXI DMA。未来若改成 stream+DMA，需要重新定义 TLAST、帧长度、双向启动顺序、超时恢复与寄存器合同，不能沿用当前驱动假装兼容。

KV260 是 Zynq UltraScale+ MPSoC，不是 PYNQ-Z2 的 Zynq-7000。典型连接为：

```text
ZynqMP PS M_AXI_HPM0_FPD
    -> SmartConnect -> ffn_accel_0/s_axi_control

ffn_accel_0/m_axi_gmem0
    -> SmartConnect -> PS S_AXI_HP0_FPD -> DDR
ffn_accel_0/m_axi_gmem1
    -> SmartConnect -> PS S_AXI_HP1_FPD -> DDR

PS pl_clk0 -> IP / SmartConnect / 对应PS AXI时钟
PS pl_resetn0 -> Processor System Reset -> 各互连/IP复位
```

端口名称和数量需以当前 Vivado PS 配置实际可用项为准。也可以共享一个 HP 端口先验证，带宽/争用另测；不能把 HPC/ACP 的名字当成已经实现缓存一致性。首版显式做 cache sync。

## 3. 寄存器 ABI

| 偏移 | 内容 | 位宽 |
|---:|---|---:|
| `0x00` | ap_ctrl_hs 控制/状态 | 32 |
| `0x10`, `0x14` | x 地址低/高 32 位 | 64 |
| `0x1c`, `0x20` | y 地址低/高 32 位 | 64 |
| `0x28`, `0x2c` | w1 地址低/高 32 位 | 64 |
| `0x34`, `0x38` | b1 地址低/高 32 位 | 64 |
| `0x40`, `0x44` | w2 地址低/高 32 位 | 64 |
| `0x4c`, `0x50` | b2 地址低/高 32 位 | 64 |
| `0x58` | num_tokens | 32 |

这张表是**本次源码规定的合同**，不代表任意 HLS 导出的 IP 都恰好如此。综合后检查 `*_hw.h`、component.xml、HWH 和 PYNQ `register_map`；任何不符都先修接口，不能为了“跑起来”跳过检查。

K26 有高地址 DDR；设备物理地址可能超过 32 位。Python 驱动写完整 64 位地址；Vivado SmartConnect、PS 地址映射、HLS m_axi 地址宽度也必须端到端支持。**仅 Python 写了高 32 位，不代表实际硬件地址通道支持了它。**

`ps/pynq_backend.py` 根据 HWH 核对寄存器地址/位宽后才允许调用；不猜 MMIO 基址。还要求 CTRL 为32位，并在 HWH fields 中明确验证 AP_START/AP_DONE/AP_IDLE/AP_READY/AUTO_RESTART 的位位置和位宽，拒绝 AP_CONTINUE/chain 协议及缺失字段。HWH 可把指针表示为一个 64 位寄存器或低/高两个 32 位寄存器。导出版本若采用不同命名，应先核对事实再增加解析支持，而非关闭校验。

## 4. 缓冲区与异常生命周期

1. 使用 `pynq.allocate` 分配，传 `device_address`，不用普通 NumPy 的虚拟地址。
2. 每套权重第一次用到时分配并同步，后续层调用复用；输入/输出缓冲区各按最大 660 tokens 分配。
3. CPU 写输入及权重后 `sync_to_device()`。
4. 确认 IP idle、auto_restart 关闭；写 pointer 和 `num_tokens`，再写 ap_start。
5. 轮询 ap_done，带明确超时；完成后 `sync_from_device()` 再读取结果。
6. 正常完成后才允许释放缓冲区。输出会复制为普通 NumPy 数组，避免下次调用覆盖上次结果。

**超时不等于硬件停止。** 当前后端会隔离并保留缓冲区引用，拒绝再次执行/释放。它不能代替物理复位，也不能保证进程退出后内存仍安全；发生超时要保持进程/Jupyter kernel 存活，按经过核验的恢复流程复位 PL/AXI master，之后重建进程与后端。不要简单 `finally: freebuffer()` 或重启 kernel 后继续操作。

不允许另一程序同时控制同一核；本后端是单线程/单所有者设计。异常、KeyboardInterrupt、可能已提交的 MMIO 写失败都需要保守处理。mock tests 只验证这套软件逻辑，不证明真实 AXI 安全性。

## 5. 如何逐步做真正的流水线

### 阶段 A：先通过参考核

- FP32 和 exact erf，尺寸固定 D=128/H=256，T 可变。
- 每次调用把权重读到片上数组，避免每 token 从 DDR 重读所有权重。
- 单 token 完整算出两层结果，再下一个 token。
- 检查偏置广播、矩阵方向、660 的最后 token、非法长度不写输出。

### 阶段 B：分块并行 MAC

从小并行度开始，例如输出通道并行 4/8，按实际 DSP/BRAM/时序调节：

```text
Load token tile -> FC1 tiles -> GELU -> FC2 tiles -> Store token tile
```

- `ARRAY_PARTITION`/`ARRAY_RESHAPE` 必须与每拍读口数匹配；不能只加 `UNROLL` 不给足存储带宽。
- FP32 累加存在反馈依赖；`PIPELINE II=1` 是请求，不是结果。查看 schedule/loop latency、实际 II、DSP 使用和时钟余量。
- 660 不能被常见 tile=16/32/64 整除，尾块必须 mask；测试 1、4、659、660 和非法 0/661。
- 不在“第一版正确性”同时做 FP16/int8、GELU 近似、转置重排。

### 阶段 C：DATAFLOW 与双缓冲

在相互独立、接口干净的 load/compute/store 函数之间使用 `DATAFLOW`；通过 stream/FIFO 或 ping-pong tile 缓冲解耦。检查：

- token tile 的生产/消费计数一致，无阻塞死锁；FIFO 深度有依据。
- FC1 输出为 256 维，FC2 需要相应完整或可正确累计的中间块；不能把依赖关系当独立流水级。
- 权重在启动时装载还是每层切换装载要明确；四组共享一个计算核不意味着片上缓存能同时放下四组。
- 主机端双缓冲只能在独立 buffer 和明确完成事件下进行；当前首版驱动不支持并发调用。

### 阶段 D：系统收益验证

- 同一套输入、同一模型、同一线程和 warmup，比 `golden` CPU、`split-ort`、`split-PL`。
- 分别记录 PS 非 FFN、拷贝/flush、PL 等待、invalidate、后处理；另记录完整离线/实时链路 P50/P95/P99。
- camera FFN 占大多数算量；小 cross FFN 若搬运更慢，可以在后续配置中保留 PS，但需要明确调度策略与独立测试。本次代码统一四处接口方便验证。
- 报告真实 synthesis/implementation 利用率、WNS/TNS、Fmax、功耗及整帧延迟。只有 C++ 测试和 HLS C simulation 不能宣称这些结果。

## 6. 验收分级（不可跳级）

| 等级 | 必须留下的证据 |
|---|---|
| 软件合同 | 模型哈希、三摄预处理、四输出合同 |
| PS 分图 | 原图与分图/NumPy 在随机及真实输入上的误差、整数输出差异 |
| C++ 数值 | 主机 testbench；不叫 HLS 综合结果 |
| HLS | C simulation、C synthesis、C/RTL cosimulation，包含尾块 |
| Vivado | Validate Design、地址映射、implementation 时序/资源、匹配 bit/hwh |
| 板上基础 | CMA/工具路径、已知安全 overlay、真实 PS→PL→PS 计算 |
| 单层 FFN | 四套真实权重逐层误差、非零/边界输入、重复调用 |
| 混合模型 | 同帧同模型四输出一致、完整耗时含传输 |
| 车辆系统 | 相机同步/新鲜度、故障停车、接管、串口/方向/单位、物理急停 |

本轮完成范围和具体数值见 `04_验证记录.md`。HLS/Vivado/板上验收没有发生时，必须保持为未验证，不用“工具可导入”或“代码能编译”替代。
