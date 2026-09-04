`default_nettype none 

module I2S_Tx_Slave #(
    parameter int BIT_WIDTH = 24    
) ( 
    input logic clk,
    input logic bclk,
    input logic ws,
    output logic sd,
    input logic rst_n,
    input logic [BIT_WIDTH-1:0] sample,
    output logic busy
);

typedef enum logic[1:0] {
    IDLE,
    WAIT_BIT, // needed so that we dont shift sample reg after exiting reset
    OUTPUT,
    LAST_BIT
} state_t;

state_t current_state, next_state;
logic[2:0] bclk_sreg, ws_sreg;
logic bclk_negedge, ws_posedge, ws_negedge;
logic[BIT_WIDTH-1:0] sample_reg;

assign bclk_negedge = (bclk_sreg[2] == 1'b1 && bclk_sreg[1] == 1'b0);
assign ws_posedge   = (ws_sreg[2] == 1'b0 && ws_sreg[1] == 1'b1);
assign ws_negedge   = (ws_sreg[2] == 1'b1 && ws_sreg[1] == 1'b0);

always_ff @(posedge clk, negedge rst_n) begin 
    if (!rst_n) begin 
        current_state <= IDLE;
        bclk_sreg <= 3'b000;
        ws_sreg <= 3'b000;
        busy <= 1'b1;
        sd <= 1'b0;
        sample_reg <= '0;
    end else begin 
        current_state <= next_state;
        bclk_sreg <= {bclk_sreg[1:0], bclk};
    ws_sreg <= {ws_sreg[1:0], ws};
        case (current_state)
            IDLE: begin
                busy <= 1'b0;
                sample_reg <= sample;
            end
            WAIT_BIT: begin 
                busy <= 1'b1;
                sample_reg <= sample;
            end
            LAST_BIT: begin 
                busy <= 1'b1;
                if (bclk_negedge) begin 
                    sd <= sample_reg[BIT_WIDTH-1]; // Send LSB
                    sample_reg <= {sample_reg[BIT_WIDTH-2:0], 1'b0};
                end 
            end
            OUTPUT: begin 
                busy <= 1'b1;
                if (bclk_negedge) begin 
                    sd <= sample_reg[BIT_WIDTH-1]; // Send next bit (starting at MSB)
                    sample_reg <= {sample_reg[BIT_WIDTH-2:0], 1'b0}; // shift sample register to the left
                end
            end
        endcase
    end 
end 

always_comb begin 
    next_state = current_state;
    case (current_state)
        IDLE: if (ws_posedge) next_state = WAIT_BIT;
        WAIT_BIT: if (bclk_negedge) next_state = OUTPUT;
        OUTPUT: if (ws_negedge) next_state = LAST_BIT; // if there's no ws_negedge then design will hang
        LAST_BIT: if (bclk_negedge) next_state = IDLE; 
    endcase
end

endmodule
