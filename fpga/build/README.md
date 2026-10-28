# 当前 Overlay 工件

- `conv_front_int8_20261021.bit`
  SHA-256：`8ab0f411cc3ddee74045a753b379d265a121871dc656a53ef25b7c2edfe8da24`
- `conv_front_int8_20261021.hwh`
  SHA-256：`c2b5c8138087c8e398d509df40e1c854311ebcca249f7a0e232c107d8db6d067`

这两个文件与 `host/model/` 中的 PS ONNX、params 和整数参考属于同一验收基线。车端程序会拒绝哈希不匹配的 bit。

在本目录的 Bash 中可执行：

```bash
sha256sum conv_front_int8_20261021.bit conv_front_int8_20261021.hwh
```

预期与上面的值完全一致，否则停止，不加载 Overlay。

**XSA 未纳入本交付。** `metrics/evidence/system_artifact_verification.json` 只保留其 SHA-256 `60cc460d5b324b6f63a9c9aa983665b26709ec442f9f80e8ccb2278e43b7d92a` 和成员核对信息，不能声称已有可独立重建整系统的 XSA。当前也没有整系统 WNS 签核报告。

加载 bit、写寄存器和启动核都改变硬件状态；文件校验通过不等于获得设备操作授权。
