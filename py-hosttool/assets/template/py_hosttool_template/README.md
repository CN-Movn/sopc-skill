# {{APP_NAME}} {{APP_VERSION}}

基于 `py-hosttool` skill 的 PySide6 上位机模板。

## 启动

```powershell
python -m pip install -r requirements.txt
python main.py
```

## 测试

```powershell
python -m pytest -q
```

## 打包

编辑 `build.bat` 中的 `PYTHON_EXE` 后运行：

```powershell
build.bat
```

默认入口布局由 `main.py` 决定。业务协议、寄存器和设备工作流需要按项目事实实现。
