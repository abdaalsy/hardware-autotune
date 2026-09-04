`default_nettype none

// Output handler reads memory for the latest sample to be played and outputs it to the I2S Tx Slave
// When the I2S Tx Slave requires a new sample, busy is driven low. The next sample must arrive by the time busy is driven high again.
// The module controlling this one sees that and pulses the start input
// Then, the output handler will read memory, and set sample_out to the memory value
// By the time busy goes back high, the sample will be there
module Output_Handler #(
    parameter int BIT_WIDTH = 24
) (
    input logic rst_n,
    input logic clk,
    input logic start,
    input logic [23:0] address,
    input logic res_A,
    input logic bclk,
    input logic ws,
    output logic sd,
    output logic ready,
    output logic [23:0] address_A,
    input logic [BIT_WIDTH-1:0] val_in,
    output logic start_read_A
);

typedef enum logic {
    IDLE,
    READ
} state_t;

state_t current_state, next_state;

logic [BIT_WIDTH-1:0] sample_out;
logic [1:0] start_sreg, resA_sreg;
logic start_posedge, resA_posedge, ready_n;

assign start_posedge = (start_sreg[1] == 1'b0 && start_sreg[0] == 1'b1);
assign resA_posedge = (resA_sreg[1] == 1'b0 && resA_sreg[0] == 1'b1);
assign address_A = address;
assign ready = ~ready_n;

I2S_Tx_Slave u_i2s_tx (
    .clk(clk),
    .rst_n(rst_n),
    .bclk(bclk),
    .ws(ws),
    .sd(sd),
    .busy(ready_n),
    .sample(sample_out) 
);

always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        current_state <= IDLE;
        start_sreg <= '0;
        resA_sreg <= '0;
        start_read_A <= 1'b0;
        sample_out <= '0;
    end else begin
        start_sreg <= {start_sreg[0], start};
        resA_sreg <= {resA_sreg[0], res_A};
        current_state <= next_state;
        case (current_state)
            IDLE: begin
                start_read_A <= 1'b0;
            end
            READ: begin
                start_read_A <= 1'b1;
                sample_out <= val_in; // the last time sample_out gets updated is when res_A goes high
            end
        endcase
    end
end

always_comb begin
    next_state = current_state;
    case (current_state)
        IDLE: if (start_posedge) next_state = READ;
        READ: if (resA_posedge) next_state = IDLE;
    endcase
end

endmodule
