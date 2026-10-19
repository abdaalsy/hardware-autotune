// Note matcher
// Takes in a pitch in Hz and binary searches an array of note frequencies for the closest one
// When there's two choices left, determine which one is closer on a logarithmic scale.
// Drives shift with answer to: note frequency divided by user's pitch
//
// Flow:
// 1. Wait for a start pulse
// 2. Once received, transition to a state where we decide the endpoints
//      - if we were just in idle, set endpoints to 0 and num_notes-1
//      - otherwise, check the greater than or less than flags to know
// 3. After deciding on endpoints, transition to a state where we read the midpoint's value from memory
// 4. Once received, set the greater than/less than flags by comparing the pitch to the midpoint value
//      - If endpoints are 1 apart, then determine if its closer to the lower or upper endpoint value on a logarithmic scale
//      - If endpoitns are equal, skip this and immediately transition to the FINISH state.
// 5. After 1 cycle (or when the cordic engine has completed), loop back to the endpoints stage (or transition to FINISH otherwise)
//
// In the FINAL_READ state we read the note at final_index and divide it by pitch to get the output, shift.

`default_nettype none 

module Match_Note #(
    parameter int BIT_WIDTH = 24,
) (
    input logic rst_n,
    input logic clk,
    input logic [BIT_WIDTH-1:0] num_notes,
    input logic [BIT_WIDTH-1:0] pitch,
    input logic start,
    output logic [BIT_WIDTH-1:0] shift,
    output logic busy,

    // Memory interface
    output logic                        start_read,
    output logic                        start_write, // unused
    output logic [BIT_WIDTH-1:0]        address,
    output logic [BIT_WIDTH-1:0]        val_out,
    input  logic [BIT_WIDTH-1:0]        val_in,
    input  logic                        res,

    // CORDIC engine interface.  Division (opcode=0) performs B/A, Square root (opcode=1) performs sqrt(A)
    output logic                        cordic_start,
    output logic                        opcode,
    output logic signed [BIT_WIDTH-1:0] A_out,
    output logic signed [BIT_WIDTH-1:0] B_out,
    input  logic signed [BIT_WIDTH-1:0] ans,
    input  logic                        cordic_busy
);

localparam logic [BIT_WIDTH-1:0] notes_addr_base = ;

typedef enum logic [2:0] {
    IDLE,
    ENDPOINTS,
    READ,
    COMPARE,
    FINAL_READ,
    FINISH,
} state_t;
state_t prev_state, current_state, next_state;

logic [BIT_WIDTH-1:0] start, end, mid_val, final_index;

always_comb begin
    next_state = current_state;
    case (current_state)
        IDLE: if (start) next_state = ENDPOINTS;
        ENDPOINTS: next_state = READ;
        READ: if (res_posedge) next_state = COMPARE;
        COMPARE: begin
            if (start + 1'b1 == end) begin
                if (cordic_busy_negedge) next_state = FINAL_READ;
            end else begin
                next_state = ENDPOINTS;
            end
        end 
        FINAL_READ: if (res_posedge) next_state = FINISH;
        FINISH: next_state = IDLE;
        default: next_state = IDLE;
    endcase
end



endmodule
