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

## 2. 创建脚本

`scripts/bootstrap_project.py`

复制模板并替换：

- 应用名称；
- 版本；
- EXE 名称；
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

## 4. 选择顺序

1. 先读 `SKILL.md`。
2. 根据任务阅读相关 reference。
3. 从 template 起工程。
4. 只有模板缺少特定成熟行为时，再去 reference_projects 定点查找。
5. 复制后执行业务残留检查与测试。
