# 资产目录

## 1. 新项目模板

`assets/template/py_hosttool_template/`

包含：

- `main.py`：应用入口和统一字体；
- `hosttool/theme.py`：设计令牌和全局 QSS；
- `hosttool/window_chrome.py`：无边框窗口、矢量按钮、置顶组和 Windows resize；
- `hosttool/serial_worker.py`：通用串口线程；
- `hosttool/serial_console.py`：成熟串口面板与子窗口；
- `hosttool/main_window.py`：protocol-workbench 模板；
- `hosttool/dashboard_window.py`：instrument-dashboard 模板；
- `tests/`：不依赖真实设备的静态/纯函数测试骨架；
- `HostTool.spec`、`build.bat`：Windows 打包骨架。

模板是可运行的壳和回归示例，不是业务成品：没有项目协议、寄存器白名单、真实 request client、诊断采集、性能模型或可上板的自动流程。`dashboard_window.py` 当前尤其是布局占位；选择 dashboard 后仍需在项目层接入 transport、client、services、diagnostics 和 workflows。

## 资产覆盖矩阵

| 能力 | 模板是否覆盖 | 复用方式与边界 |
|---|---|---|
| 无边框窗口、置顶、布局和通用串口面板 | 有 | UI/外壳可直接复用，按目标布局做 smoke test |
| 串口 worker 与 byte channel | 有 | 只能按 `serial_logging.md` 生命周期、generation、背压和 shutdown 契约复用 |
| protocol parser、CRC、寄存器编码 | 无 | 依据用户 source of truth 和 golden vectors 新建/重写 |
| request client、services、diagnostics、models、workflows | 仅来源工程有参考 | 按契约/模式定点复用；禁止复制业务字段、重试和流程事实 |
| 协议单测、fake transport、offscreen GUI smoke | 仅有结构 | 测试向量、断连/重连断言和目标控件必须重建 |
| PyInstaller spec/build | 有骨架 | 重新核对依赖、架构、DLL 和 frozen smoke；先交付 onedir |

## 2. 创建脚本

`scripts/bootstrap_project.py`

复制模板并替换：

- 应用名称；
- 版本；
- EXE 名称；
- 经目标设备文档确认的串口初始波特率；
- 默认布局入口。

不会复制来源工程业务协议。

## 3. 来源工程

### ArqMinSystem_v1.1

重点查阅：

- `gui.py`：dashboard 布局、状态栏、置顶与导出；
- `widgets.py`：RegisterCard、ProtocolStrip；
- `performance.py` / `performance_view.py`：计数差分、指标卡和趋势；
- `client.py`：请求/响应；
- `services.py`：诊断与控制服务；
- `workflows.py`：可取消的多步骤自动流程；
- `tests/`：刷新恢复、计数器 reset、GUI smoke test。

### MasterController_v1.4

重点查阅：

- `gui.py`：workbench、串口侧栏、子窗口、彩色日志；
- `serial_worker.py`：通用串口线程；
- `protocol.py`：分段/粘包帧提取和分析；
- `MasterController_v1_4.spec`：PySide6/Conda DLL 冲突处理；
- `tests/test_protocol.py`：协议测试向量。

来源工程的协议向量、寄存器和业务流程不是新项目事实；只能用来理解测试形状，不能代替新项目 source of truth。

## 4. 选择顺序

1. 先读 `SKILL.md`。
2. 根据任务阅读相关 reference。
3. 从 template 起工程。
4. 只有模板缺少特定成熟行为时，再去 reference_projects 定点查找。
5. 复制后执行业务残留检查与测试。
