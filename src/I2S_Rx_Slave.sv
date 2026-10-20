`timescale 1ns / 1ps
`default_nettype none
// Optional simulation/RTL correction: captures exactly 24 I2S bits per WS=1
// channel. The uploaded original keeps shifting during 32-bit channel padding.
// This file is only compiled if run_wav.py receives --fixed-rx.
module I2S_Rx_Slave #(
    parameter int BIT_WIDTH = 24
)(
    input  logic clk,
    input  logic bclk,
    input  logic ws,
    input  logic sd,
    input  logic rst_n,
    output logic [BIT_WIDTH-1:0] sample,
    output logic busy
);
    localparam int COUNT_W = (BIT_WIDTH > 1) ? $clog2(BIT_WIDTH + 1) : 1;
    logic [2:0] bclk_sync, ws_sync;
    wire bclk_rise = bclk_sync[1] && !bclk_sync[2];
    wire ws_rise   = ws_sync[1] && !ws_sync[2];
    wire ws_fall   = !ws_sync[1] && ws_sync[2];
    logic capturing, skip_delay;
    logic [COUNT_W-1:0] count;
    logic [BIT_WIDTH-1:0] shifted;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            bclk_sync <= '0;
            ws_sync <= '0;
            sample <= '0;
            shifted <= '0;
            capturing <= 1'b0;
            skip_delay <= 1'b0;
            count <= '0;
            busy <= 1'b1;
        end else begin
            bclk_sync <= {bclk_sync[1:0], bclk};
            ws_sync <= {ws_sync[1:0], ws};
            if (ws_rise) begin
                capturing <= 1'b1;
                skip_delay <= 1'b1;
                count <= '0;
                busy <= 1'b1;
            end else if (ws_fall && capturing) begin
                // Malformed or shortened I2S word; do not publish partial data.
                capturing <= 1'b0;
                busy <= 1'b1;
            end else if (capturing && bclk_rise) begin
                if (skip_delay) begin
                    skip_delay <= 1'b0;
                end else if (count < COUNT_W'(BIT_WIDTH)) begin
                    shifted <= {shifted[BIT_WIDTH-2:0], sd};
                    count <= count + 1'b1;
                    if (count == COUNT_W'(BIT_WIDTH-1)) begin
                        sample <= {shifted[BIT_WIDTH-2:0], sd};
                        capturing <= 1'b0;
                        busy <= 1'b0;
                    end
                end
            end
        end
    end
endmodule
`default_nettype wire
