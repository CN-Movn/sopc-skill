// -----------------------------------------------------------------------------
// 模块名称：example_module
// 功能说明：普通单时钟 RTL 模块骨架；示例展示寄存边界和中文注释格式。
// 时钟复位：clk；rst_n 为低有效同步复位（实际项目按上层接口约定调整）。
// 接口说明：valid_i 表示输入数据有效；valid_o 与 data_o 同拍有效。
// 数据流与延迟：输入 valid_i 被采样后，1 个时钟周期后输出；可每拍接收 1 个数据。
// CDC 说明：示例为单时钟域，不包含 CDC。
// 时序设计：输入先进入组合 next-value，再由输出寄存器切断组合路径；复杂逻辑应继续拆级。
// 边界说明：复位后 valid_o=0，data_o 清零；无输入时 valid_o 拉低。
// -----------------------------------------------------------------------------
`timescale 1ns / 1ps
`default_nettype none

module example_module #(
    parameter integer DATA_WIDTH = 8
) (
    input  wire                  clk,
    input  wire                  rst_n,

    input  wire [DATA_WIDTH-1:0] data_i,
    input  wire                  valid_i,
    output reg  [DATA_WIDTH-1:0] data_o,
    output reg                   valid_o
);

    reg [DATA_WIDTH-1:0] data_n;
    reg                  valid_n;

    // 组合下一值逻辑：默认保持 data，valid 默认无效；复杂 decode/算术不要无限堆在本级。
    always @(*) begin
        data_n  = data_o;
        valid_n = 1'b0;

        if (valid_i) begin
            data_n  = data_i;
            valid_n = 1'b1;
        end
    end

    // 输出寄存级：将模块主要数据路径限制在本模块内，便于后续 timing 分析和继续插入流水。
    always @(posedge clk) begin
        if (!rst_n) begin
            data_o  <= {DATA_WIDTH{1'b0}};
            valid_o <= 1'b0;
        end else begin
            data_o  <= data_n;
            valid_o <= valid_n;
        end
    end

endmodule

`default_nettype wire
