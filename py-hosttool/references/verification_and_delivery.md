# 验证与交付

## 1. Skill 包验证

在 Skill 根目录验证 Skill 本身，不要把这些命令当成生成项目的业务测试：

```bash
python scripts/validate_skill.py
```

普通模式允许缺失运行时依赖时完成结构检查，但会明确输出 skipped；发布前使用 `python scripts/validate_skill.py --strict`，任何 GUI pytest 或参考验证器未执行都返回非零。若环境安装了 Agent Skills 的参考验证器，也可单独追加 `skills-ref validate .`。这些检查不会证明生成的上位机协议或设备行为正确。

## 2. 生成项目静态门槛

至少执行：

```bash
python -m compileall -q .
python -m pytest -q
```

必要时增加 ruff/mypy，但不要为了工具形式牺牲项目环境兼容性。静态门槛应从生成项目根目录、干净目标环境执行，并记录 Python、PySide6、pyserial 和测试工具版本。

## 3. 协议与 client 测试

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

测试向量必须来自项目协议 source of truth，并锁定长度字段含义、字节序和完整 checksum/CRC 变体；来源工程的向量不能直接算作新项目证据。协议测试应尽量不导入 GUI。另需用 fake/loopback transport 覆盖 request 超时、取消、断连、重连、迟到响应、generation 丢弃、主动上报分流和不可幂等命令不重试。

## 4. GUI smoke test

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

## 5. Transport 与关闭路径

无真实设备也要能重复验证：

- 打开不存在端口、设备拔出、串口异常关闭和重连；
- worker/transport 的 start、close、shutdown 幂等，有限时间内停止且不遗留线程；
- 刷新、周期发送、pending request 和自动 workflow 在断连/取消后全部停止；
- 旧 connection/workflow generation 的 late callback 不改变新状态；
- fake/loopback 能注入半帧、粘包、坏帧、超时和高流量，测试队列背压与日志上限。

这些测试可在 pytest 中完成；真实设备联调另列为未验证或现场验证，不得用 offscreen 构造替代。

## 6. Windows 运行检查

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

## 7. PyInstaller 与 frozen smoke

- 优先使用项目固定 spec；
- 使用 PyInstaller 原生 PySide6/shiboken6 hooks；
- 不用无差别 `collect_all()`；
- hidden import 只添加实际需要项，如 `serial.tools.list_ports`；
- Conda 环境出现 DLL 冲突时，在 spec 中按确切文件名过滤，并记录原因；
- 在干净、固定版本的构建环境执行 clean build，先验证 onedir，再决定 onefile；
- 对生成的 onedir 运行 frozen smoke：在无设备环境启动实际 EXE，构造公开主窗口、确认无缺失 DLL/hidden import，并通过项目定义的无交互退出方式（例如 `--smoke-test` 或等价入口）结束；记录输出目录、版本和结果；
- onefile 只有在 onedir 和 frozen smoke 通过后才交付，并单独记录启动/解包失败；
- `console=False` 只用于正式 GUI 版本，调试阶段保留 console 更利于定位。

## 8. 交付内容

源码交付建议包含：

- 源码；
- requirements；
- tests；
- build.bat/spec；
- README；
- 协议或寄存器文档；
- 已验证环境和未验证项。
- onedir/frozen smoke 的命令、环境、产物哈希和结果；若用户要求 onefile，再附 onefile 结果。

不要提交：

- EXE 与 build/release 缓存（除非用户明确要求成品包）；
- 用户日志；
- 串口抓包中的敏感数据；
- 本机绝对路径；
- `.pyc`、`.pytest_cache`、临时截图。

## 9. 诚实性

报告需要区分：

- 静态检查通过；
- 单元测试通过；
- GUI offscreen 构造通过；
- Windows 手工 UI 通过；
- 串口设备联调通过；
- PyInstaller 生成通过；
- 目标电脑运行通过。

未执行的层级明确写“未验证”，不能用“应该可以”替代事实。
