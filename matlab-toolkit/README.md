# MATLAB 调试工具 Skill

正式 skill name：`matlab-toolkit`。

这是一个用于指导 coding agent / Codex 制作、改造、维护 MATLAB GUI 上位机调试工具的 skill。

本 skill 的目标是让 agent 参考 `MasterController v1.2` 的源码模板、UI 设计语言和工程经验，做出同类 MATLAB GUI 上位机调试工具。

## 适用场景

- MATLAB GUI 调试工具；
- FPGA / SoPC / embedded-board 上板调试；
- 串口调试；
- 协议帧生成 / 解析；
- 遥测 / 寄存器解析；
- RX/TX 彩色日志；
- 周期发送；
- EXE + MATLAB Runtime 交付。

## 二进制产物说明

skill 包只携带源码模板和说明文档，不携带历史编译产物。

不应包含：

- `MasterController_v1_2.exe`
- `MasterController_v1_2_RuntimeInstaller.exe`
- `dist_build_work/`
- `runtime_installer/`
- `slprj/`
- `.asv`
- cache、临时 HTML、编译日志等中间文件

新工程应由用户或 agent 在目标 MATLAB 环境中重新运行自己的 build 脚本，生成自己的 `dist`。
