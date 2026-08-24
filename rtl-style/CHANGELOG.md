# Changelog

## Unreleased

## [2.2.2] - 2026-08-24

### Added

- 增加 checker regression coverage，覆盖注释/字符串屏蔽、`case/endcase` 结构、多模块头部、SystemVerilog/`always_comb` 以及真实 ready 依赖告警。
- 强化 Timing Design Gate 与 Functional Verification Gate 的工作流说明，使非平凡 RTL 修改的架构与验证边界可复核。

### Changed

- 增强 checker 的轻量结构解析，避免注释、字符串和 `case/endcase` 等文本误导启发式检查。
- 将注释质量判断聚焦于设计意图与合同要求，不再按注释数量或密度评价模块。
- 优化随附 RTL 模板的 reset 策略，只复位必要的 valid/控制状态，避免无效 payload 机械引入 reset control set。

### Fixed

- 修正 checker 对 SystemVerilog/`always_comb`、连续多模块源码以及组合块内部结构的覆盖与误报问题。

## [2.2.1]

- Previous published baseline.
- Earlier detailed changelog is not reconstructed here.
