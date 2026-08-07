# AMD 官方资料真实性与版本核验

本文件记录 `rtl-style` 使用的三份 AMD Vivado 官方 User Guide 的来源、版本与核验方式。目的不是把整本 PDF 冻结进 Skill，而是保证 Skill 中每条方法论都能回溯到真实的 AMD 官方文档。

## 核验原则

1. **只把 `docs.amd.com` 视为本 Skill 的一手来源。** 历史 `xilinx.com/docs.xilinx.com` 链接仅作为品牌迁移背景，不作为当前版本号依据。
2. **文档 ID、标题、版本、发布日期必须由 AMD 官方页面直接给出。** 不根据搜索摘要、博客、论坛或二手文章自行推断。
3. **Skill 不捆绑整本官方 PDF。** 原因是 PDF 体积大、版本会更新，而且 Coding Agent 真正需要的是与 RTL 编码直接相关的工程化提炼。
4. **官方事实与 Skill 启发式必须分开。** AMD 没有规定“超过 N 个 if 就一定 timing fail”；本 Skill 中的静态阈值只用于代码审查预警。

## 当前核验基线（2026-08-07）

| 文档 | AMD 官方标题 | 当前核验版本 | 官方发布日期 | 官方入口 |
|---|---|---:|---:|---|
| UG949 | UltraFast Design Methodology Guide for FPGAs and SoCs | 2026.1 English | 2026-06-23 | https://docs.amd.com/r/en-US/ug949-vivado-design-methodology |
| UG906 | Vivado Design Suite User Guide: Design Analysis and Closure Techniques | 2026.1 English | 2026-06-23 | https://docs.amd.com/r/en-US/ug906-vivado-design-analysis |
| UG901 | Vivado Design Suite User Guide: Synthesis | 2026.1 English | 2026-07-08 | https://docs.amd.com/r/en-US/2026.1/ug901-vivado-synthesis/Synthesis-Methodology |

以上三个 ID、标题和版本均在 AMD 官方文档站点的当前页面中直接显示。

## PDF 交叉核验说明

AMD 文档站点的历史版本页会暴露同名 PDF（例如 `ug901-vivado-synthesis.pdf`）。本轮核验中，AMD 官方 UG901 v2021.1 PDF 可直接打开，其封面明确显示：

- `Vivado Design Suite User Guide`
- `Synthesis`
- `UG901 (v2021.1) July 14, 2021`
- Xilinx 官方标识（品牌迁移前）

这用于确认“UG901 不是自行编造的文档名/编号”，但 **本 Skill 的规则正文优先按 2026.1 AMD 在线文档核对**，而不是冻结在 2021.1。

UG949/UG906 的 AMD 历史版本页同样明确暴露官方 PDF 文件名和 Document ID；由于当前工具环境对超大 PDF 有内容长度/缓存限制，本 Skill 不声称已在本地完整保存这两本 PDF。真实性判断依据是 AMD 官方当前文档页面及其历史版本页面，而不是第三方镜像。

## 版本使用规则

- 如果项目明确使用较旧 Vivado，Agent 可以参考相应版本的 UG 页面，但不得把较新版本新增特性当作旧版本必然支持。
- 只要规则涉及 RTL 基础结构（pipeline、fan-in/fanout、reset/control set、RAM/DSP inference、priority processing），优先使用本 Skill 已提炼且跨版本稳定的方法论。
- 如果 AMD 后续版本修改了某项具体命令、属性或语义，应以项目版本对应官方文档为准，并更新本文件的核验基线。
