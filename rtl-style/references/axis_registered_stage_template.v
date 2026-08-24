// -----------------------------------------------------------------------------
// 模块名称：axis_registered_stage_template
// 功能说明：单时钟 AXI4-Stream 弹性寄存级，切断前向数据路径并提供 1 个额外暂存位置。
// 时钟复位：clk；rst_n 为低有效同步复位（集成时服从项目 reset 约定）。
// 接口说明：标准 valid/ready；模板覆盖 tdata/tkeep/tlast/tuser，输出阻塞期间这些信号保持稳定。
// 数据流与延迟：无阻塞时前向增加 1 拍且稳态每拍 1 beat；skid 排空后的 ready 恢复允许 1 拍气泡。
// CDC 说明：单时钟域，不承担 CDC；跨时钟必须在本模块外使用 async FIFO/握手。
// 时序设计：s_axis_tready 仅由本地占用状态决定，避免把下游 ready 长距离组合反馈到上游。
// 边界说明：DATA_WIDTH 必须为正且按字节对齐；下游阻塞时额外缓存 1 beat；其他 sideband 需同拍扩展。
//             复位只清 valid，valid=0 时 payload/sideband 不作语义保证；异步复位必须在模块外同步释放。
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

    wire input_fire_w;
    wire output_accept_w;

    // ready 只依赖本地 skid 占用状态：避免形成“下游 ready -> 多级组合 -> 上游 ready”的长反压链。
    assign s_axis_tready  = !skid_tvalid_r;
    assign input_fire_w   = s_axis_tvalid && s_axis_tready;
    assign output_accept_w = !m_axis_tvalid || m_axis_tready;

    // 弹性寄存逻辑：输出被阻塞时保持 m_axis_*，并将额外输入 beat 暂存在 skid 寄存器。
    // 只复位两个 valid 位；无效 payload 由 valid 屏蔽，避免宽数据与 sideband 机械增加 reset control set。
    always @(posedge clk) begin
        if (!rst_n) begin
            m_axis_tvalid  <= 1'b0;
            skid_tvalid_r  <= 1'b0;
        end else begin
            if (output_accept_w) begin
                if (skid_tvalid_r) begin
                    m_axis_tdata  <= skid_tdata_r;
                    m_axis_tkeep  <= skid_tkeep_r;
                    m_axis_tvalid <= 1'b1;
                    m_axis_tlast  <= skid_tlast_r;
                    m_axis_tuser  <= skid_tuser_r;
                    skid_tvalid_r <= 1'b0;
                end else if (input_fire_w) begin
                    m_axis_tdata  <= s_axis_tdata;
                    m_axis_tkeep  <= s_axis_tkeep;
                    m_axis_tvalid <= 1'b1;
                    m_axis_tlast  <= s_axis_tlast;
                    m_axis_tuser  <= s_axis_tuser;
                end else begin
                    m_axis_tvalid <= 1'b0;
                end
            end else if (input_fire_w) begin
                skid_tdata_r  <= s_axis_tdata;
                skid_tkeep_r  <= s_axis_tkeep;
                skid_tvalid_r <= 1'b1;
                skid_tlast_r  <= s_axis_tlast;
                skid_tuser_r  <= s_axis_tuser;
            end
        end
    end

endmodule

`default_nettype wire
