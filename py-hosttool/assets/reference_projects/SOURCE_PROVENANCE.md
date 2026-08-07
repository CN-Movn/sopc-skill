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
