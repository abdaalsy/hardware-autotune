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


// Assume values are in fixed point

module Resampler #(
    parameter int BIT_WIDTH = 24,
    parameter int BLOCK_SIZE = 256,
    parameter int SAMPLE_RATE = 48000,
    parameter int INPUT_BUFFER_BASE = 0,
    parameter int CIRCULAR_BUFFER_BASE = 0,
    parameter int OUTPUT_BUFFER_BASE = 0
) (
    input logic clk,
    input logic rst_n,
    input logic start,
    input logic [BIT_WIDTH-1:0] shift,
    input logic [BIT_WIDTH-1:0] read_pos,
    input logic [BIT_WIDTH-1:0] real_read_pos,
    input logic [BIT_WIDTH-1:0] write_pos,
    input logic [BIT_WIDTH-1:0] period_samples,
    input logic [BIT_WIDTH-1:0] new_read_pos,
    output logic [BIT_WIDTH-1:0] new_real_read_pos,
    output logic [BIT_WIDTH-1:0] new_write_pos,
    output logic busy,
    output logic address_A,
    output logic val_A,
    output logic start_read_A,
    output logic start_write_A,
    input logic res_A,
    input logic [BIT_WIDTH-1:0] value_in
);



endmodule
