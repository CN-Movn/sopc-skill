# rtl-style

面向 AMD/Xilinx Vivado 的 Verilog RTL 创建、修改与评审 skill，强调中文设计意图注释、Timing by Construction、CDC/握手边界、可预测的综合推断和诚实的验证边界。

Current release: v2.2.2

## 目录

- `VERSION`：机器可读的唯一当前版本源。
- `CHANGELOG.md`：按 Keep a Changelog 风格维护发布记录。
- `SKILL.md`：coding agent 的入口规则。
- `references/`：注释合同、AMD 方法论、时序设计与 RTL 模板。
- `scripts/check_rtl_style.py`：Verilog/SystemVerilog 静态 preflight checker。
- `scripts/validate_version.py`：版本元数据一致性检查。
- `tests/`：checker 与版本元数据 regression tests。

## 统一发布流程

开发阶段按以下顺序维护版本信息：

```text
开发阶段
   ↓
CHANGELOG 顶部维护 Unreleased
   ↓
功能/规则/测试完成
   ↓
判断 PATCH / MINOR / MAJOR
   ↓
更新 VERSION
   ↓
将 Unreleased 转为带日期的正式版本
   ↓
新建空 Unreleased 区域供下一轮开发使用
   ↓
README 当前版本同步
   ↓
运行 validator / tests
   ↓
打包 / commit / tag
```

每次正式发布时，`VERSION`、CHANGELOG 正式标题、README 当前版本和（若维护文件清单）manifest 必须同步；版本检查与 regression tests 全部通过后再提交。推荐使用带 Skill 名称的 tag，例如 `rtl-style-v2.2.2`，避免与同仓库的其他 Skill 混淆。

版本规则采用 Semantic Versioning：规则修正、checker 修复、测试增强、模板 bugfix 和兼容性工程增强使用 PATCH；新增向后兼容能力使用 MINOR；改变核心行为合同或兼容性时才使用 MAJOR。
