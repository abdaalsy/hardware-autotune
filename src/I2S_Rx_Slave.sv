`default_nettype none 

module I2S_Rx_Slave #(
    parameter int BIT_WIDTH = 24
) (
    input logic clk,
    input logic bclk,
    input logic ws,
    input logic sd,
    input logic rst_n,
    output logic [BIT_WIDTH-1:0] sample,
    output logic busy
);

typedef enum logic [1:0] {
    IDLE,
    WAIT_BIT,
    INPUT,
    LAST_BIT
} state_t;

state_t current_state, next_state;
logic [2:0] bclk_sreg, ws_sreg;

logic bclk_posedge, ws_negedge, ws_posedge;

assign bclk_posedge = (bclk_sreg[1] == 1'b1 && bclk_sreg[2] == 1'b0);
assign ws_posedge   = (ws_sreg[2] == 1'b0 && ws_sreg[1] == 1'b1);
assign ws_negedge   = (ws_sreg[2] == 1'b1 && ws_sreg[1] == 1'b0);

// decide values of outputs based on current state
always_ff @(posedge clk or negedge rst_n) begin 
    if (!rst_n) begin 
        current_state <= IDLE;
        bclk_sreg <= 3'b000;
        ws_sreg <= 3'b000;
    sample <= 'b0;
        busy <= 1'b1;
    end else begin 
        current_state <= next_state;
        bclk_sreg <= {bclk_sreg[1:0], bclk};
        ws_sreg <= {ws_sreg[1:0], ws};
    case (current_state)
            IDLE: begin
                busy <= 1'b0;
            end
            WAIT_BIT: begin
                busy <= 1'b1;
            end
            LAST_BIT: begin
                busy <= 1'b1;
                if (bclk_posedge) begin 
                    sample <= {sample[BIT_WIDTH-2:0], sd};
                end
            end 
            INPUT: begin 
                busy <= 1'b1;
                if (bclk_posedge) begin 
                    sample <= {sample[BIT_WIDTH-2:0], sd};
                end
            end
        endcase
    end 
end 

// decide next state combinationally using current_state and other inputs
always_comb begin 
    next_state = current_state;
    case (current_state)
        IDLE: if (ws_posedge) next_state = WAIT_BIT;
        WAIT_BIT: if (bclk_posedge) next_state = INPUT;
        INPUT: if (ws_negedge) next_state = LAST_BIT;
        LAST_BIT: if (bclk_posedge) next_state = IDLE;
    endcase 
end 

endmodule
