`timescale 1ns / 1ps
`default_nettype none

// Fixed-priority multi-client request arbiter for one Memory_Controller.
// Index 0 has highest priority (typically an incoming ADC or filter write).
// Client protocol: hold read/write request and address/data until res[i] pulse,
// then LOWER request before starting a new one. Separate res pulse per client.
// No request queue: other clients hold their requests until granted.
// This is deliberately a small, area-conscious controller for an ASIC.
module RAM_Request_Arbiter #(
    parameter int CLIENTS = 3,
    parameter int BIT_WIDTH = 24
) (
    input  logic clk,
    input  logic rst_n,
    input  logic [CLIENTS-1:0] request_read,
    input  logic [CLIENTS-1:0] request_write,
    input  logic [CLIENTS-1:0][23:0] request_address,
    input  logic [CLIENTS-1:0][BIT_WIDTH-1:0] request_value,
    output logic [CLIENTS-1:0] response,
    output logic [BIT_WIDTH-1:0] read_value,

    // Interface to a single Memory_Controller (or SRAM request adapter).
    output logic mem_read,
    output logic mem_write,
    output logic [23:0] mem_address,
    output logic [BIT_WIDTH-1:0] mem_value_out,
    input  logic mem_response,
    input  logic [BIT_WIDTH-1:0] mem_value_in
);
    localparam int SEL_BITS = (CLIENTS > 1) ? $clog2(CLIENTS) : 1;
    typedef enum logic [1:0] {IDLE, SEND, WAIT_RESPONSE, WAIT_DROP} state_t;
    state_t state;
    logic [SEL_BITS-1:0] selected;
    logic valid_request;
    logic [SEL_BITS-1:0] first_request;
    logic [CLIENTS-1:0] request_any;
    integer i;

    assign request_any = request_read | request_write;
    always_comb begin
        valid_request = 1'b0;
        first_request = '0;
        for (int k = 0; k < CLIENTS; k = k+1) begin
            if (!valid_request && request_any[k]) begin
                valid_request = 1'b1;
                first_request = SEL_BITS'(k);
            end
        end
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= IDLE;
            selected <= '0;
            response <= '0;
            read_value <= '0;
            mem_read <= 1'b0;
            mem_write <= 1'b0;
            mem_address <= '0;
            mem_value_out <= '0;
        end else begin
            response <= '0;
            mem_read <= 1'b0;
            mem_write <= 1'b0;
            case (state)
                IDLE: if (valid_request) begin
                    selected <= first_request;
                    mem_read <= request_read[first_request];
                    mem_write <= !request_read[first_request] && request_write[first_request];
                    mem_address <= request_address[first_request];
                    mem_value_out <= request_value[first_request];
                    state <= SEND;
                end
                SEND: state <= WAIT_RESPONSE;
                WAIT_RESPONSE: if (mem_response) begin
                    read_value <= mem_value_in;
                    response[selected] <= 1'b1;
                    state <= WAIT_DROP;
                end
                WAIT_DROP: if (!request_any[selected]) state <= IDLE;
                default: state <= IDLE;
            endcase
        end
    end
endmodule
`default_nettype wire
