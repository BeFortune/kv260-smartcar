# 推理输入存图与不发串口诊断

## 本轮范围

2026-10-24：只在 Windows 本机开发与离线验证，**未部署到 KV260，未打开相机、串口、手柄或 PL**。现有 CPU 原模型、PL 后端与模型哈希约束保留；没有重训、没有改变裁剪、相机顺序或速度编码。

新增能力是给后续“现场图片同图对照”保存证据，不是已经修好转弯半径问题。存下的预测不等于 STM32 收到，更不等于轮子执行。

## 参数

| 参数 | 默认值 | 含义 |
|---|---:|---|
| `--save-frames` | 关闭 | 开启抽样存图；不开时不创建存图线程或目录 |
| `--save-every N` | 15 | 保存第 1、1+N、1+2N… 次**成功推理**实际用到的三摄图片，不是按相机帧号或秒数抽样 |
| `--save-max N` | 300 | 最多接受 N 组进入写盘队列；队列满丢弃的采样不占这个额度 |
| `--save-max-mib N` | 256 | 本次存图目录预算，包含图、张量、元数据和索引；达到数量或容量任一上限即停止存图 |
| `--dry-run` | 关闭 | 采集、推理、记录，但不创建/打开串口，不检查串口配置，不读取手柄；所有控制报文（含停车帧）只在内存丢弃 |
| `--seconds S` | 不限 | 仅用于 `--dry-run`：从开始检查相机到结束的时长，单位秒；不含模型初始化或 PL 标记等待，退出时排空存图队列 |

`--dry-run` **不等于“完全不使用硬件”**：真实运行仍会打开相机；若选择 `--backend pl` 还会调用已加载的 PL。纯本机请使用下文 `frame_capture_replay.py`。

`--save-frames` **本身不会禁用车辆控制**。以后做不动车的采图，应明确同时使用 `--backend cpu --dry-run`，不能只加存图开关。

`--dry-run` 的日志中 `serial_enabled=false`、`frames_sent=0`、`stop_frames=0`。沿用控制主循环做编码检查的数量记为 `diagnostic_packets`、`diagnostic_stop_packets`；退出还写 `diagnostic_output`，其中 `serial_bytes_written=0`。

## 存在哪里、能对应什么

每次运行的 `run_…/` 下：

```text
run_…/
  events.jsonl
  frames.csv
  snapshots/
    manifest.json
    index.jsonl
    frame_000001/
      left.png
      front.png
      right.png
      crop_left.png
      crop_front.png
      crop_right.png
      inputs.npz
      metadata.json
```

- 三张原图是 `TimedInfer` 当次推理实用的 **640×480 BGR 解码图，保存为无损 PNG**。不是第二次从相机抓取，不是重新压缩成 JPEG；坏帧回退时保存的也正是当次沿用的那张图。
- 三张 `crop_*.png` 是相同裁剪和缩放后的 **200×66 BGR 可视化**，不是模型的 YUV/量化输入，不可把它直接送入模型。
- `inputs.npz` 的 `preprocessed_fp32_recomputed` 用生产 `preprocess_arrays` 从保存原图复算，明确不是截获 CPU session 的实际输入。PL 路径额外保存本轮核缓冲的立即拷贝：`pl_input_int8_actual`、`pl_output_int8_actual`。本机 `reference` 回放下这两项来自整数参考缓冲，manifest 会标明不是真 PL。
- `metadata.json` 包含推理序号、预测左右速度、主机取帧时间和帧龄、三路时间差、文件及像素 SHA256；`manifest.json` 记录后端、模型哈希和相机配置。
- 用 `frame_id` 将图片组和同一次运行 `frames.csv` 对上。该序号是**成功推理序号**，不是驱动帧序号，也不保证三路每次都有新图。
- `grabbed_*_mono` 是主机 `cap.read` 返回后记录的单调时钟，不是曝光时间；`host_frame_skew_ms` 是这三个主机时间的差，不能冒充硬件同步精度或完整光学延迟。
- `snapshot_queued=true` 仅表示接受入队，**不代表已经保存成功**。以 `snapshots/index.jsonl` 的完成记录和 `frame_capture_closed` 的 `saved/errors` 为准。

## 避免存图反过来拖慢主循环

一个后台线程编码和写盘，最多两组等待；主循环只为选中样本复制图像/核缓冲。队列满丢弃这次存图，不等待磁盘，不重发旧控制结果。默认每 15 次推理采一组；CPU 若只有约 10 Hz，相当于约每 1.5 秒一组，不能称每秒一组。

磁盘满/编码失败：停止后续采样，记录 `errors/first_error/disabled_reason`，保留失败的 `.partial`，不静默宣称成功。相机/推理正常时不会仅因存图失败改变控制量。每次心跳及退出统计包括 `accepted/saved/queue_dropped/errors/bytes_written/pending`。

这些限制减少干扰，但**不保证零性能开销**；真实板上对节拍的影响尚未测。含 `snapshots/` 的运行目录不再被“保留最近30次”的日志轮换自动删除，需自行归档，多个运行的总占用仍会累积。

## 现在可执行：纯本机录像回放

执行机器：本机 Windows，**Git Bash**。前提：本机工程、既有测试 venv、5.16 录像 ZIP 均保留。

以下命令使用 Windows 的 mktemp 唯一临时目录作为输出父目录；Python 只读 ZIP 中指定的 117 帧，不解压整库。命令任何一步失败就停。成功会打印 `status=已验证`，并在输出目录生成报告、图片与索引。

```bash
cd /c/Users/BeFortuneEX/Source/Repos/FPGA/pynq_ffn && \
RUN_PARENT=$(mktemp -d /c/Users/BeFortuneEX/.zcode/tmp/capture_replay_XXXXXX) && \
/c/Users/BeFortuneEX/.zcode/tmp/ffn_opt_20261017/venv/Scripts/python.exe \
  tools/car/frame_capture_replay.py \
  --archive ../Database/record_data_5.16.zip \
  --segment 1778852165 \
  --backend cpu \
  --save-every 15 --save-max 8 \
  --out "$RUN_PARENT/cpu"
```

这里的 `cpu` 明确是**旧 NUC 原模型**。把回放脚本参数改为 `--backend reference` 则运行新模型路线 B 的整数参考+ORT后半段；不会导入 PYNQ。两条路径都能检查：保存图片像素完全一致、哈希一致、CSV/元数据一致、张量一致、重新预测最大误差≤0.001。

录像回放的时间戳是回放程序的主机时间，**不是历史录像曝光时间，也不是吞吐量或实车性能验收**。

## 以后接回相机：板上用法说明（本轮未部署、未在板端执行）

部署前先核对板上当前版本，不能覆盖板端新的 CPU 后端改动。本机当前已包含该后端，但本轮没有重新连接板子确认两边是否继续变化。下列命令需要新参数版本已安装、原模型路径存在、相机口位配置一致、`car-main` 已停止，且车辆电机输出已独立隔离。

必须先在 KV260 Linux 终端确认 `systemctl is-active car-main` 为 `inactive`；若还在运行，先由现场负责人通过独立急停/断开电机供电确保车辆安全，再停止服务。**不能靠拔控制串口让旧下位机停车**。

新版本到位后，可手动运行这一命令，最长采图检查循环60秒，不依赖手柄、不打开控制串口、不访问PL：

```bash
/usr/local/share/pynq-venv/bin/python3 /opt/kv260car/current/tools/car/car_main.py \
  --backend cpu \
  --model /home/ubuntu/kv260_v1_pynq_20261011/model/model_current.onnx \
  --dry-run --save-frames \
  --save-every 5 --save-max 120 --save-max-mib 256 \
  --seconds 60 --runs-dir /home/ubuntu/car_capture_runs
```

缺相机时只等设备，在这60秒内不会有图片；结束看 `frame_capture_closed`，`saved=0` 不是采图成功。相机接上以后这轮采图本身不要求 STM32、CH340 或手柄在线。现有自启控制服务和其它进程不受 `--dry-run` 影响，所以必须先确认它们没有占用设备或发送控制。

**请勿为了试存图执行 `carctl pl`、重启板子或改 systemd unit；本功能不需要这些动作。**
