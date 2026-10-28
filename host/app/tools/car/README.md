# KV260 小车上位机：安装、开机自启、现场操作

板上路径：程序装在 `/opt/kv260car/<版本>/`，`/opt/kv260car/current` 指向当前版本；每次运行的日志在 `/home/ubuntu/car_runs/run_<UTC时间>_<boot_id前8位>/`，普通运行保留最近 30 次，含 `snapshots/` 的运行不自动删除。

## 可选存图与不发串口诊断（本机已实现，板端未部署）

新增默认关闭的 `--save-frames`，配合 `--save-every`、`--save-max`、`--save-max-mib` 抽样保存实际推理三摄图。`--dry-run` 跳过串口和手柄，`--seconds` 可让诊断循环定时结束。**存图开关本身不会禁用车辆控制**，不动车采图要同时使用 `--backend cpu --dry-run`，并先停掉独立的自启控制服务。

图像无损保存为 PNG，每组与 `frames.csv` 的 `frame_id` 对应。完整参数、已有录像回放方法、输出文件和安全边界见 [FRAME_CAPTURE.md](FRAME_CAPTURE.md)。

## 开机后自动发生什么

1. `car-xpad-bind`：加载 xpad，把接收器 `413d:2104` 交给它，生成 `/dev/input/jsN`。
2. `car-pl-load`：核对 bit/hwh 哈希、PL0 约 100 MHz，下载钉死的 bit（方式与 2026-10-21 验收相同，不改时钟），核空闲后写 `/run/kv260car/pl_loaded.json`。
3. `car-main`：
   - 等上面的标记对上本次开机，不对就一直等，什么都不碰。
   - PL 自检：合成图，PL 对整数参考逐字节比较。不对就退出码 3，不重启。
   - 等设备：手柄、相机口位、串口、三路出帧。设备不齐就什么都不发，每秒查一次。
   - 等手柄 **Back** 启动，然后开始跑。

## 手柄

| 键 | 作用 |
|---|---|
| Back | 等待时按：开始跑。运行中按：补停车帧，回到等待 |
| 右摇杆按下 | 任何时候：立刻停车，回到等待 |
| 手柄关机/接收器断开 | 补停车帧，等手柄回来，回来后要再按 Back 才会跑 |

手柄超时自动关机时接收器会切到 2106 模式，js 设备消失，车会停。跑之前按一下键唤醒手柄。

## 出错时

| 情况 | 程序怎么做 |
|---|---|
| 一路相机断流、一帧过期（>0.2 s）、串口写失败、推理出错 | 补停车帧，回到等设备 |
| 单帧 JPEG 坏 | 沿用上一张，过期了才停车 |
| 模型/配置哈希不对、PL 自检不对 | 退出码 3，systemd 不重启，看日志 |
| PL 核超时卡死 | 补停车帧后进程停住不退出，要重启板子 |
| `systemctl stop` / 关机 | 这一轮结束后补停车帧再退出 |

## 现场命令（`carctl`）

```bash
carctl status          # 三个服务状态 + 标记 + 最新日志末尾 5 行
carctl rate            # 实时监看输出速度：每秒一行（只读日志，不碰相机/串口）
carctl rate --once     # 只打一次汇总，适合留存证据
carctl rate --interval 2 --window 10   # 两秒刷新、按 10 秒窗口算频率
carctl events 30       # 最新一次运行的事件日志
carctl frames 5        # 最新一次运行的逐帧记录
carctl log 80          # journalctl 本次开机
carctl restart         # 重启主程序（会先补停车帧）
carctl stop            # 停主程序
carctl enable          # 开机自启
carctl disable         # 取消开机自启
```

## 实时看输出速度

`carctl rate` 只读 `<run>/frames.csv` 和 `<run>/events.jsonl`，不给车发任何字节，跑不跑它都不影响控制。

```
   已跑s  状态             窗口Hz      总Hz         推理ms 均值/P95/最大  PL核ms    帧龄max     串口累计    未发出      左速度 /     右速度
    43.4  RUNNING       13.80    13.80    20.90/ 20.90/ 20.90   4.40     40.0      590     10   188.20 /  209.70
```

| 列 | 含义 |
|---|---|
| 已跑s | 本次运行第一帧到最新一帧的时长（用 `t_mono`，不受板子时钟影响） |
| 窗口Hz | 最近 `--window` 秒内的真实帧率，看当前速度用这一列 |
| 总Hz | 本次运行从头到最新一帧的平均帧率 |
| 推理ms | 均值/P95/最大，等于预处理 + PL 核 + 反量化 + PS 后半段 |
| PL核ms | 只算 PL 核那一段（`kernel.run()`），不含搬运和 PS 计算 |
| 帧龄max | 三路相机当前帧离取帧时刻的最大年龄，超过 200 ms 会触发停车 |
| 串口累计 | 最近一次 heartbeat 里累计写成功的帧数（默认 60 秒一次） |
| 未发出 | 推理了但没写进串口的帧数（编码被拒或刚重启还没到 heartbeat） |

注意：`frames.csv` 每 15 行才 flush 一次，文件落盘有约 1 秒抖动；频率是用每帧的 `t_mono` 算的，所以数值本身准确，只是看到得稍晚一点。`--window 1` 在数据成批出现时会跳，建议 5 秒以上。

## 切换推理后端（PL / 纯 CPU 原模型）

主程序有两个后端，靠 `/etc/kv260car/backend` 这个文件选（内容 `pl` 或 `cpu`，没有文件就是 `pl`）：

| 后端 | 跑什么 | 依赖 |
|---|---|---|
| `pl` | conv0-2 在 PL，后半段在 ORT CPU | bit、HWH、params、partb.onnx，等 `pl_loaded.json` |
| `cpu` | 原模型整图在 ORT CPU（NUC 原方案） | 只有 `model_current.onnx`；不碰 PL、不等标记 |

切换一定要重启主程序，因为相机和串口是独占的。切换时车会收到一次停车帧后停下，这是设计行为。

```bash
# 0 先停：会补停车帧再退出
sudo carctl stop

# 1 预检（此时车已停，不碰相机/串口/PL）。看“合计 ms P50”够不够 66.7 ms
/usr/local/share/pynq-venv/bin/python3 /opt/kv260car/current/tools/car/cpu_check.py --threads 4

# 2 选后端并启动
sudo mkdir -p /etc/kv260car
echo cpu | sudo tee /etc/kv260car/backend
sudo systemctl start car-main

# 3 确认走的是 CPU：事件日志里应有 "cpu_backend"，config_ok 里 backend=cpu
carctl events 20
carctl rate
```

回退到 PL：

```bash
sudo carctl stop
sudo rm /etc/kv260car/backend        # 或 echo pl | sudo tee /etc/kv260car/backend
sudo systemctl start car-main
```

手动跑一次（不装服务、不碰 systemd）：

```bash
sudo /usr/local/share/pynq-venv/bin/python3 /opt/kv260car/current/tools/car/car_main.py \
  --backend cpu --runs-dir /home/ubuntu/car_runs
```

纯 CPU 模式下 `frames.csv` 的 `kernel_ms`、`dequant_ms` 两列是空的，`partb_ms` 这一列表示整模型推理耗时；列本身不变，`carctl rate` 照常能看。

## 安装新版本（本机打包，板上安装）

```bash
# 本机 pynq_ffn 目录下
python tools/car/make_bundle.py            # 生成 tools/car/dist/kv260car_<时间>.tar.gz 和 .sha256
# 上传到板上 /home/ubuntu/ 后，在板上：
cd /home/ubuntu && sha256sum -c kv260car_<时间>.tar.gz.sha256
tar xzf kv260car_<时间>.tar.gz
sudo bash kv260car_<时间>/tools/car/install.sh   # 只装文件和 unit，不 enable、不启动、不下载 bit
```

切回旧版本：`sudo ln -sfn /opt/kv260car/<旧版本> /opt/kv260car/current && carctl restart`。

## 撤销全部

```bash
sudo carctl disable
sudo rm /etc/systemd/system/car-main.service /etc/systemd/system/car-pl-load.service /etc/systemd/system/car-xpad-bind.service /usr/local/bin/carctl
sudo systemctl daemon-reload
sudo rm -r /opt/kv260car
```
