`default_nettype none

// Butterworth bandpass filter.
// With a lower cutoff of 50 Hz, and upper cutoff of 800 Hz, this filters non-vocal frequencies out of our audio to improve pitch detection
// Equation: $$y[n] = \sum_{i=0}^{N} b_i x[n-i] - \sum_{j=1}^{N} a_j y[n-j]$$
// b_i = [ 0.00225158  0.         -0.00450317  0.          0.00225158]
// a_j = [ 1.         -3.86003689  5.59053061 -3.60086076  0.8703]
//
// Since doing constant multiplication, no multiplier needed
// Need to save past 4 inputs, past 4 outputs

module Bandpass_Filter #(
    parameter int BIT_WIDTH = 24,
    parameter int FRACTIONAL_BITS = BIT_WIDTH/2,
    parameter int FRAME_LEN = 1920
) (
    input logic rst_n,
    input logic clk,
    input logic start,
    input logic [BIT_WIDTH-1:0] frame_addr_base,
    input logic signed [BIT_WIDTH-1:0] sample_in,
    output logic busy,

    // Memory interface 
    output logic                        start_read,
    output logic                        start_write,
    output logic [BIT_WIDTH-1:0]        address,
    output logic [BIT_WIDTH-1:0]        val_out,
    input  logic [BIT_WIDTH-1:0]        val_in,
    input  logic                        res
);

localparam real SCALE_FACTOR = 1.0 << FRACTIONAL_BITS; // 4096
// coefficients on inputs
localparam signed [BIT_WIDTH-1:0] b0 = signed'(0.00225158 * SCALE_FACTOR);
localparam signed [BIT_WIDTH-1:0] b2 = signed'(-0.00450317 * SCALE_FACTOR);
localparam signed [BIT_WIDTH-1:0] b4 = signed'(0.00225158 * SCALE_FACTOR);
// coefficients on outputs
localparam signed [BIT_WIDTH-1:0] a1 = signed'(-3.86003689 * SCALE_FACTOR);
localparam signed [BIT_WIDTH-1:0] a2 = signed'(5.59053061 * SCALE_FACTOR);
localparam signed [BIT_WIDTH-1:0] a3 = signed'(-3.60086076 * SCALE_FACTOR);
localparam signed [BIT_WIDTH-1:0] a4 = signed'(0.8703 * SCALE_FACTOR);


typedef enum logic [2:0] {
    IDLE,
    COMPUTE_TERMS,
    SUM,
    WRITE,
    SHIFT
} state_t;

state_t current_state, next_state;

logic [1:0] res_sreg;
logic res_posedge;
logic [$clog2(FRAME_LEN)-1:0] sample_num;
logic [3:0][BIT_WIDTH - 1] past_inputs, past_outputs; // recent (MSB) -> older (LSB)
logic signed [BIT_WIDTH-1:0] current_output;
logic signed [BIT_WIDTH-1:0] term_b0, term_b2, term_b4, term_a1, term_a2, term_a3, term_a4;

assign res_posedge = (res_sreg[1] == 1'b0 && res_sreg[0] == 1'b1);

always_comb begin
    next_state = current_state;
    case (current_state)
        IDLE: if (start) next_state = WRITE;
        COMPUTE_TERMS: next_state = SUM;
        SUM: next_state = WRITE,
        WRITE: if (res_posedge) next_state = SHIFT;
        SHIFT: next_state = IDLE;
        default: next_state = IDLE;
    endcase
end

always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        current_state <= IDLE;
        busy <= 1'b1;
        sample_out <= '0;
        past_inputs <= '0;
        past_outputs <= '0;
        res_sreg <= '0;
        sample_num <= '0;
    end else begin
        current_state <= next_state;
        res_sreg <= {res_sreg[0], res};
        case (current_state)
            IDLE: begin
                busy <= 1'b0;
            end
            COMPUTE_TERMS: begin
                busy <= 1'b1;
                term_b0 <= b0*sample_in;
                term_b2 <= b2*past_inputs[1];
                term_b4 <= b4*past_inputs[3];
                term_a1 <= a1*past_outputs[0];
                term_a2 <= a2*past_outputs[1];
                term_a3 <= a3*past_outputs[2];
                term_a4 <= a4*past_outputs[3];
            end
            SUM: begin
                busy <= 1'b1;
                current_output <= term_b0+term_b2+term_b4-term_a1-term_a2-term_a3-term_a4;
            end
            WRITE: begin
                busy <= 1'b1;
                start_write <= 1'b1;
                address <= frame_addr_base + (sample_num << 2);
                val_out <= current_output;
                sample_num <= sample_num + 1'b1;
                if (res_posedge) start_write <= 1'b0;
            end
            SHIFT: begin
                busy <= 1'b1;
                past_inputs <= {sample_in, past_inputs[3:1]};
                past_outputs <= {current_output, past_outputs[3:1]};
            end
            default: begin
                // do nothing
            end
        endcase
    end
end



endmodule
