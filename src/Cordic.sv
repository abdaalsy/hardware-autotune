`timescale 1ns / 1ps
`default_nettype none

module Cordic #(
    parameter int BIT_WIDTH = 24,
    parameter int ONES_BIT  = BIT_WIDTH/2   // number of fractional bits at the I/O (1.0 == 1 << ONES_BIT)
) (
    input  logic                    clk,
    input  logic                    rst_n,
    input  logic                    start,
    input  logic                    opcode, // 0: Division (Linear vectoring), 1: Square Root (Hyperbolic)
    // Division: computes B/A
    // Square root: computes sqrt(A)
    input  logic signed [BIT_WIDTH-1:0]    A_in,
    input  logic signed [BIT_WIDTH-1:0]    B_in,
    output logic signed [BIT_WIDTH:0]      ans,
    output logic                    busy,

    // Memory interface
    output logic                    start_read,
    output logic                    start_write,
    output logic [BIT_WIDTH-1:0]    address,
    output logic [BIT_WIDTH-1:0]    val_out,
    input  logic [BIT_WIDTH-1:0]    val_in,
    input  logic                    res
);

    // ------------------------------------------------------------------
    // Constants
    // ------------------------------------------------------------------
    // Guard bits: the datapath runs in Q(ONES_BIT+GUARD). Truncation error in
    // the shift-and-add loop is then ~2^-GUARD of an output ulp per step
    // instead of a full ulp, and the final rounding removes the bias.
    localparam int GUARD  = 4;
    localparam int FRAC   = ONES_BIT + GUARD;     // internal fractional bits
    localparam int LAST_I = FRAC;                 // later iterations add < 1 internal ulp

    localparam int W      = BIT_WIDTH + 4 + GUARD;  // datapath width
    localparam int WIDE_W = 2*BIT_WIDTH + 8 + GUARD; // headroom for shifts / multiply

    localparam logic signed [W-1:0] ONE     = W'(1) <<< FRAC;   // 1.0 (internal format)
    localparam logic signed [W-1:0] QUARTER = ONE >>> 2;

    // 1/A_h = 1.2074970677..., as a Q24 integer
    localparam int INV_AH_FRAC = 24;
    localparam logic signed [WIDE_W-1:0] INV_AH = 20258439;

    // Rounding offsets for dropping GUARD bits
    localparam logic signed [WIDE_W-1:0] HALF   = WIDE_W'(1) <<< (GUARD - 1);
    localparam logic signed [WIDE_W-1:0] SQ_RND = WIDE_W'(1) <<< (INV_AH_FRAC + GUARD - 1);

    // Output saturation limits (ans is BIT_WIDTH+1 bits, signed)
    localparam logic signed [WIDE_W-1:0] ANS_MAX =   (WIDE_W'(1) <<< BIT_WIDTH) - 1;
    localparam logic signed [WIDE_W-1:0] ANS_MIN = -(WIDE_W'(1) <<< BIT_WIDTH);

    // ------------------------------------------------------------------
    // Helper functions
    // ------------------------------------------------------------------
    // Index of the most significant set bit, -1 if v == 0
    function automatic int msb_idx(input logic [W-1:0] v);
        msb_idx = -1;
        for (int b = 0; b < W; b++) begin
            if (v[b]) msb_idx = b;
        end
    endfunction

    function automatic logic signed [BIT_WIDTH:0] sat_ans(input logic signed [WIDE_W-1:0] v);
        if (v > ANS_MAX)      sat_ans = ANS_MAX[BIT_WIDTH:0];
        else if (v < ANS_MIN) sat_ans = ANS_MIN[BIT_WIDTH:0];
        else                  sat_ans = v[BIT_WIDTH:0];
    endfunction

    // ------------------------------------------------------------------
    // FSM States
    // ------------------------------------------------------------------
    typedef enum logic [2:0] {
        IDLE,
        NORMALIZE,
        DO_ITERATION,
        POST_REVERT,
        DESCALE
    } state_t;

    state_t state, next_state;

    // ------------------------------------------------------------------
    // Registers
    // ------------------------------------------------------------------
    logic signed [W-1:0] x_reg, y_reg, z_reg, i_reg;
    logic [11:0]         iter_cnt;
    logic                op_reg;
    logic signed [5:0]   norm_shift;   // k (signed: sqrt can need a negative k)

    logic                          special;      // bypass: div-by-zero / sqrt(<=0)
    logic signed [BIT_WIDTH:0]      special_val;

    logic prev_start, start_posedge;
    assign start_posedge = (prev_start == 1'b0 && start == 1'b1);

    // ------------------------------------------------------------------
    // Combinational normalization helpers (evaluated from x_reg / y_reg
    // while in NORMALIZE, which hold A_in / B_in in Q(ONES_BIT))
    // ------------------------------------------------------------------
    logic                norm_special;
    // division
    logic                a_neg;
    logic signed [W-1:0] xn, yn, x_div, y_div;
    logic        [W-1:0] xmag, ymag;
    int                  msb_x, msb_y, k_div, a_sh, y_sh;
    // square root
    int                  p_sq, d_sq, s_sq, k_sq;
    logic signed [W-1:0] x_g, w_norm;

    assign norm_special = op_reg ? (x_reg[W-1] || (x_reg == '0))   // sqrt(w), w <= 0
                                 : (x_reg == '0);                  // B / 0

    always_comb begin
        // ---- Division ----
        // 1) make x > 0 (B/A == (-B)/(-A))
        // 2) scale x AND y by 2^a so x lands in [1,2) internal format: this keeps
        //    (x >>> i) nonzero for every iteration and keeps y at full precision
        // 3) additionally shift y right by k so |y'| < |x'|; undo k afterwards
        a_neg = x_reg[W-1];
        xn    = a_neg ? -x_reg : x_reg;
        yn    = a_neg ? -y_reg : y_reg;
        xmag  = xn;
        ymag  = yn[W-1] ? -yn : yn;
        msb_x = msb_idx(xmag);
        msb_y = msb_idx(ymag);
        k_div = (msb_y >= msb_x) ? (msb_y - msb_x + 1) : 0;
        a_sh  = FRAC - msb_x;
        y_sh  = a_sh - k_div;
        x_div = (a_sh >= 0) ? (xn <<< a_sh) : (xn >>> (-a_sh));
        y_div = (y_sh >= 0) ? (yn <<< y_sh) : (yn >>> (-y_sh));

        // ---- Sqrt: shift by an even amount 2k so w' lands in [0.5, 2) ----
        x_g    = x_reg <<< GUARD;                   // promote input to internal format
        p_sq   = msb_idx(xmag);                     // MSB of the Q(ONES_BIT) input
        d_sq   = p_sq - (ONES_BIT - 1);
        s_sq   = d_sq[0] ? (d_sq - 1) : d_sq;       // force even
        k_sq   = s_sq >>> 1;
        w_norm = (s_sq >= 0) ? (x_g >>> s_sq) : (x_g <<< (-s_sq));
    end

    // Post-reversion shift (left by k; right if k is negative), internal format
    logic signed [WIDE_W-1:0] rev_val;
    always_comb begin
        rev_val = op_reg ? WIDE_W'(x_reg) : WIDE_W'(z_reg);
        if (norm_shift >= 0) rev_val = rev_val <<< norm_shift;
        else                 rev_val = rev_val >>> (-norm_shift);
    end

    // ------------------------------------------------------------------
    // FSM Sequential Logic & Datapath Execution
    // ------------------------------------------------------------------
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state       <= IDLE;
            x_reg       <= '0;
            y_reg       <= '0;
            z_reg       <= '0;
            i_reg       <= '0;
            iter_cnt    <= '0;
            op_reg      <= '0;
            norm_shift  <= '0;
            special     <= 1'b0;
            special_val <= '0;
            ans         <= '0;
            prev_start  <= '0;
        end else begin
            state <= next_state;
            prev_start <= start;
            case (state)
                IDLE: begin
                    if (start_posedge) begin
                        op_reg     <= opcode;
                        x_reg      <= A_in;
                        y_reg      <= B_in;
                        z_reg      <= '0;
                        i_reg      <= '0;
                        iter_cnt   <= '0;
                        norm_shift <= '0;
                        special    <= 1'b0;
                    end
                end

                NORMALIZE: begin
                    if (norm_special) begin
                        // Skip the CORDIC loop entirely; DESCALE emits special_val
                        special <= 1'b1;
                        if (op_reg || y_reg == '0) special_val <= '0;
                        else special_val <= y_reg[W-1] ? ANS_MIN[BIT_WIDTH:0]
                                                       : ANS_MAX[BIT_WIDTH:0];
                    end else if (op_reg) begin
                        // sqrt: x0 = w' + 0.25, y0 = w' - 0.25, i starts at 1
                        x_reg      <= w_norm + QUARTER;
                        y_reg      <= w_norm - QUARTER;
                        norm_shift <= k_sq[5:0];
                        i_reg      <= 1;
                    end else begin
                        // div: x0 in [1,2), y0 scaled to match, i starts at 0
                        x_reg      <= x_div;
                        y_reg      <= y_div;
                        norm_shift <= k_div[5:0];
                        i_reg      <= '0;
                    end
                end

                DO_ITERATION: begin
                    if (op_reg == 1'b0) begin
                        // Linear vectoring (division): z accumulates sigma * 2^-i
                        if (y_reg[W-1] == 1'b1) begin // y < 0
                            y_reg <= y_reg + (x_reg >>> i_reg);
                            z_reg <= z_reg - (ONE   >>> i_reg);
                        end else begin                // y >= 0
                            y_reg <= y_reg - (x_reg >>> i_reg);
                            z_reg <= z_reg + (ONE   >>> i_reg);
                        end
                        i_reg <= i_reg + 1;
                    end else begin
                        // Hyperbolic vectoring (square root)
                        // (z = atanh(y/x) is not needed for sqrt, so no LUT)
                        if (y_reg[W-1] == 1'b1) begin
                            x_reg <= x_reg + (y_reg >>> i_reg);
                            y_reg <= y_reg + (x_reg >>> i_reg);
                        end else begin
                            x_reg <= x_reg - (y_reg >>> i_reg);
                            y_reg <= y_reg - (x_reg >>> i_reg);
                        end
                        // Repeat i = 4 and i = 13: hold i on the FIRST occurrence.
                        // Step j=3 is the first i=4, step j=13 is the first i=13.
                        if (iter_cnt != 12'd3 && iter_cnt != 12'd13) i_reg <= i_reg + 1;
                    end
                    iter_cnt <= iter_cnt + 1;
                end

                POST_REVERT: begin
                    // Undo pre-normalization: shift by k
                    if (op_reg) x_reg <= rev_val[W-1:0];   // sqrt: x_N * 2^k (internal format)
                    else        z_reg <= W'(sat_ans((rev_val + HALF) >>> GUARD)); // div: round to Q(ONES_BIT)
                end

                DESCALE: begin
                    if (special) begin
                        ans <= special_val;
                    end else if (op_reg) begin
                        // sqrt(w) = x_rev / A_h ; one rounding for gain + guard removal
                        ans <= sat_ans((WIDE_W'(x_reg) * INV_AH + SQ_RND) >>> (INV_AH_FRAC + GUARD));
                    end else begin
                        // linear mode has unity gain (already rounded in POST_REVERT)
                        ans <= z_reg[BIT_WIDTH:0];
                    end
                end

                default: begin
                end
            endcase
        end
    end

    // ------------------------------------------------------------------
    // FSM Combinational Next-State Logic
    // ------------------------------------------------------------------
    always_comb begin
        next_state = state;
        case (state)
            IDLE: if (start_posedge) next_state = NORMALIZE;
            NORMALIZE: begin
                if (norm_special)  next_state = DESCALE;
                else               next_state = DO_ITERATION;
            end
            DO_ITERATION: begin
                if (i_reg[5:0] == LAST_I[5:0]) next_state = POST_REVERT;
            end
            POST_REVERT: next_state = DESCALE;
            DESCALE:     next_state = IDLE;
            default:     next_state = IDLE;
        endcase
    end

    assign busy = (state != IDLE);

    // Memory interface is unused (no LUT needed); ports kept for compatibility.
    // val_in and res are intentionally unused.
    assign start_read  = 1'b0;
    assign start_write = 1'b0;
    assign address     = '0;
    assign val_out     = '0;

endmodule
