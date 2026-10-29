# Source provenance

本目录保存用户提供的两个来源工程的清理版源码：

- `ArqMinSystem_v1.1`；
- `MasterController_v1.4`。

清理动作仅排除：

- `__pycache__`、`.pytest_cache`；
- `build`、`release`、`dist`；
- `.pyc/.pyo`；
- EXE 和日志。

源码、测试、requirements、协议文档和 PyInstaller spec 保留供 skill 查阅与定点复用；README 与 build.bat 仅将来源电脑的绝对路径改为通用命令。它们仍包含具体业务协议和项目名称，不应整体复制为新工程。

## 第三个设计输入

`can_hosttool_v2.0.3`（用户提供于 2026-10-29）作为架构与验证模式的设计输入，而不是本目录中的可执行源码资产。只提炼以下通用经验：

- 被 GUI 与 worker 共享的 transport 生命周期、有界命令/接收队列以及 generation；
- 请求发送、超时、迟到响应隔离和不可幂等命令禁止自动重发；
- 后台 JSONL/CSV 记录、方向过滤回放、无设备模拟 transport；
- 驱动接受、链路回复、协议解析与业务执行分层报告；
- offscreen 布局、纯函数 golden vectors、onedir/frozen EXE 与真实硬件验收分层。

没有复制该工程的 CAN 业务协议、CAN ID、遥测/寄存器字段、厂家 SDK/DLL、设备标识、现场日志、用户设置、EXE、构建目录或绝对路径。完整说明见 `../../../references/can_hosttool_patterns.md`。
