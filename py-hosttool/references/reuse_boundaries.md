# 复用边界

## 可直接复用：UI 与外壳

以下属于通用的 UI/窗口资产，通常只需改名称、文案或配置；仍需按目标布局做 smoke test：

- `WindowControlButton` 的矢量绘制；
- `WindowTitleBar` 的 hover、close、maximize/restore 状态；
- `FramelessWindow` 的 frame、拖动、双击、native resize；
- 多窗口统一置顶 group；
- `PortComboBox` 弹出前刷新；
- COM 数字排序；
- 串口参数区、连接灯、打开/关闭、新串口；
- ASCII/HEX RX/TX、周期发送、日志保存；
- protocol-workbench 与 instrument-dashboard 布局骨架；
- GroupBox、Tab、状态栏、日志导出模式；
- pytest 的 offscreen GUI smoke test 结构（不是协议/设备测试）。

## 按契约或模式复用：通信、业务与交付

以下可以参考成熟实现，但必须先对齐新项目的公开契约、线程生命周期和测试替身；不能只复制文件：

- `SerialWorker + queue.Queue + Qt signals`：按 transport 生命周期、generation、背压和 shutdown 契约适配；
- 流 parser、`request client`：按 source-of-truth、golden vectors、帧长度/CRC、sequence 和幂等重试规则重写或定点移植；
- 指令生成面板；
- 上报帧分析面板；
- RegisterCard；
- ProtocolStrip；
- MetricTile 和轻量趋势图；
- DiagnosticService；
- ControlService；
- WorkflowService；
- 性能计数器差分与趋势历史；
- 操作日志/诊断日志双导出；
- PyInstaller spec 的受控收集思路：按当前依赖和目标环境重新验证，不能照搬 hidden imports 或 DLL 过滤；
- 协议向量测试、fake transport、offscreen smoke test：测试结构可复用，测试事实和断言必须重建。

## 必须替换

这些是来源项目业务事实，不得默认进入新工程：

- ARQ、Alice/Bob、TX/RX Wrapper、Scheduler、MCP 等名称；
- `EB 90/EB 91`、134 字节上报、0x90–0x94 指令；
- ARQ 寄存器表、访问属性和写掩码；
- 固定 COM4；
- 固定 115200（除非新硬件同样约束）；
- `D:\\ProgramData...`、`D:\\Workspace...` 等路径；
- 项目版本 `v1.1/v1.4`；
- 特定设备 ID、MAC、超时、重试、随机种子；
- 业务专有诊断文本和自动流程顺序。

模板中的 dashboard 当前只提供连接/流程/诊断的可运行布局占位，不包含真实 transport、协议 client、寄存器模型、诊断采集或自动流程实现；若项目选择 dashboard，必须在应用层补齐这些边界，不能把占位控件当成已交付能力。

## 复用检查清单

复制后搜索：

```text
ARQ|Alice|Bob|Wrapper|Scheduler|MCP|MasterController|ArqMinSystem
COM4|115200|D:\\|v1.1|v1.4|EB 90|EB 91
```

逐项判断是新项目真实需求还是残留。不能只改窗口标题；对 worker、parser、service 和 spec 还要核对接口契约、线程所有权、测试向量和目标环境。

## 原始工程资产说明

`assets/reference_projects/` 保存清理版来源源码，作用是：

- 查找成熟实现；
- 对比行为；
- 定点复制经过验证的片段；
- 理解测试覆盖和打包策略。

它们不是新项目模板。新工程应从 `assets/template/` 开始，避免把旧业务耦合整体带入。
