`default_nettype none 

// Resampler needs to do the following things in a loop:
// 1. Step the write head
//      i. Retrieve the current sample from address input_buffer_base + write_pos
//      ii. Write it to the circular buffer at address circular_buffer_base
//      + write_pos
//      iii. Increment the write_pos and mod it by the length of the circular
//      buffer
//      iv. Repeat until BLOCK_SIZE samples have been written
//
// 2. Step the read head
//      i. Retrieve the current sample from address circular_buffer_base
//      + read_pos
//      ii. Write it to address output_buffer_base + read_pos
//      iii. increment read_pos
//      iv. add shift to real_read_pos
//      v. Check if difference between read_pos and real_read_pos >= 1
//              If shift > 1, increment read_pos
//              If shift < 1, decrement read_pos
//      vi. mod both read_pos and real_read_pos by circular buffer length
//      vii. Check if read_pos is an increment away from overtaking write_pos
//              If so, subtract period_samples from both read_pos and
//              real_read_pos
//      viii. Repeat until BLOCK_SIZE samples have been read


// Assume pos values are in fixed point

module Resampler #(
    parameter int BIT_WIDTH = 24,
    parameter int ONES_BIT = BIT_WIDTH/2,
    parameter int BLOCK_SIZE = 256,
    parameter int SAMPLE_RATE = 48000,
    parameter int INPUT_BUFFER_BASE = 'h10,
    parameter int CIRCULAR_BUFFER_BASE = 'h810,
    parameter int OUTPUT_BUFFER_BASE = 'h410,
    parameter int CIRCULAR_BUFFER_LEN = 'd4096    // must be equal to 2^(BIT_WIDTH/2)
) (
    input logic clk,
    input logic rst_n,
    input logic start,
    input logic [BIT_WIDTH-1:0] shift,
    input logic [BIT_WIDTH-1:0] real_read_pos_in,
    input logic [BIT_WIDTH-1:0] write_pos_in,
    input logic [BIT_WIDTH/2-1:0] period_samples,
    input logic [BIT_WIDTH-1:0] read_pos_in,
    output logic [BIT_WIDTH-1:0] real_read_pos_out,
    output logic [BIT_WIDTH-1:0] read_pos_out,
    output logic [BIT_WIDTH-1:0] write_pos_out,
    output logic busy,

    // Memory interface
    output logic [23:0] address_A,
    output logic [BIT_WIDTH-1:0] val_A,
    output logic start_read_A,
    output logic start_write_A,
    input logic res_A,
    input logic [BIT_WIDTH-1:0] value_in
);

localparam int INT_WIDTH = BIT_WIDTH - ONES_BIT; // whole portion width

typedef enum logic[3:0] {
    IDLE,
    READ_INPUT,
    WRITE_CIRCULAR,
    INCREMENT_WRITE_POS,
    FIX_UNDERRUN,
    READ_CIRCULAR,
    WRITE_OUTPUT,
    INCREMENT_READ_POS,
    FIX_POS_DELTA,
    FIX_OVERRUN
} state_t;

state_t current_state, next_state;

logic [BIT_WIDTH-1:0] write_pos, read_pos, real_read_pos, current_sample;
logic [$clog2(BLOCK_SIZE)+1:0] num_samples_write, num_samples_read;
logic [1:0] start_sreg, resA_sreg;
logic start_posedge, resA_posedge;
logic underrun, overrun; // signals that'll indicate under/overruns
logic pitching_up, pitching_down, pos_delta;

assign start_posedge = (start_sreg[1] == 1'b0 && start_sreg[0] == 1'b1);
assign resA_posedge = (resA_sreg[1] == 1'b0 && resA_sreg[0] == 1'b1);

assign pitching_up = (shift[BIT_WIDTH-1:ONES_BIT] != 12'b0); // if the whole portion is non zero, then we are definitely pitching up
assign pitching_down = (shift[BIT_WIDTH-1:ONES_BIT] == 12'b0); // if the whole portion is 0, then we are definitely pitching down
assign pos_delta = (read_pos[BIT_WIDTH-1:ONES_BIT] != real_read_pos[BIT_WIDTH-1:ONES_BIT]); // if the whole portions match, then the diference is guaranteed less than 1

// Extract just the integer portions (ignoring the 12 fractional bits)
wire [INT_WIDTH-1:0] read_int  = read_pos[BIT_WIDTH-1:ONES_BIT];
wire [INT_WIDTH-1:0] write_int = write_pos[BIT_WIDTH-1:ONES_BIT];

// Calculate distances using unsigned subtraction.
// Because these are fixed-width unsigned signals, the math wraps perfectly.
// diff_w_minus_r = How much data is available to read
// diff_r_minus_w = How much space is available to write
wire [INT_WIDTH-1:0] diff_w_minus_r = write_int - read_int;
wire [INT_WIDTH-1:0] diff_r_minus_w = read_int  - write_int;

// Evaluate the conditions
assign overrun  = (diff_w_minus_r < 2);
assign underrun = (diff_r_minus_w < BLOCK_SIZE);

always_comb begin
    next_state = current_state;
    case (current_state) 
        IDLE: if (start_posedge) next_state = READ_INPUT;
        READ_INPUT: if (resA_posedge) next_state = WRITE_CIRCULAR;
        WRITE_CIRCULAR: if (resA_posedge) next_state = INCREMENT_WRITE_POS;
        INCREMENT_WRITE_POS: begin
            if (num_samples_write == BLOCK_SIZE) begin 
                // stop when we've written all BLOCK_SIZE samples to the circular buffer
                next_state = FIX_UNDERRUN;
            end else begin
                next_state = READ_INPUT;
            end
        end
        FIX_UNDERRUN: begin
            if (!underrun) begin
                next_state = READ_CIRCULAR;
            end
        end
        READ_CIRCULAR: begin
            if (resA_posedge) next_state = WRITE_OUTPUT;
        end
        WRITE_OUTPUT: begin
            if (resA_posedge) next_state = INCREMENT_READ_POS;
        end
        INCREMENT_READ_POS: next_state = FIX_POS_DELTA;
        FIX_POS_DELTA: begin
            if (!pos_delta) begin
                next_state = FIX_OVERRUN;
            end
        end
        FIX_OVERRUN: begin
            if (!overrun) begin
                if (num_samples_read == BLOCK_SIZE) begin
                    next_state = IDLE;
                end else begin
                    next_state = READ_CIRCULAR;
                end
            end
        end
        default: next_state = IDLE;
    endcase
end

always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        current_state <= IDLE;
        real_read_pos_out <= '0;
        write_pos_out <= '0;
        read_pos_out <= '0;
        busy <= 1'b1;
        address_A <= '0;
        val_A <= '0;
        start_read_A <= 1'b0;
        start_write_A <= 1'b0;
        write_pos <= '0;
        read_pos <= '0;
        real_read_pos <= '0;
        num_samples_write <= '0;
        current_sample <= '0;
    end else begin
        current_state <= next_state;
        start_sreg <= {start_sreg[0], start};
        resA_sreg <= {resA_sreg[0], res_A};

        case (current_state)
            IDLE: begin
                // don't set pos outputs so that they're available for longer
                busy <= 1'b0;
                address_A <= '0;
                val_A <= '0;
                start_read_A <= 1'b0;
                start_write_A <= 1'b0;
                write_pos <= write_pos_in;
                read_pos <= read_pos_in;
                real_read_pos <= real_read_pos_in;
                num_samples_write <= '0;
                num_samples_read <= '0;
                current_sample <= '0;
            end
            READ_INPUT: begin
                // set address of input buffer base + write_pos offset
                address_A <= INPUT_BUFFER_BASE + num_samples_write;
                start_read_A <= 1'b1;
                start_write_A <= 1'b0;
                val_A <= '0;
                if (res_A) begin
                    current_sample <= value_in; // This will receive the value from the memory controller
                    start_read_A <= 1'b0;
                end
                busy <= 1'b1;
            end
            WRITE_CIRCULAR: begin 
                address_A <= CIRCULAR_BUFFER_BASE + {12'b0, write_pos[BIT_WIDTH-1:ONES_BIT]};
                val_A <= current_sample;
                start_read_A <= 1'b0;
                start_write_A <= 1'b1;
                busy <= 1'b1;
                if (res_A) start_write_A <= 1'b0;
            end
            INCREMENT_WRITE_POS: begin
                busy <= 1'b1;
                address_A <= '0;
                val_A <= '0;
                start_read_A <= 1'b0;
                start_write_A <= 1'b0;
                current_sample <= '0;
                if (num_samples_write != BLOCK_SIZE) begin
                    write_pos <= write_pos + 13'h1000;  // the lower 12 bits correspond to the fractional portion
                    // the circular buffer has length 4096 which automatically wraps with 24 bit width fixed point
                    num_samples_write <= num_samples_write + 1'b1;
                end else begin
                    write_pos_out <= write_pos;
                    num_samples_write <= '0;
                end
            end
            FIX_UNDERRUN: begin
                busy <= 1'b1;
                if (underrun) begin
                    read_pos <= read_pos + {period_samples, {ONES_BIT{1'b0}}};
                    real_read_pos <= real_read_pos + {period_samples, {ONES_BIT{1'b0}}};
                end
            end
            READ_CIRCULAR: begin
                // read the circular buffer for a sample
                busy <= 1'b1;
                address_A <= CIRCULAR_BUFFER_BASE + {12'b0, read_pos[BIT_WIDTH-1:ONES_BIT]};
                start_read_A <= 1'b1;
                start_write_A <= 1'b0;
                if (res_A) begin
                    current_sample <= value_in;
                    start_read_A <= 1'b0;
                end
            end
            WRITE_OUTPUT: begin
                // write the read sample to the output buffer
                busy <= 1'b1;
                address_A <= OUTPUT_BUFFER_BASE + num_samples_read;
                val_A <= current_sample;
                start_read_A <= 1'b0;
                start_write_A <= 1'b1;
                if (res_A) start_write_A <= 1'b0;
            end
            INCREMENT_READ_POS: begin
                busy <= 1'b1;
                start_read_A <= 1'b0;
                start_write_A <= 1'b0;
                read_pos <= read_pos + 13'h1000;
                real_read_pos <= real_read_pos + shift; // shift is in fixed point form
            end
            FIX_POS_DELTA: begin
                // when the read_pos and real_read_pos differ by 1, we need to increment/decrement read_pos
                busy <= 1'b1;
                if (pos_delta) begin
                    if (pitching_up) read_pos <= read_pos + 13'h1000;   // read pos is behind when pitching up
                    if (pitching_down) read_pos <= read_pos - 13'h1000; // read pos is ahead when pitching down
                end
            end
            FIX_OVERRUN: begin
                // when read_pos boutta overtake write_pos we gotta subtract the period of the audio so that it lands in the same spot (no popping sound)
                busy <= 1'b1;
                if (overrun) begin
                    // Standard replication {N{1'b0}} is much safer for synthesis than casts
                    read_pos <= read_pos - {period_samples, {ONES_BIT{1'b0}}};
                    real_read_pos <= real_read_pos - {period_samples, {ONES_BIT{1'b0}}};
                end else begin
                    num_samples_read <= num_samples_read + 1'b1; // Increment ONLY when safe
                    
                    // once overrun addressed and we're on our last sample, output our values
                    if (num_samples_read == BLOCK_SIZE) begin
                        read_pos_out <= read_pos;
                        real_read_pos_out <= real_read_pos;
                    end
                end
            end
            default: current_state <= IDLE;
        endcase
    end
end


endmodule
