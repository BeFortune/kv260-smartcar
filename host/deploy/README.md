# 车端打包与部署

## 本机离线打包

在仓库根目录的 Bash 中执行：

```bash
python host/deploy/make_bundle.py
```

脚本从 `host/app`、`host/model` 和 `fpga/build` 取白名单文件，核对钉死哈希，生成：

- `host/deploy/dist/kv260car_<UTC时间>.tar.gz`
- 同名 `.sha256`
- tar 内 `MANIFEST.json`

输出目录不会进入 Git。相同文件名已存在时脚本失败，不覆盖旧包。打包成功只表示文件和哈希正确，不表示板端安装、Overlay 或实车已验证。

依赖清单为 `requirements-runtime.txt`、`requirements-prepare.txt` 和 `requirements-test.txt`。本交付整理未创建新环境或安装依赖；实际安装前先审查依赖和第三方许可证。

## 板端部署边界

原现场操作说明位于 `../app/tools/car/README.md`，服务单元位于 `../app/tools/car/systemd/`。板上目标布局仍是 `/opt/kv260car/<版本>/`，打包器会在 tar 内恢复程序需要的 `ps/` 与 `tools/car/` 相对路径。

上传后先核对 `.sha256`，再解包和审查 `MANIFEST.json`。安装脚本只安装文件和 unit 的设计意图不等于允许启动服务；以下操作均需单独确认：

- 加载 Overlay、写 PL 寄存器或启动核。
- 打开相机、串口、手柄或启动 `car-main`。
- 修改 systemd、重启服务或让车辆动作。

当前 PL 路线没有完成实时相机 + 串口 + 电机闭环验收。不要根据离线打包成功直接启动实车。
