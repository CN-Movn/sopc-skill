// -----------------------------------------------------------------------------
// 模块名称：axis_registered_stage_template
// 功能说明：单时钟 AXI4-Stream 寄存级，带 1 级 skid buffer，支持 backpressure。
// 时钟复位：clk，低有效同步复位 rst_n。
// 延迟说明：前向数据路径增加 1 级寄存。
// 吞吐说明：上下游都不阻塞时，每周期传输 1 个 beat。
// -----------------------------------------------------------------------------
`timescale 1ns / 1ps
`default_nettype none

module axis_registered_stage_template #(
    parameter integer DATA_WIDTH = 32,
    parameter integer KEEP_WIDTH = (DATA_WIDTH / 8),
    parameter integer USER_WIDTH = 1
) (
    input  wire                  clk,
    input  wire                  rst_n,

    input  wire [DATA_WIDTH-1:0] s_axis_tdata,
    input  wire [KEEP_WIDTH-1:0] s_axis_tkeep,
    input  wire                  s_axis_tvalid,
    output wire                  s_axis_tready,
    input  wire                  s_axis_tlast,
    input  wire [USER_WIDTH-1:0] s_axis_tuser,

    output reg  [DATA_WIDTH-1:0] m_axis_tdata,
    output reg  [KEEP_WIDTH-1:0] m_axis_tkeep,
    output reg                   m_axis_tvalid,
    input  wire                  m_axis_tready,
    output reg                   m_axis_tlast,
    output reg  [USER_WIDTH-1:0] m_axis_tuser
);

    reg [DATA_WIDTH-1:0] skid_tdata_r;
    reg [KEEP_WIDTH-1:0] skid_tkeep_r;
    reg                  skid_tvalid_r;
    reg                  skid_tlast_r;
    reg [USER_WIDTH-1:0] skid_tuser_r;

    wire s_axis_fire;
    wire output_ready_w;

    // ready 只依赖本地 skid 寄存器，避免形成很深的下游组合反压路径。
    assign s_axis_tready = !skid_tvalid_r;
    assign s_axis_fire   = s_axis_tvalid && s_axis_tready;
    assign output_ready_w = !m_axis_tvalid || m_axis_tready;

    always @(posedge clk) begin
        if (!rst_n) begin
            m_axis_tdata   <= {DATA_WIDTH{1'b0}};
            m_axis_tkeep   <= {KEEP_WIDTH{1'b0}};
            m_axis_tvalid  <= 1'b0;
            m_axis_tlast   <= 1'b0;
            m_axis_tuser   <= {USER_WIDTH{1'b0}};
            skid_tdata_r   <= {DATA_WIDTH{1'b0}};
            skid_tkeep_r   <= {KEEP_WIDTH{1'b0}};
            skid_tvalid_r  <= 1'b0;
            skid_tlast_r   <= 1'b0;
            skid_tuser_r   <= {USER_WIDTH{1'b0}};
        end else begin
            if (output_ready_w) begin
                if (skid_tvalid_r) begin
                    m_axis_tdata  <= skid_tdata_r;
                    m_axis_tkeep  <= skid_tkeep_r;
                    m_axis_tvalid <= 1'b1;
                    m_axis_tlast  <= skid_tlast_r;
                    m_axis_tuser  <= skid_tuser_r;
                    skid_tvalid_r <= 1'b0;
                end else if (s_axis_fire) begin
                    m_axis_tdata  <= s_axis_tdata;
                    m_axis_tkeep  <= s_axis_tkeep;
                    m_axis_tvalid <= 1'b1;
                    m_axis_tlast  <= s_axis_tlast;
                    m_axis_tuser  <= s_axis_tuser;
                end else begin
                    m_axis_tvalid <= 1'b0;
                end
            end else begin
                // 输出被阻塞时，输出 payload 必须保持稳定，skid 暂存额外 1 个输入 beat。
                if (s_axis_fire) begin
                    skid_tdata_r  <= s_axis_tdata;
                    skid_tkeep_r  <= s_axis_tkeep;
                    skid_tvalid_r <= 1'b1;
                    skid_tlast_r  <= s_axis_tlast;
                    skid_tuser_r  <= s_axis_tuser;
                end
            end
        end
    end

endmodule

`default_nettype wire
