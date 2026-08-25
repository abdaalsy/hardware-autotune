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
    output logic done
);

typedef enum logic[1:0] {
    RESET,
    LAST_BIT_FIRST, // needed so that we dont shift sample reg after exiting reset
    LAST_BIT,
    OUTPUT
} state_t;

state_t current_state, next_state;
logic[2:0] bclk_sreg, ws_sreg;
logic bclk_negedge;
logic ws_edge;
logic[BIT_WIDTH-1:0] sample_reg;

assign bclk_posedge = (bclk_sreg[1] == 1'b0 && bclk_sreg[2] == 1'b1);
assign ws_edge      = (ws_sreg[1] != ws_sreg[2]);

always_ff @(posedge clk, negedge rst_n) begin 
    if (!rst_n) begin 
        current_state <= RESET;
        bclk_sreg <= 3'b000;
        ws_sreg <= 3'b000;
        done <= 1'b0;
        sd <= 1'b0;
        sample_reg <= sample;
    end else begin 
        current_state <= next_state;
        bclk_sreg <= {bclk_sreg[+1:0], bclk};
        ws_sreg <= {ws_sreg[1:0], ws};
        case (current_state)
            RESET: begin
                bclk_sreg <= 3'b000;
                ws_sreg <= 3'b000;
                sd <= 1'b0;
                done <= 1'b0;
                sample_reg <= sample;
            end
            LAST_BIT_FIRST: begin 
                // do nothing
            end
            LAST_BIT: begin 
                if (bclk_negedge && ws == 1'b1) begin 
                    sd <= sample_reg[BIT_WIDTH-1]; // Send LSB
                    sample_reg <= {sample_reg[BIT_WIDTH-2:0], 1'b0};
                    done <= 1'b1;
                end 
            end
            OUTPUT: begin 
                if (bclk_negedge && ws == 1'b1) begin 
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
        RESET: if (ws_edge) next_state = LAST_BIT;
        LAST_BIT_FIRST: if (bclk_negedge) next_state = OUTPUT;
        LAST_BIT: if (bclk_negedge) next_state = OUTPUT;
        OUTPUT: if (ws_edge) next_state = LAST_BIT;
    endcase
end 
