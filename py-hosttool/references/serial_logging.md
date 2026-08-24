# 串口与日志

## 1. 串口线程所有权

`SerialWorker(QThread)` 独占 pySerial 对象。GUI 只调用：

- `open_port(settings)`；
- `close_port()`；
- `send(data)`；
- `shutdown()`。

这些方法只向线程安全队列写命令。模板将低频、幂等的 `open/close` 控制队列与有界发送队列分开，避免高频发送饿死关闭；GUI 应抑制重复连接操作，不能把控制队列当作业务任务队列。worker 在线程 `run()` 中处理命令和读取数据，并通过 signals 返回 `opened/closed/received/sent/error`。发送队列满使用 `command_rejected`/`False` 表达背压，不能冒充 I/O `error`，否则 GUI 会错误显示断连。

这只是实现形状，不是完整生命周期契约。实际项目还必须明确：worker 的启动方、`open/close/shutdown` 幂等性、有限 read timeout 或可唤醒关闭机制、停止确认和 wait 超时处理、断连时待发送队列/周期任务的处理、队列背压以及异常到 `closed` 的唯一收敛路径。裸 `serial-console` 只展示同一 worker 按顺序发出的原始通道事件，可以不把 generation 暴露到 UI；一旦接入 request/pending、自动重连或其他可能晚到的业务回调，必须由 transport adapter 或 client 在连接边界补齐并校验 generation。`SerialWorker` 应可被 fake/loopback transport 替换，不能把测试绑定到真实 COM 口。

优点：

- GUI 不阻塞；
- open/close/send 与 read 不会跨线程同时碰串口对象；
- 断连、重连和关闭顺序更清晰；
- 单元测试可用 fake worker 替代。

## 2. 端口列表

- 使用 `serial.tools.list_ports.comports()`。
- `PortComboBox.showPopup()` 前执行 refresh，减少“插入设备后下拉框仍旧”的问题。
- 排序时 `COM4 < COM12 < USB0`，不能用纯字符串排序。
- 刷新时尽量保留当前选择；默认端口只能是项目配置，不应硬编码为通用事实。

## 3. 参数处理

- 常用波特率列表可复用，但固定 AXI UARTLite 波特率的项目应隐藏或锁定波特率控件。
- parity UI 文本映射到 pySerial 的 `N/O/E`。
- 打开串口失败必须保留原异常文本，并恢复未连接状态。
- 连接灯是辅助信息；打开/关闭按钮和状态栏也要同步。

## 4. ASCII / HEX 与成熟日志视觉语义

发送：

- HEX 模式移除常规空白后校验偶数字符和十六进制合法性。
- ASCII 模式按明确编码编码，默认 UTF-8；需要 CR/LF 时发送真实字节，不发送字面量 `\\r\\n`。
- 空输入不发送。

接收与显示：

- `QTextEdit` 使用 `Consolas` 并关闭 Qt 自动换行。
- **HEX 日志默认依据日志 `viewport()` 的实际宽度、当前字体 `fontMetrics()` 和 document margin 动态计算每行可容纳的 Byte 数，不得硬编码固定 16 Byte 一行。**
- 窗口 `resizeEvent` 后重新渲染已有 HEX 日志，使宽/窄窗口都保持完整、整齐的 Byte 行。
- ASCII 模式使用容错解码，真实换行在日志中保持。
- 原始 bytes 应保留在内存日志条目中，便于重新排版和稳定导出。

MasterController_v1.4 已验证的颜色语义必须优先保留：

| 内容 | 颜色 |
|---|---|
| `[时间] RX/TX/INFO/...` 头部 | 灰 `#72777f` |
| RX 数据 | 绿 `#237a32` |
| TX 数据 | 蓝 `#1565c0` |
| ERROR 数据 | 深红 `#b71c1c` |
| INFO 数据 | 灰 `#5f6368` |

不要用通用主题的 `accent/success` 直接代替 RX/TX；这两个方向色是串口资产自身的稳定视觉语义。

## 5. 日志滚动保持

追加新日志前先记录：

```python
bar = log_view.verticalScrollBar()
at_bottom = bar.value() >= bar.maximum() - 2
```

- 用户原本在底部：新日志到来后继续跟随到底部。
- 用户已经滚到历史位置：追加日志后保持其阅读位置，**不得**无条件调用 `ensureCursorVisible()` 把用户强制拉回底部。
- 因 resize 重新渲染全部日志时，同样保存旧 scrollbar value 和 `was_at_bottom`，渲染后恢复。

这是高速串口调试工具的重要可用性资产，不应在新模板中简化掉。

## 6. 周期发送状态机

“周期发送” checkbox 表示**发送模式**，而不是立即启动 timer。成熟交互顺序为：

1. 勾选“周期发送”；
2. 点击“发送”；
3. 校验连接、数据格式和非空内容；
4. **立即发送第一帧**；
5. 启动 `QTimer`，之后按周期提交发送命令；
6. “发送”按钮变为“停止发送”；
7. 再次点击同一按钮停止 timer，按钮恢复“发送”。

因此不要在 `periodic.toggled(True)` 时直接启动 timer。

异常与关闭行为：

- 串口异常必须立即停止周期发送；
- 连接状态灯与打开/关闭按钮恢复为未连接语义；
- 窗口关闭必须停止 timer 后再 shutdown worker；
- 周期回调中的 HEX 格式错误、空发送区或断连必须停止当前周期会话，避免持续刷错误。

checkbox 可作为“模式选择”保留勾选状态；真正的活动态由 `timer.isActive()` 和“发送/停止发送”按钮共同表达。

## 7. 日志模型

推荐日志条目保存：

```python
(timestamp, direction_or_level, plaintext, hex_bytes_or_none)
```

显示层根据 `hex_bytes_or_none` 选择动态 HEX 或文本渲染；导出层保留：

- 时间戳；
- RX/TX/INFO/WARN/ERROR；
- 原始 HEX；
- ASCII/语义文本；
- 应用版本、端口和串口参数；设备/固件/协议版本在项目能够识别时一并记录。

日志控件设置最大块数或应用环形缓冲，避免长时间运行无限增长。

## 8. 状态颜色

默认优先级：

```text
[ERROR] > [WARN] > [OK] > [INFO] > 普通文本
```

即使一行包含多个标签，也按最严重状态着色。方向色与严重度色冲突时，错误严重度优先。

## 9. 协议流缓存

串口 worker 只负责 bytes 通道，帧提取放在协议层。协议实现必须以用户确认的文档、RTL/固件定义或抓包说明为 source of truth；来源工程只能提供实现参考。

```python
frames, remainder = parser.feed(previous_remainder + new_bytes)
```

约定由调用方持有 `remainder`，parser 不得同时隐式保留同一份缓存；连接建立或重连时显式 `reset()`。每个 parser 必须声明帧长度字段的含义、最大帧长、噪声/缓存上限和错误后的重新同步策略，并以每种帧的 golden vectors 锁定编码结果。CRC/checksum 不能只写“CRC”，必须记录覆盖范围、poly、init、refin/refout、xorout 和结果字节序。

解析器必须覆盖：

- 噪声前缀；
- 半帧；
- 多帧粘连；
- 直接帧与外层封装帧；
- 错误长度/校验后的重新同步；
- 缓存上限，避免异常数据导致无限增长。

解析结果交给 client 前还要区分响应、主动上报、重复/迟到帧和未识别帧；request 的 connection generation 与 sequence 不匹配时只能记录诊断信息，不能完成当前请求。
