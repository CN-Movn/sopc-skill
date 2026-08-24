# 架构与开发流程

## 1. 推荐目录

```text
project/
├─ main.py
├─ hosttool/
│  ├─ config.py             # 名称、版本、默认参数
│  ├─ theme.py              # 颜色与 QSS
│  ├─ window_chrome.py      # 无边框外壳
│  ├─ transport.py          # 可选的 byte-channel 契约；串口是其中一种实现
│  ├─ serial_worker.py      # 串口线程
│  ├─ serial_console.py     # 通用串口 UI
│  ├─ protocol.py           # 纯函数/流解析
│  ├─ client.py             # 请求、超时、序列号、重试
│  ├─ services.py           # 业务操作
│  ├─ workflows.py          # 多步骤流程
│  ├─ diagnostics.py        # 状态规则
│  ├─ models.py             # 性能/寄存器/显示模型
│  ├─ widgets.py            # 可复用控件
│  └─ main_window.py        # 只负责装配与交互
├─ tests/
├─ requirements.txt
├─ build.bat
└─ HostTool.spec
```

小工具可以合并部分文件，但协议纯逻辑、串口线程和 GUI 至少要分开。

默认资产只覆盖串口 byte channel。TCP、UDP 或文件回放只有在项目明确需要并实现同一 transport 契约时才纳入；不能把串口的连接灯、端口参数或阻塞模型直接套到其他传输。

## 2. 分层职责

### GUI

- 创建控件、布局、连接 signals；
- 读取用户输入并做格式级校验；
- 展示状态、日志和错误；
- 不直接拼复杂协议、不直接读写串口、不执行长流程。

### protocol

- 帧编码/解码；
- checksum/CRC；
- 字节序；
- 流式提取；
- 纯函数优先，便于 pytest。

协议文档、RTL/固件定义或用户确认的抓包说明必须指定为 source of truth。实现前记录帧长度字段语义、最大帧长、字节序和完整 checksum/CRC 参数（覆盖范围、poly、init、refin/refout、xorout、结果字节序），并为每类帧保存 golden vectors。来源工程只能提供代码形状，不能提供新项目字段事实。

### client

- sequence 分配；
- pending request 表；
- 超时与有限重试；
- 响应匹配；
- 主动事件分流；
- 断连时清空 pending。

request 身份至少由 `(connection_generation, sequence)` 组成；sequence 回绕时不能覆盖仍 pending 的请求。超时、取消、断连和响应只能完成同一个 request 一次，迟到响应必须丢弃或作为诊断事件记录。自动重试只允许明确幂等的读取/查询，或协议提供去重键；写寄存器、脉冲和其他不可幂等命令必须禁止自动重放。并发数量、设备忙重试窗口和 response/主动上报的分流规则应写入 client 契约。

### services

- 将“设置链路”“读取寄存器”“清错误”转换为 client 请求；
- 发出 issued/completed 信号；
- 不决定复杂流程顺序。

### workflows

- 用显式 step 列表表示请求、延时、等待空闲和回读确认；
- 每一步写日志；
- generation token 防止取消后旧回调继续执行；
- 任何失败立即停止；
- BUSY 只在限定时间内重试。

### diagnostics / models

- 将原始寄存器转为 normal/notice/error/stale；
- 差分计数考虑回绕和 reset；
- 配置/清统计后 invalidates baseline；
- 模型与 view 分离，便于无 GUI 测试。

services、client、protocol 和 models 不应依赖具体窗口类；GUI 通过公开接口、signals 和注入的 fake transport/client 使用它们。至少明确允许的依赖方向，避免 service 绕过 client 直接写串口，或 model 读取控件状态。

## 2.1 Transport 与线程生命周期契约

byte-channel 至少定义 `open(settings) -> opened/error`、`read -> bytes`、`write(bytes)`、`close` 和 `shutdown` 的语义；调用方不得直接持有串口对象。串口实现可以使用 `SerialWorker + queue.Queue + Qt signals`，但必须满足：

- worker 只在自身线程创建、读写和关闭底层句柄；GUI 只提交命令并接收 signals；
- `start/open/close/shutdown` 幂等，命令顺序可解释；断连时拒绝或清理待发送项，周期发送和刷新任务先停止；
- 阻塞读取有有限超时或可唤醒机制，shutdown 发送关闭命令并在有界时间内确认线程停止；超时必须报告，不得依赖进程退出清理；
- opened/received/sent/error/closed 等异步结果带 connection generation，或由唯一 adapter 在边界处补齐；旧连接和已取消流程的回调不得触发新状态；
- 命令队列有明确的背压/丢弃策略，控制命令不会被无限周期发送饿死；写入、读取和关闭异常收敛到单一断连路径；
- transport 可由 fake/loopback 实现替换，以便测试半帧、断连、超时、迟到响应和关闭。

这些是生命周期不变量，不要求所有项目使用相同的 QThread 组织方式；若采用 QObject moved-to-QThread 或其他实现，也必须满足相同的所有权、唤醒、generation 和停止语义。

## 3. 自动流程原则

成熟的一键流程不是连续调用若干按钮，而是状态机：

1. 记录流程开始；
2. 停止可能改变状态的数据源；
3. 等待硬件安全窗口或空闲；
4. 发配置/控制命令；
5. 对关键配置执行 GET/readback；
6. 恢复数据源；
7. 重新建立性能差分基线；
8. 明确完成或失败。

每个步骤需要 label、目标、超时、重试策略与验收条件。

## 4. 刷新与采集

- 周期采集只读有诊断价值的关键寄存器，不轮询 WO、脉冲和重复镜像。
- 多节点采集可以相位错开，降低串口突发压力。
- 一次跨多个 IP 的顺序读取不是原子快照；日志和 UI 必须诚实说明。
- 严格守恒判断应依赖硬件快照或停止数据源后采集。

## 5. 错误恢复

重点测试：

- 打开不存在端口；
- 设备拔出；
- 串口异常关闭；
- 请求超时；
- 旧连接回调晚到；
- 刷新中断后重连；
- 自动流程取消；
- 清统计后首个性能样本；
- 子窗口关闭和主窗口退出。

用 connection generation 或 workflow generation 丢弃旧回调，不能只依赖布尔 connected。

## 6. 实施顺序

1. 外壳与布局 smoke test；
2. 串口 worker 与连接状态；
3. protocol 纯函数与测试向量；
4. client 请求/响应；
5. 单项业务 service；
6. 诊断/模型；
7. 自动 workflow；
8. 导出与打包；
9. 上板联调。
