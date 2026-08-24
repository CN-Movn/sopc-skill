from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_rtl_style.py"
SPEC = importlib.util.spec_from_file_location("check_rtl_style", SCRIPT)
assert SPEC and SPEC.loader
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


HEADER = """\
// 模块名称：{name}
// 功能说明：测试模块。
// 时钟复位：单时钟同步复位。
// 接口说明：测试接口。
// 数据流与延迟：测试延迟。
// CDC 说明：无 CDC。
// 时序设计：寄存边界。
// 边界说明：复位边界。
"""


def analyze(source: str, suffix: str = ".v") -> tuple[list[str], list[str]]:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / f"sample{suffix}"
        path.write_text(source, encoding="utf-8")
        return CHECKER.analyze_file(path)


class CheckerTests(unittest.TestCase):
    def test_shipped_templates_pass(self) -> None:
        reference_root = Path(__file__).resolve().parents[1] / "references"
        for name in ("verilog_module_skeleton.v", "axis_registered_stage_template.v"):
            with self.subTest(name=name):
                errors, warnings = CHECKER.analyze_file(reference_root / name)
                self.assertEqual([], errors)
                self.assertEqual([], warnings)

    def test_comment_density_is_not_a_quality_metric(self) -> None:
        body = "\n".join(f"    wire unused_{index} = 1'b0;" for index in range(90))
        source = (
            HEADER.format(name="large_but_simple")
            + "`default_nettype none\nmodule large_but_simple;\n"
            + body
            + "\nendmodule\n`default_nettype wire\n"
        )
        errors, warnings = analyze(source)
        self.assertEqual([], errors)
        self.assertFalse(any("中文注释" in warning and "行" in warning for warning in warnings))

    def test_comments_and_strings_do_not_create_structure(self) -> None:
        source = (
            HEADER.format(name="masked_tokens")
            + "`default_nettype none\nmodule masked_tokens(input wire clk);\n"
            + "    // module fake; always @(*) begin case (x) endcase\n"
            + "    /* always @(*) begin else if (x) fake = a ? b : c; end */\n"
            + '    wire [31:0] label = "case ? begin end";\n'
            + "    // 单语句时序逻辑：验证拆行 always 不吞掉后续代码。\n"
            + "    always @(posedge clk)\n        label_reg <= label;\n"
            + "    reg [31:0] label_reg;\nendmodule\n`default_nettype wire\n"
        )
        errors, warnings = analyze(source)
        self.assertEqual([], errors)
        self.assertEqual([], warnings)

    def test_directive_text_inside_comment_does_not_satisfy_policy(self) -> None:
        source = (
            HEADER.format(name="commented_directive")
            + "// `default_nettype none\nmodule commented_directive;\n"
            + "endmodule\n`default_nettype wire\n"
        )
        errors, _warnings = analyze(source)
        self.assertIn("缺少 `default_nettype none`。", errors)

    def test_case_endcase_does_not_terminate_always_block_early(self) -> None:
        source = (
            HEADER.format(name="case_block")
            + "`default_nettype none\nmodule case_block(input wire a, output reg y);\n"
            + "    // 组合选择：case 明确表达互斥选择，末尾逻辑仍属于同一组合块。\n"
            + "    always @(*) begin\n        y = 1'b0;\n        case (a)\n"
            + "            1'b1: y = 1'b1;\n            default: y = 1'b0;\n"
            + "        endcase\n        if (!a) begin\n            y = 1'b0;\n        end\n    end\n"
            + "endmodule\n`default_nettype wire\n"
        )
        masked = CHECKER.mask_comments_and_strings(source)
        blocks = CHECKER.extract_always_blocks(source.splitlines(), masked.splitlines())
        self.assertEqual(1, len(blocks))
        self.assertIn("if (!a)", blocks[0][2])
        errors, _warnings = analyze(source)
        self.assertEqual([], errors)

    def test_second_module_cannot_reuse_previous_header(self) -> None:
        source = (
            HEADER.format(name="first")
            + "`default_nettype none\nmodule first; endmodule\n"
            + "module second; endmodule\n`default_nettype wire\n"
        )
        errors, _warnings = analyze(source)
        self.assertTrue(any("module second" in error for error in errors))

    def test_systemverilog_and_always_comb_are_checked(self) -> None:
        source = (
            HEADER.format(name="sv_module")
            + "`default_nettype none\nmodule sv_module(input logic a, output logic y);\n"
            + "    // 组合输出：完整赋值避免 latch，本例验证 always_comb 路由。\n"
            + "    always_comb begin\n        y = a;\n    end\n"
            + "endmodule\n`default_nettype wire\n"
        )
        errors, warnings = analyze(source, suffix=".sv")
        self.assertEqual([], errors)
        self.assertEqual([], warnings)

    def test_real_ready_dependency_still_warns(self) -> None:
        source = (
            HEADER.format(name="ready_chain")
            + "`default_nettype none\nmodule ready_chain(\n"
            + "    input wire downstream_tready, output wire upstream_tready);\n"
            + "    assign upstream_tready = downstream_tready;\n"
            + "endmodule\n`default_nettype wire\n"
        )
        errors, warnings = analyze(source)
        self.assertEqual([], errors)
        self.assertTrue(any("ready" in warning for warning in warnings))


if __name__ == "__main__":
    unittest.main()
