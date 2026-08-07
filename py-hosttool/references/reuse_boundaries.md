# 复用边界

## 可直接复用

以下属于通用成熟资产，通常只需改名称或配置：

- `WindowControlButton` 的矢量绘制；
- `WindowTitleBar` 的 hover、close、maximize/restore 状态；
- `FramelessWindow` 的 frame、拖动、双击、native resize；
- 多窗口统一置顶 group；
- `PortComboBox` 弹出前刷新；
- COM 数字排序；
- `SerialWorker + queue.Queue + Qt signals`；
- 串口参数区、连接灯、打开/关闭、新串口；
- ASCII/HEX RX/TX、周期发送、日志保存；
- protocol-workbench 与 instrument-dashboard 布局骨架；
- GroupBox、Tab、状态栏、日志导出模式；
- pytest 的协议向量测试与 offscreen GUI smoke test结构；
- PyInstaller spec 的受控收集思路。

## 可按模式复用

需要根据项目改字段，但结构可保留：

- 指令生成面板；
- 上报帧分析面板；
- RegisterCard；
- ProtocolStrip；
- MetricTile 和轻量趋势图；
- request client；
- DiagnosticService；
- ControlService；
- WorkflowService；
- 性能计数器差分与趋势历史；
- 操作日志/诊断日志双导出。

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

## 复用检查清单

复制后搜索：

```text
ARQ|Alice|Bob|Wrapper|Scheduler|MCP|MasterController|ArqMinSystem
COM4|115200|D:\\|v1.1|v1.4|EB 90|EB 91
```

逐项判断是新项目真实需求还是残留。不能只改窗口标题。

## 原始工程资产说明

`assets/reference_projects/` 保存清理版来源源码，作用是：

- 查找成熟实现；
- 对比行为；
- 定点复制经过验证的片段；
- 理解测试覆盖和打包策略。

它们不是新项目模板。新工程应从 `assets/template/` 开始，避免把旧业务耦合整体带入。
