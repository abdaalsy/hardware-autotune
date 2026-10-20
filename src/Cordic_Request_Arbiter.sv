`timescale 1ns / 1ps
`default_nettype none
// Multiplexes three existing edge/pulse-based CORDIC clients onto one engine.
// Records one outstanding pulse/operand tuple per client while engine is busy.
// Priority: index 0 > 1 > 2.  Busy goes high for the selected client when
// dispatched and falls only after the CORDIC result has been captured.
module Cordic_Request_Arbiter #(
    parameter int BIT_WIDTH = 24,
    parameter int CLIENTS = 3
) (
    input logic clk,
    input logic rst_n,
    input logic [CLIENTS-1:0] client_start,
    input logic [CLIENTS-1:0] client_opcode,
    input logic signed [CLIENTS-1:0][BIT_WIDTH-1:0] client_A,
    input logic signed [CLIENTS-1:0][BIT_WIDTH-1:0] client_B,
    output logic [CLIENTS-1:0] client_busy,
    output logic signed [CLIENTS-1:0][BIT_WIDTH-1:0] client_answer,
    output logic cordic_start,
    output logic cordic_opcode,
    output logic signed [BIT_WIDTH-1:0] cordic_A,
    output logic signed [BIT_WIDTH-1:0] cordic_B,
    input logic cordic_busy,
    input logic signed [BIT_WIDTH:0] cordic_answer
);
    localparam int SW = (CLIENTS > 1) ? $clog2(CLIENTS) : 1;
    typedef enum logic [1:0] {IDLE, ISSUE, WAIT_BUSY, WAIT_DONE} state_t;
    state_t state;
    logic [CLIENTS-1:0] pending;
    logic [CLIENTS-1:0] prior_start;
    logic [CLIENTS-1:0] op_pending;
    logic signed [CLIENTS-1:0][BIT_WIDTH-1:0] a_pending,b_pending;
    logic [SW-1:0] sel;
    logic [SW-1:0] first_idx;
    logic have_pending;
    integer i;

    always_comb begin
        have_pending = 1'b0;
        first_idx = '0;
        for (int j=0; j<CLIENTS; j=j+1) begin
            if (!have_pending && pending[j]) begin
                have_pending = 1'b1;
                first_idx = SW'(j);
            end
        end
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= IDLE;
            pending <= '0;
            prior_start <= '0;
            client_busy <= '0;
            client_answer <= '0;
            op_pending <= '0;
            a_pending <= '0;
            b_pending <= '0;
            sel <= '0;
            cordic_start <= 1'b0;
            cordic_opcode <= 1'b0;
            cordic_A <= '0;
            cordic_B <= '0;
        end else begin
            prior_start <= client_start;
            cordic_start <= 1'b0;
            for (i=0; i<CLIENTS; i=i+1) begin
                if (client_start[i] && !prior_start[i]) begin
                    pending[i] <= 1'b1;
                    a_pending[i] <= client_A[i];
                    b_pending[i] <= client_B[i];
                    op_pending[i] <= client_opcode[i];
                end
            end
            case (state)
                IDLE: if (have_pending) begin
                    sel <= first_idx;
                    pending[first_idx] <= 1'b0;
                    cordic_A <= a_pending[first_idx];
                    cordic_B <= b_pending[first_idx];
                    cordic_opcode <= op_pending[first_idx];
                    client_busy[first_idx] <= 1'b1;
                    state <= ISSUE;
                end
                ISSUE: begin
                    cordic_start <= 1'b1;
                    state <= WAIT_BUSY;
                end
                WAIT_BUSY: if (cordic_busy) state <= WAIT_DONE;
                WAIT_DONE: if (!cordic_busy) begin
                    client_answer[sel] <= cordic_answer[BIT_WIDTH-1:0];
                    client_busy[sel] <= 1'b0;
                    state <= IDLE;
                end
                default: state <= IDLE;
            endcase
        end
    end
endmodule
`default_nettype wire
