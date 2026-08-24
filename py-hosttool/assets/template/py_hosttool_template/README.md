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

默认先运行测试，再生成便于排查依赖问题的 onedir 产物，并以
`--smoke-test` 启动真实 EXE、构造窗口后通过正常关闭路径退出：

```powershell
build.bat
```

本工程由 bootstrap 生成时必须用 `--baudrate` 明确指定串口下拉框的初始值。该值必须来自目标 FPGA/SoPC 的接口文档，不能从来源工程或示例默认值推断。

需要指定解释器时，先设置 `PYTHON_EXE` 环境变量。自动 frozen smoke 不能替代
目标机人工检查；只有 onedir 在目标环境验证通过后，才根据交付需求另行制作 onefile。

默认入口布局由 `main.py` 决定。业务协议、寄存器和设备工作流需要按项目事实实现。
