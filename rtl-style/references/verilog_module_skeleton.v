// -----------------------------------------------------------------------------
// 模块名称：example_module
// 功能说明：普通单时钟 RTL 模块骨架。
// 时钟复位：clk，低有效同步复位 rst_n。
// 设计备注：在这里说明接口语义、时序假设和 CDC 状态。
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

    // 局部常量：用具名常量替代魔法数字。
    localparam integer ZERO_VALUE = 0;

    // 内部信号。
    reg  [DATA_WIDTH-1:0] data_n;
    reg                   valid_n;
    wire                  accept_w;

    // 简单连线逻辑。
    assign accept_w = valid_i;

    // 组合下一值逻辑：先给默认值，避免推断 latch。
    always @(*) begin
        data_n  = data_o;
        valid_n = 1'b0;

        if (accept_w) begin
            data_n  = data_i;
            valid_n = 1'b1;
        end
    end

    // 时序寄存器更新逻辑。
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
