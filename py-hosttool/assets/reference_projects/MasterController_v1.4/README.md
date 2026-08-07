# MasterController v1.4（Python 版）

基于 PySide6、pySerial 和 pyqtgraph 的 Windows 上位机，保留 MATLAB 版的协议组帧、解帧、上报分析和串口调试工作流。

## 环境与源码启动

```powershell
python -m pip install -r requirements.txt
python main.py
```

未连接串口设备时程序仍可启动；在串口下拉框打开时会重新扫描端口。

## 测试与调试

```powershell
python -m pytest -q
```

协议测试覆盖指令帧、主控帧、连续分段帧提取和上报解析等核心逻辑。

## Windows 打包

```powershell
build.bat
```

正式产物为无控制台单文件版 `release\MasterController_v1.4.exe`。目标电脑无需安装 Python、Anaconda 或 MATLAB，但仍需要对应 USB 转串口设备的驱动。

## 工程结构

- `main.py`：程序入口。
- `gui.py`：主窗口与子串口窗口。
- `serial_worker.py`：非 GUI 线程中的 pySerial 收发。
- `protocol.py`：协议编解码、缓存提取与数据解析。
- `tests/`：pytest 自动化测试。
- `MasterController_v1_4.spec`：PyInstaller onefile 配置。
- `build.bat`：可重复执行的 Windows 打包脚本。

## PyInstaller 说明

原工程环境中的 PySide6 为 6.11.1。spec 使用 PyInstaller 原生 PySide6/shiboken6 hook 收集 Qt DLL 与平台插件，并排除会遮蔽 Windows ICU 运行时、导致 `QtCore.pyd` 导入失败的 Conda ICU DLL。请勿恢复旧的 `collect_all` 配置。

> skill 资产副本已移除来源电脑的 Conda 环境与工作区绝对路径。
