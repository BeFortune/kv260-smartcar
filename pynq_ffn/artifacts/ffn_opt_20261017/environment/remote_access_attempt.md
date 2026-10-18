# 2026-10-17 “4090内网”远程访问尝试记录

状态：已更正并连通。2026-10-17 晚起通过正确的“4090内网”会话完成 HLS 验证；2026-10-18 正在新的 Vivado 系统工程导出工件。后续结果见 `../../ffn_opt_20261018/README.md`。

## 目标机确认（更正先前错误映射）

- **唯一选用目标是“4090内网”**：Netcatty 主机 id `cc06fd18-1b33-4ef1-b44f-2c0f063bedd8`，`192.168.110.248`，用户 `JW`，主机名 `JasonWenDsek`。实际工具位于 `D:/Learning/AMDDesignTools/2026.1/`。
- 先前将“4090内网”等同 4090zt 且把 id 认反，是助手错误：`02f0db8d-5fd8-4c5e-be33-4396656b2e9e` 实为 4090ts；4090zt 为另一个条目 `446f5afb-2d81-4409-be8f-6237d142559b`。它们都不是本任务应选的“4090内网”。不要沿用旧推断，也不要关闭用户手工会话。
- 每次先用 `get_environment` 定位**名称和地址同时匹配**的当前已连接会话，不能复用过期 sessionId 或靠截断主机列表推断。

## 连接方式（已验证）

- Netcatty 运行中，External MCP discovery.json 存在且新鲜；本会话经 stdio JSON-RPC 客户端 `~/.zcode/tmp/ffn_opt_20261017/netcatty_mcp_client.py` 调用（含 initialize/tools/list/get_environment 全部成功，不打印 token）。
- `host_open` 会触发 Netcatty 审批确认卡，约 110 秒无应答自动拒绝（前两次均因此被拒）。

## 协作约定（用户 2026-10-17 指定）

- 用户随后更正为：需要审批时，**先发通知，再发起远程请求并立即弹选择题**，避免思考耗时占掉审批卡有效期；没有批准或超时都不是已同意。
- 用户之后明确已切换 Netcatty 无审批模式，要求后续不再逐项弹审批问题。该选择只取消 Netcatty 卡片交互，不扩大到修改权限配置、系统维护或车辆动作；若实际返回拒绝，不能当作超时而绕过重试。
- 长构建用 `terminal_start` 后按返回 offset 增量 `terminal_poll`。`terminal_execute` 仅用于短命令；过去误传不受支持的 `waitMs` 和截断 6000 字符造成了误判，不能用它们确认阶段通过。当前客户端保留完整返回 JSON，原始证据须另存并核验哈希。

## 历史只读检查（阻塞原因存档，2026-10-17 早）

本机当前为 Windows/Git Bash。只读检查结果：

- `command -v netcatty` 无结果；`ncat` 也未找到（系统只发现普通 `ssh` 与 `/c/Program Files (x86)/Nmap/ncat`，不是 `netcatty`）。
- 当前 `~/.ssh/config`（Windows `C:\Users\BeFortuneEX\.ssh\config`）只列有 `homelab-dash`、`github.com`，没有 `4090内网`/`4090` 主机映射。
- `ssh -o BatchMode=yes -o ConnectTimeout=10 -- '4090内网' ...` 返回退出码 255，`Could not resolve hostname`。
- Windows socket DNS lookup 对 `4090内网` 和 `4090` 均失败 `getaddrinfo failed`。

没有猜 IP、扫描内网、试用其他主机别名、读取/打印 SSH 私钥、使用密码或触达其他网络服务。没有检查到远端 AMD 工具状态，也没有板卡下载/Overlay 操作。

候选 HLS 构建输入已准备在 `pynq_ffn/artifacts/ffn_opt_20261017/staged_hls/`。继续前需提供“4090内网”的可解析主机名/IP（或安装并说明 `netcatty` 的受支持连接流程），并确认远程登录用户/授权方式已经配置好；不得在对话或工程中提供私钥内容。到达 AMD 工具机后先验证 staged 文件哈希，使用新本地 build/work 路径运行候选 HLS；生成并核验与候选源码匹配的新 `.bit/.hwh` 后，再取得单独板卡编程授权与设备连接路径，执行候选单层、混合模型和性能验收。