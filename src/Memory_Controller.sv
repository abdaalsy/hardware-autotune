`timescale 1ns / 1ps
`default_nettype none
// Drop-in Basys 3 on-chip block RAM implementation of Memory_Controller.
// Keeps the original interface so existing callers do not need rewiring.

//
// Addressing:
//   address_in is a BYTE address.
//   Each 24-bit sample occupies a 4-byte slot; bits [1:0] must be zero.
//   Internally, byte address 4*N maps to BRAM word N.
//
// Handshake:
//   * Assert start_read OR start_write, with address/data stable.
//   * One synchronous BRAM access occurs when the request is accepted.
//   * res pulses high on a subsequent rising clock edge. For reads, val_out
//     is valid before res rises and remains stable until the next read.
//   * Bring both start signals low before making a new request.
//   * Both one-clock pulses and requests held until res are supported.
//
// Each instance infers its own RAM. Instantiate one for RAM0 and one for RAM1.
// The external SPI pins are retained solely for port compatibility; they are
// inactive and can be removed once the top-level design is updated.
module Memory_Controller #(
parameter int BIT_WIDTH   = 24,
parameter int DEPTH_WORDS = 8192
) (
input  logic                     rst_n,
input  logic                     clk,
input  logic [23:0]              address_in,
input  logic [BIT_WIDTH-1:0]     val_in,
input  logic                     start_read,
input  logic                     start_write,
output logic                     res,
output logic [BIT_WIDTH-1:0]     val_out,
output logic                     CS,
output logic                     MOSI,
input  logic                     MISO,
output logic                     SCK
);
localparam int WORD_AW = (DEPTH_WORDS <= 1) ? 1 : $clog2(DEPTH_WORDS);
// No reset or initialization of the memory array: BRAM-inference friendly.
(* ram_style = "block" *)
logic [BIT_WIDTH-1:0] ram [0:DEPTH_WORDS-1];

typedef enum logic [1:0] {
    IDLE,
    RESPOND,
    WAIT_RELEASE
} state_t;
state_t state;

wire [WORD_AW-1:0] word_addr = address_in[WORD_AW+1:2];
wire address_valid = (address_in[1:0] == 2'b00) &&
                     ((address_in >> 2) < DEPTH_WORDS);
wire accept_request = (state == IDLE) && (start_read || start_write);

// Single synchronous BRAM port. Writes take priority if both request
// signals are erroneously asserted; the assertion below flags this.
// val_out is intentionally not reset (resetting the memory read path can
// interfere with inference on some tools).
always_ff @(posedge clk) begin
    if (rst_n && accept_request) begin
        if (address_valid) begin
            if (start_write)
                ram[word_addr] <= val_in;
            else
                val_out <= ram[word_addr];
        end else if (start_read) begin
            val_out <= '0;
        end
    end
end

always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        state <= IDLE;
        res   <= 1'b0;
    end else begin
        res <= 1'b0;
        case (state)
            IDLE: begin
                if (start_read || start_write)
                    state <= RESPOND;
            end
            RESPOND: begin
                res   <= 1'b1;
                state <= WAIT_RELEASE;
            end
            WAIT_RELEASE: begin
                if (!start_read && !start_write)
                    state <= IDLE;
            end
            default: state <= IDLE;
        endcase
    end
end

// Kept only to preserve the original SPI-oriented module's port list.
assign CS   = 1'b1;
assign MOSI = 1'b0;
assign SCK  = 1'b0;
`ifndef SYNTHESIS
always @(posedge clk) begin if (rst_n && accept_request) begin if (start_read && start_write) $error("Memory_Controller: start_read and start_write both asserted"); if (!address_valid) $error("Memory_Controller: invalid/unaligned byte address %h for depth %0d", address_in, DEPTH_WORDS); end end
`endif
endmodule
`default_nettype wire

