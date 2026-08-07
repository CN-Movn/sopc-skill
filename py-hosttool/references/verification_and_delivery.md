# 验证与交付

## 1. 静态门槛

至少执行：

```bash
python -m compileall -q .
python -m pytest -q
```

必要时增加 ruff/mypy，但不要为了工具形式牺牲项目环境兼容性。Skill 自身应先运行 `python scripts/validate_skill.py`；若环境安装了 Agent Skills 的参考验证器，再追加 `skills-ref validate .`。

## 2. 协议测试

每一种帧至少覆盖：

- 已知正确测试向量；
- 长度字段与字节序；
- checksum/CRC；
- 边界值；
- 非法字段；
- 半帧；
- 粘包；
- 噪声前缀；
- 错误帧后的重新同步。

协议测试应尽量不导入 GUI。

## 3. GUI smoke test

在 CI 或无显示环境：

```bash
set QT_QPA_PLATFORM=offscreen
python -m pytest -q tests/test_gui_smoke.py
```

smoke test 应从公开入口 `from hosttool.main_window import MainWindow` 构造窗口，不能硬编码 `WorkbenchWindow`，否则 dashboard 项目会误测另一套布局。

检查：

- `MainWindow` 对当前生成布局可构造；
- 核心控件存在；
- splitter/固定宽度符合预期；
- worker 可以 shutdown；
- closeEvent 不挂死；
- 未连接设备也可启动。

Linux offscreen smoke test不能替代 Windows 原生边缘缩放和置顶测试。

## 4. 运行检查

Windows 上人工检查：

- 标题栏拖动与双击；
- 最大化/还原图标；
- 四边四角 resize；
- DPI 100%/125%/150%；
- 置顶切换后窗口状态不丢失；
- 主/子串口窗口置顶同步；
- 插拔串口后列表刷新；
- 断连时 timer、请求和刷新停止；
- 日志长时间运行不无限增长；
- RX 绿 / TX 蓝 / 灰色头部语义未退化；
- HEX 行随 viewport 宽度动态重排；
- 用户查看历史日志时，新数据不会强制拉回底部；
- 周期发送为“立即首发 → timer → 停止按钮”，异常会终止活动周期。

## 5. PyInstaller

- 优先使用项目固定 spec；
- 使用 PyInstaller 原生 PySide6/shiboken6 hooks；
- 不用无差别 `collect_all()`；
- hidden import 只添加实际需要项，如 `serial.tools.list_ports`；
- Conda 环境出现 DLL 冲突时，在 spec 中按确切文件名过滤，并记录原因；
- 先验证 onedir，再决定 onefile；
- `console=False` 只用于正式 GUI 版本，调试阶段保留 console 更利于定位。

## 6. 交付内容

源码交付建议包含：

- 源码；
- requirements；
- tests；
- build.bat/spec；
- README；
- 协议或寄存器文档；
- 已验证环境和未验证项。

不要提交：

- EXE 与 build/release 缓存（除非用户明确要求成品包）；
- 用户日志；
- 串口抓包中的敏感数据；
- 本机绝对路径；
- `.pyc`、`.pytest_cache`、临时截图。

## 7. 诚实性

报告需要区分：

- 静态检查通过；
- 单元测试通过；
- GUI offscreen 构造通过；
- Windows 手工 UI 通过；
- 串口设备联调通过；
- PyInstaller 生成通过；
- 目标电脑运行通过。

未执行的层级明确写“未验证”，不能用“应该可以”替代事实。
