`default_nettype none

module Cordic #(
    parameter int BIT_WIDTH = 24,
    parameter int ONES_BIT  = BIT_WIDTH/2
) (
    input  logic                    clk,
    input  logic                    rst_n,
    input  logic                    start,
    input  logic                    opcode, // 0: Division (Circular), 1: Square Root (Hyperbolic)
    // Division: computes B/A 
    // Square root: computes sqrt(A)
    input  logic [BIT_WIDTH-1:0]    A_in,
    input  logic [BIT_WIDTH-1:0]    B_in,
    output logic [BIT_WIDTH:0]      ans,
    output logic                    busy,

    // Memory interface 
    output logic                    start_read,
    output logic                    start_write,
    output logic [BIT_WIDTH-1:0]    address,
    output logic [BIT_WIDTH-1:0]    val_out,
    input  logic [BIT_WIDTH-1:0]    val_in,
    input  logic                    res
);

    // Local parameters for LUT starting addresses
    localparam int ARCTANH_LUT_ADDR = 0; // Offset separating arctan and arctanh tables in memory

    // FSM States
    typedef enum logic [2:0] {
        IDLE,
        NORMALIZE,
        FETCH_LUT,
        DO_ITERATION,
        POST_REVERT,
        DESCALE
    } state_t;

    state_t state, next_state;

    // Internal datapath registers (extra bits for growth/overflow prevention)
    logic signed [BIT_WIDTH+3:0] x_reg, y_reg, z_reg, i_reg;
    logic [11:0]                 iter_cnt;
    logic                        op_reg;
    logic [3:0]                  norm_shift;
    logic signed [BIT_WIDTH+3:0] lut_val_reg;

    // FSM Sequential Logic & Datapath Execution
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
            lut_val_reg <= '0;
            start_read  <= 1'b0;
            start_write <= 1'b0;
            address     <= '0;
            val_out     <= '0;
        end else begin
            state <= next_state;

            // Default control values for memory interface
            start_read  <= 1'b0;
            start_write <= 1'b0;

            case (state)
                IDLE: begin
                    if (start) begin
                        op_reg     <= opcode;
                        x_reg      <= A_in;
                        y_reg      <= B_in;
                        z_reg      <= '0;
                        i_reg      <= '0;
                        iter_cnt   <= '0;
                        norm_shift <= '0;
                    end
                end

                NORMALIZE: begin
                    // Input normalization logic
                    if (op_reg) begin
                        
                    end
                end

                FETCH_LUT: begin
                    // Drive memory interface read request
                    start_read <= 1'b1;
                    address    <= ARCTANH_LUT_ADDR + (i_reg <<< 2); // only LUT is arctanh
                    
                    if (res) begin
                        // Capture LUT value from memory response and sign-extend
                        // necessary bc val_in is 24 bits wide while cordic
                        // operands are slightly wider to prevent overflow
                        lut_val_reg <= {{(4){val_in[BIT_WIDTH-1]}}, val_in};
                        start_read  <= 1'b0;
                    end
                end

                DO_ITERATION: begin
                    // Execute CORDIC iteration using fetched LUT constant
                    if (op_reg == 1'b0) begin
                        // Circular CORDIC (Division / Vectoring mode)
                        if (y_reg[BIT_WIDTH+3] == 1'b1) begin // y < 0
                            x_reg <= x_reg;
                            y_reg <= y_reg + (x_reg >>> i_reg);
                            z_reg <= z_reg - (z_reg >>> i_reg);
                        end else begin                       // y >= 0
                            x_reg <= x_reg;
                            y_reg <= y_reg - (x_reg >>> i_reg);
                            z_reg <= z_reg + (z_reg >>> i_reg);
                        end
                        i_reg <= i_reg + 1;
                    end else begin
                        // Hyperbolic CORDIC (Square Root mode)
                        if (y_reg[BIT_WIDTH+3] == 1'b1) begin
                            x_reg <= x_reg + (y_reg >>> i_reg);
                            y_reg <= y_reg + (x_reg >>> i_reg);
                            z_reg <= z_reg - lut_val_reg;
                        end else begin
                            x_reg <= x_reg - (y_reg >>> i_reg);
                            y_reg <= y_reg - (x_reg >>> i_reg);
                            z_reg <= z_reg + lut_val_reg;
                        end
                        // in hyperbolic cordic, certain indexes must be repeated
                        // dont increment on repeated indexes (i = 4, 13)
                        if (iter_cnt != 12'd4 && iter_cnt != 12'd14) i_reg <= i_reg + 1;
                    end
                    iter_cnt <= iter_cnt + 1;
                end

                POST_REVERT: begin
                    // Unnormalizing output
                end

                DESCALE: begin
                    // CORDIC-gain removal
                    // Assign ans to x_reg for sqrt, z_reg for div
                end

                default: begin
                    // do nothing
                end
            endcase
        end
    end

    // FSM Combinational Next-State Logic
    always_comb begin
        next_state = state;
        case (state)
            IDLE:         if (start) next_state = NORMALIZE;
            NORMALIZE:    next_state = FETCH_LUT;
            FETCH_LUT:    if (res) next_state = DO_ITERATION;
            DO_ITERATION: begin
                if (i_reg[5:0] == BIT_WIDTH[5:0]) begin
                    next_state = POST_REVERT;
                end else begin
                    if (op_reg) begin // doing sqrt
                        next_state = FETCH_LUT;    // sqrt requires LUT
                    end else begin    // doing division
                        next_state = DO_ITERATION; // division does not need LUT
                    end
                end
            end
            POST_REVERT: next_state = DESCALE;
            DESCALE: next_state = IDLE;
            default: next_state = IDLE;
        endcase
    end

    // Output assignments
    assign busy = (state != IDLE);

endmodule
