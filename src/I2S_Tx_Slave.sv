`default_nettype none

// 24-bit (configurable width) I2S transmitter, WS-HIGH channel only.
// Assumes conventional I2S timing: WS changes one BCLK period before MSB.
// Latches a sample on the WS rising edge, skips the transition/delay slot,
// and sends bit BIT_WIDTH-1 on the NEXT detected BCLK falling edge.
// SD changes on BCLK falling edge; DAC samples it on rising edge.
// One 32-bit I2S time slot per channel at 48 kHz is compatible, with
// all slots after the 24 data bits driven low.
module I2S_Tx_Slave #(
    parameter int BIT_WIDTH = 24
) (
    input  logic clk,
    input  logic bclk,
    input  logic ws,
    output logic sd,
    input  logic rst_n,
    input  logic [BIT_WIDTH-1:0] sample,
    output logic busy
);
    localparam int COUNT_WIDTH = $clog2(BIT_WIDTH + 1);

    // Synchronize asynchronous serial clocks to the 100 MHz system clock.
    logic [2:0] bclk_sync, ws_sync;
    wire bclk_fall = bclk_sync[2] && !bclk_sync[1];
    wire ws_rise   = !ws_sync[2] && ws_sync[1];
    wire ws_fall   = ws_sync[2] && !ws_sync[1];

    logic active;
    logic [COUNT_WIDTH-1:0] bit_count;
    logic [BIT_WIDTH-1:0] shift_reg;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            bclk_sync <= '0;
            ws_sync   <= '0;
            sd        <= 1'b0;
            busy      <= 1'b0;
            active    <= 1'b0;
            bit_count <= '0;
            shift_reg <= '0;
        end else begin
            bclk_sync <= {bclk_sync[1:0], bclk};
            ws_sync   <= {ws_sync[1:0], ws};

            if (ws_rise) begin
                // This WS/BCLK transition is the I2S one-bit delay,
                // NOT the first payload bit.
                shift_reg <= sample;
                bit_count <= '0;
                active    <= 1'b1;
                busy      <= 1'b1;
                sd        <= 1'b0;
            end else if (ws_fall) begin
                active    <= 1'b0;
                busy      <= 1'b0;
                bit_count <= '0;
                sd        <= 1'b0;
            end else if (bclk_fall) begin
                if (active && bit_count < BIT_WIDTH) begin
                    sd        <= shift_reg[BIT_WIDTH-1];
                    shift_reg <= {shift_reg[BIT_WIDTH-2:0], 1'b0};
                    bit_count <= bit_count + 1'b1;
                end else begin
                    // 32-bit I2S slots: remaining bits are padding.
                    sd <= 1'b0;
                end
            end
        end
    end
endmodule

`default_nettype wire
