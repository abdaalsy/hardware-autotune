`timescale 1ns / 1ps
`default_nettype none

// -----------------------------------------------------------------------------
// Match_Note
//
// Binary-searches a sorted (ascending) array of note frequencies stored in
// memory for the note closest to `pitch` (on a log scale), then drives
//     shift = note_freq / pitch
// using the CORDIC divider.
//
// Memory layout : note[i] lives at  notes_addr_base + 4*i  (3 used bytes,
//                 4 byte stride).
//
// Search        : invariant-free bisection on [lo, hi] until hi - lo <= 1.
//                 Afterwards f[lo] and f[hi] are read and:
//                   * pitch <= f[lo]            -> pick lo   (below range / exact)
//                   * pitch >= f[hi]            -> pick hi   (above range / exact)
//                   * otherwise compare ratios  pitch/f[lo]  vs  f[hi]/pitch
//                     (equivalent to pitch^2 vs f[lo]*f[hi], i.e. pitch vs the
//                      geometric mean of the two notes, but needs only two
//                      divisions and no multiply / no overflow).
//
// CORDIC use    : opcode 0 = divide, computes B/A.
//
// Number format : CORDIC operands/answer are signed 12.12 fixed point (24 bit,
//                 MSB = sign, so representable range is about +/-2047.9998).
//                 `pitch` and the table entries must share ONE format.
//                 IN_FRAC_BITS says how many fractional bits that format has;
//                 operands are shifted up by (12 - IN_FRAC_BITS) on their way
//                 into the CORDIC so it always sees true 12.12 values.
//                   IN_FRAC_BITS = 12 : pitch/table already 12.12 (no shift)
//                   IN_FRAC_BITS = 0  : pitch/table are integer Hz (shift 12,
//                                       so max usable frequency is 2047 Hz)
//                 Ratios (and so `shift`) come back in 12.12.
// -----------------------------------------------------------------------------
module Match_Note #(
    parameter int BIT_WIDTH    = 24,
    parameter int IN_FRAC_BITS = 12   // fractional bits of pitch & table entries
) (
    input  logic                        rst_n,      // async, active low
    input  logic                        clk,
    input  logic [BIT_WIDTH-1:0]        num_notes,
    input  logic [BIT_WIDTH-1:0]        pitch,
    input  logic                        start,
    output logic [BIT_WIDTH-1:0]        shift,
    output logic                        busy,

    // Memory interface
    output logic                        start_read,
    output logic                        start_write, // unused
    output logic [BIT_WIDTH-1:0]        address,
    output logic [BIT_WIDTH-1:0]        val_out,
    input  logic [BIT_WIDTH-1:0]        val_in,
    input  logic                        res,

    // CORDIC engine interface.  Division (opcode=0) performs B/A,
    // Square root (opcode=1) performs sqrt(A)
    output logic                        cordic_start,
    output logic                        opcode,
    output logic signed [BIT_WIDTH-1:0] A_out,
    output logic signed [BIT_WIDTH-1:0] B_out,
    input  logic signed [BIT_WIDTH-1:0] ans,
    input  logic                        cordic_busy
);

    // -------------------------------------------------------------------------
    // Constants
    // -------------------------------------------------------------------------
    // Base address of the notes array (placeholder - change as needed).
    localparam logic [BIT_WIDTH-1:0] notes_addr_base = 'h10;

    localparam logic OP_DIV  = 1'b0;
    localparam logic OP_SQRT = 1'b1; // unused

    // CORDIC fixed-point format is 12.12
    localparam int CORDIC_FRAC_BITS = 12;
    localparam int SHIFT_UP         = CORDIC_FRAC_BITS - IN_FRAC_BITS;

    if (SHIFT_UP < 0) begin : g_bad_frac
        $error("IN_FRAC_BITS (%0d) must be <= %0d", IN_FRAC_BITS, CORDIC_FRAC_BITS);
    end

    // -------------------------------------------------------------------------
    // Unused memory outputs
    // -------------------------------------------------------------------------
    assign start_write = 1'b0;
    assign val_out     = '0;

    // -------------------------------------------------------------------------
    // State
    // -------------------------------------------------------------------------
    typedef enum logic [2:0] {
        S_IDLE,
        S_SEARCH,       // bisection step / loop exit check
        S_RD_ISSUE,     // drive address + pulse start_read
        S_RD_WAIT,      // wait for rising edge on res
        S_DECIDE,       // f[lo], f[hi] known: pick note or go compare
        S_CORDIC_ISSUE, // drive operands + pulse cordic_start
        S_CORDIC_WAIT,  // wait for falling edge on cordic_busy
        S_DONE          // one-cycle exit for degenerate inputs
    } state_t;

    // What the data coming back from memory is for.
    typedef enum logic [1:0] {
        RD_MID,
        RD_LO,
        RD_HI
    } rd_purpose_t;

    // What the CORDIC division in flight is for.
    typedef enum logic [1:0] {
        CD_RATIO_LO,    // r1 = pitch / f_lo
        CD_RATIO_HI,    // r2 = f_hi  / pitch
        CD_SHIFT        // shift = note / pitch
    } cd_purpose_t;

    state_t      state;
    rd_purpose_t rd_purpose;
    cd_purpose_t cd_purpose;

    // Frequencies are signed (same sign convention as the CORDIC)
    logic signed [BIT_WIDTH-1:0] pitch_r;
    logic signed [BIT_WIDTH-1:0] f_lo, f_hi;
    logic signed [BIT_WIDTH-1:0] note_sel;
    // Array indices are unsigned
    logic [BIT_WIDTH-1:0]        lo, hi;
    logic [BIT_WIDTH-1:0]        rd_idx;
    logic signed [BIT_WIDTH-1:0] ratio_lo;      // pitch / f_lo

    // Edge detectors
    logic res_prev, cordic_busy_prev;
    wire  res_rise    =  res              && !res_prev;
    wire  cordic_fall = !cordic_busy      &&  cordic_busy_prev;

    // Midpoint (overflow-safe)
    wire [BIT_WIDTH-1:0] mid_c = lo + ((hi - lo) >> 1);

    assign busy = (state != S_IDLE);

    // -------------------------------------------------------------------------
    // FSM
    // -------------------------------------------------------------------------
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state            <= S_IDLE;
            rd_purpose       <= RD_MID;
            cd_purpose       <= CD_SHIFT;
            pitch_r          <= '0;
            lo               <= '0;
            hi               <= '0;
            rd_idx           <= '0;
            f_lo             <= '0;
            f_hi             <= '0;
            note_sel         <= '0;
            ratio_lo         <= '0;
            shift            <= '0;
            start_read       <= 1'b0;
            address          <= '0;
            cordic_start     <= 1'b0;
            opcode           <= OP_DIV;
            A_out            <= '0;
            B_out            <= '0;
            res_prev         <= 1'b0;
            cordic_busy_prev <= 1'b0;
        end else begin
            // Single-cycle pulses by default
            start_read       <= 1'b0;
            cordic_start     <= 1'b0;

            // Edge-detect history
            res_prev         <= res;
            cordic_busy_prev <= cordic_busy;

            unique case (state)
                // -------------------------------------------------------------
                S_IDLE: begin
                    if (start) begin
                        pitch_r <= $signed(pitch);
                        if (num_notes == '0 || $signed(pitch) <= 0) begin
                            // Nothing to match / non-positive pitch (would
                            // divide by zero or is meaningless)
                            shift <= '0;
                            state <= S_DONE;
                        end else begin
                            lo    <= '0;
                            hi    <= num_notes - 1'b1;
                            state <= S_SEARCH;
                        end
                    end
                end

                // -------------------------------------------------------------
                S_SEARCH: begin
                    if (hi - lo > 1) begin
                        rd_idx     <= mid_c;
                        rd_purpose <= RD_MID;
                    end else begin
                        // Pointers adjacent (or equal): fetch the two candidates
                        rd_idx     <= lo;
                        rd_purpose <= RD_LO;
                    end
                    state <= S_RD_ISSUE;
                end

                // -------------------------------------------------------------
                S_RD_ISSUE: begin
                    address    <= notes_addr_base + (rd_idx << 2);
                    start_read <= 1'b1;
                    state      <= S_RD_WAIT;
                end

                // -------------------------------------------------------------
                S_RD_WAIT: begin
                    if (res_rise) begin
                        unique case (rd_purpose)
                            RD_MID: begin
                                if ($signed(val_in) <= pitch_r) lo <= rd_idx;
                                else                   hi <= rd_idx;
                                state <= S_SEARCH;
                            end
                            RD_LO: begin
                                f_lo       <= $signed(val_in);
                                rd_idx     <= hi;
                                rd_purpose <= RD_HI;
                                state      <= S_RD_ISSUE;
                            end
                            RD_HI: begin
                                f_hi  <= $signed(val_in);
                                state <= S_DECIDE;
                            end
                            default: state <= S_IDLE;
                        endcase
                    end
                end

                // -------------------------------------------------------------
                S_DECIDE: begin
                    if (pitch_r <= f_lo) begin
                        note_sel   <= f_lo;
                        cd_purpose <= CD_SHIFT;
                    end else if (pitch_r >= f_hi) begin
                        note_sel   <= f_hi;
                        cd_purpose <= CD_SHIFT;
                    end else begin
                        // f_lo < pitch < f_hi : compare on a log scale
                        cd_purpose <= CD_RATIO_LO;
                    end
                    state <= S_CORDIC_ISSUE;
                end

                // -------------------------------------------------------------
                S_CORDIC_ISSUE: begin
                    opcode       <= OP_DIV;           // B / A
                    cordic_start <= 1'b1;
                    unique case (cd_purpose)
                        CD_RATIO_LO: begin            // pitch / f_lo
                            A_out <= f_lo    <<< SHIFT_UP;
                            B_out <= pitch_r <<< SHIFT_UP;
                        end
                        CD_RATIO_HI: begin            // f_hi / pitch
                            A_out <= pitch_r <<< SHIFT_UP;
                            B_out <= f_hi    <<< SHIFT_UP;
                        end
                        CD_SHIFT: begin               // note / pitch
                            A_out <= pitch_r  <<< SHIFT_UP;
                            B_out <= note_sel <<< SHIFT_UP;
                        end
                        default: begin
                            A_out <= '0;
                            B_out <= '0;
                        end
                    endcase
                    state <= S_CORDIC_WAIT;
                end

                // -------------------------------------------------------------
                S_CORDIC_WAIT: begin
                    if (cordic_fall) begin
                        unique case (cd_purpose)
                            CD_RATIO_LO: begin
                                ratio_lo   <= ans;
                                cd_purpose <= CD_RATIO_HI;
                                state      <= S_CORDIC_ISSUE;
                            end
                            CD_RATIO_HI: begin
                                // pitch/f_lo <= f_hi/pitch  ->  closer to lo
                                note_sel   <= (ratio_lo <= ans) ? f_lo : f_hi;
                                cd_purpose <= CD_SHIFT;
                                state      <= S_CORDIC_ISSUE;
                            end
                            CD_SHIFT: begin
                                shift <= ans;
                                state <= S_IDLE;
                            end
                            default: state <= S_IDLE;
                        endcase
                    end
                end

                // -------------------------------------------------------------
                S_DONE: state <= S_IDLE;

                default: state <= S_IDLE;
            endcase
        end
    end

endmodule
