---
name: matlab-toolkit
description: "Build, modify, and package MATLAB GUI debugging tools for FPGA, SoPC, and embedded-board workflows. Use when Codex should create or maintain MATLAB uifigure-based upper-computer tools that reference MasterController v1.2 patterns for serial debugging, protocol frame generation/parsing, RX/TX logs, periodic sending, telemetry/register analysis, and EXE plus MATLAB Runtime delivery."
---

# MATLAB 调试工具 Skill

本 skill 用于指导 coding agent / Codex 直接制作、改造、维护 MATLAB GUI 上位机调试工具。

适用场景：

- MATLAB GUI 调试工具；
- FPGA / SoPC / embedded-board 上板调试；
- 串口调试；
- 协议帧生成 / 解析；
- 遥测 / 寄存器解析；
- RX/TX 彩色日志；
- 周期发送；
- EXE + MATLAB Runtime 交付。

## Core Workflow

1. 先确认新工程协议事实和串口参数，不要自行补关键字段。
2. 优先复用 `MasterController v1.2` 的串口、日志、周期发送、打包骨架。
3. 替换业务协议，例如指令定义、寄存器字段、上报解析、checksum/CRC、外层帧格式。
4. 保持 MasterController v1.2 的 UI 设计语言，除非用户明确要求换风格。
5. 做结构和静态检查；当前环境没有 MATLAB 时，不要假装 GUI、串口或打包已经跑通。
6. 用户要求“完整代码复制粘贴替换”时，明确替换路径并输出完整文件。

## Hard Rules

- 不要修改已验证模板源码逻辑，除非用户明确要求。
- 不要把 EXE、RuntimeInstaller、`dist`、`dist_build_work`、cache、临时 HTML 或其他打包产物放进 skill。
- 不要声称未实际运行过的 MATLAB GUI、串口通信、打包脚本或 Runtime 交付已验证通过。
- `dist` 是打包生成物，不随 skill 模板提供。
- ASCII CR/LF 必须显示为真实换行，不要显示字面量 `\r\n`。
- 主界面和子串口窗口的行为差异要保持清楚。
- 通道能力可复用，业务协议必须替换，不要机械复用旧工程协议逻辑。
- UDP / TCP / 文件解析工具可以复用 GUI 与日志思想，但不要硬套 `serialport` 通道逻辑。

## Reference Routing

- UI 布局、子串口窗口、颜色与信息架构：`reference/ui_design_language.md`。
- 可复用模块与必须替换模块：`reference/reuse_boundaries.md`。
- 串口收发、ASCII/HEX、CR/LF、彩色日志、timer 或清空行为：`reference/serial_logging.md`。
- EXE、RuntimeInstaller、README、`dist` 或交付目录：`reference/packaging.md`。
- 静态检查、环境诚实性、版本号残留、验收或完整代码输出：`reference/verification_and_delivery.md`。
