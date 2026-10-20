`timescale 1ns / 1ps
`default_nettype none

// Logical YIN frame adapter for a BRAM-backed RAM1 circular audio history.
// Generic address mapper. In this project at 6000 samples/s:
//   FRAME_LEN=480 => 80 ms frame; HOP=60 => 100 frames/s.
// The circular buffer holds >= FRAME_LEN+HOP samples, so one frame
// remains intact while the NEXT 10 ms of samples are written, provided
// the YIN computation finishes within that 10 ms.
//
// Input writer writes to write_address; pulse write_complete only AFTER
// BRAM confirms the write. When frame_ready pulses, the controller can
// start YIN if it is idle. Pulse snapshot_start on the same clock as that
// start. Feed YIN the fixed VIRTUAL_BASE as its frame_base argument.
// Translate ONLY YIN frame reads through yin_address_physical.
// Autocorrelation/notes accesses are left unchanged.
// Both YIN and the incoming writer share RAM1 and require arbitration.
module Frame_Ring_Address #(
    parameter int ADDR_WIDTH = 24,
    parameter int FRAME_LEN = 480,
    parameter int HOP = 48,
    parameter int RING_LEN = 1024, // power of two, >= FRAME_LEN + HOP
    parameter logic [ADDR_WIDTH-1:0] PHYSICAL_BASE = 24'h008000,
    parameter logic [ADDR_WIDTH-1:0] VIRTUAL_BASE  = 24'h010000
) (
    input  logic clk,
    input  logic rst_n,
    input  logic write_complete,       // 1 cycle after SRAM write completed
    input  logic snapshot_start,       // 1 cycle, at the start of a YIN run
    input  logic yin_busy,
    input  logic [ADDR_WIDTH-1:0] yin_address_logical,
    output logic [ADDR_WIDTH-1:0] yin_address_physical,
    output logic [ADDR_WIDTH-1:0] write_address,
    output logic frame_ready,         // pulse every HOP samples after first frame
    output logic frame_missed,        // sticky: next update arrived while YIN busy
    output logic [ADDR_WIDTH-1:0] yin_frame_base
);
    localparam int RING_BITS = $clog2(RING_LEN);
    localparam int COUNT_W = $clog2(FRAME_LEN + 1);
    localparam int HOP_W = (HOP > 1) ? $clog2(HOP) : 1;

    logic [RING_BITS-1:0] wr_idx, snapshot_idx;
    logic [COUNT_W-1:0] fill_count;
    logic [HOP_W-1:0] hop_count;
    logic filled;
    logic [RING_BITS-1:0] start_next;
    logic [RING_BITS-1:0] logical_idx;
    logic frame_address_range;

    assign yin_frame_base = VIRTUAL_BASE;
    assign write_address = PHYSICAL_BASE + (ADDR_WIDTH'(wr_idx) << 2);
    assign start_next = wr_idx - RING_BITS'(FRAME_LEN);
    assign frame_address_range =
        yin_address_logical >= VIRTUAL_BASE &&
        yin_address_logical < VIRTUAL_BASE + ADDR_WIDTH'(4*FRAME_LEN);
    assign logical_idx = RING_BITS'((yin_address_logical - VIRTUAL_BASE) >> 2);
    assign yin_address_physical = frame_address_range ?
        PHYSICAL_BASE + (ADDR_WIDTH'(snapshot_idx + logical_idx) << 2) :
        yin_address_logical;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            wr_idx <= '0;
            snapshot_idx <= '0;
            fill_count <= '0;
            hop_count <= '0;
            filled <= 1'b0;
            frame_ready <= 1'b0;
            frame_missed <= 1'b0;
        end else begin
            frame_ready <= 1'b0;
            if (snapshot_start)
                snapshot_idx <= wr_idx - RING_BITS'(FRAME_LEN);
            if (write_complete) begin
                wr_idx <= wr_idx + 1'b1; // power-of-two ring wraps naturally
                if (!filled) begin
                    if (fill_count == COUNT_W'(FRAME_LEN-1)) begin
                        filled <= 1'b1;
                        frame_ready <= 1'b1;
                        // complete current sample is at wr_idx; next
                        // logical frame begins wr_idx+1-FRAME_LEN.
                        if (yin_busy) frame_missed <= 1'b1;
                        hop_count <= '0;
                    end else fill_count <= fill_count + 1'b1;
                end else if (hop_count == HOP_W'(HOP-1)) begin
                    hop_count <= '0;
                    frame_ready <= 1'b1;
                    if (yin_busy) frame_missed <= 1'b1;
                end else hop_count <= hop_count + 1'b1;
            end
        end
    end
endmodule
`default_nettype wire
