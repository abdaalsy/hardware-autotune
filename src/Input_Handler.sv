`default_nettype none

// The input handler gets a sample from the I2S Rx Slave and writes it to memory
// The module controlling this one knows a sample is available by the ready output
// At which point it should pulse start and the sample will get written to memory
module Input_Handler #(
    parameter int BIT_WIDTH = 24
) (
    input logic rst_n,
    input logic clk,
    input logic start,
    input logic [23:0] address,
    input logic bclk,
    input logic ws,
    input logic sd,
    input logic res_A,
    output logic ready,
    output logic [23:0] address_A,
    output logic [BIT_WIDTH-1:0] val_A,
    output logic start_write_A
);

typedef enum logic {
    IDLE,
    WRITE
} state_t;

state_t current_state, next_state;

logic [BIT_WIDTH-1:0] sample_in;
logic [2:0] start_sreg, resA_sreg;
logic start_posedge, resA_posedge, ready_n;

assign start_posedge = (start_sreg[2] == 1'b0 && start_sreg[1] == 1'b1);
assign resA_posedge = (resA_sreg[2] == 1'b0 && resA_sreg[1] == 1'b1);
assign address_A = address;
assign ready = ~ready_n;

I2S_Rx_Slave u_i2s_rx (
    .clk(clk),
    .rst_n(rst_n),
    .bclk(bclk),
    .ws(ws),
    .sd(sd),
    .sample(sample_in),
    .busy(ready_n)
);

always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        current_state <= IDLE;
        start_write_A <= 1'b0;
        start_sreg <= '0;
        resA_sreg <= '0;
        val_A <= '0;
    end else begin
        current_state <= next_state;
        start_sreg <= {start_sreg[1:0], start};
        resA_sreg <= {resA_sreg[1:0], res_A};
        case (current_state)
            IDLE: begin
                start_write_A <= 1'b0;
                val_A <= sample_in; // when start gets pulsed, val_A will have the most recent sample_in which will be good
            end
            WRITE: begin
                start_write_A <= 1'b1;
            end
        endcase
    end
end

always_comb begin
    next_state = current_state;
    case (current_state)
        IDLE: if (start_posedge) next_state = WRITE;
        WRITE: if (resA_posedge) next_state = IDLE;
    endcase
end

endmodule
