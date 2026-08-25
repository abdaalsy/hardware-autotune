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
    output logic done
);

typedef enum logic [1:0] {
    RESET,
    LAST_BIT,
    INPUT
} state_t;

state_t current_state, next_state;
logic [2:0] bclk_sreg, ws_sreg;

logic bclk_posedge;
logic ws_edge;

assign bclk_posedge = (bclk_sreg[1] == 1'b1 && bclk_sreg[2] == 1'b0);
assign ws_edge      = (ws_sreg[1] != ws_sreg[2]);

// decide values of outputs based on current state
always_ff @(posedge clk or negedge rst_n) begin 
    if (!rst_n) begin 
        current_state <= RESET;
    bclk_sreg <= 3'b000;
        ws_sreg <= 3'b000;
        sample <= 'b0;
        done <= 1'b0;
    end else begin 
        current_state <= next_state;
        bclk_sreg <= {bclk_sreg[1:0], bclk};
        ws_sreg <= {ws_sreg[1:0], ws};
        case (current_state)
            RESET: begin
                bclk_sreg <= 3'b000;
                ws_sreg <= 3'b000;
                sample <= 'b0;
                done <= 1'b0;
            end 
            LAST_BIT: begin 
                if (bclk_posedge && ws == 1'b1) begin 
                    sample <= {sample[BIT_WIDTH-2:0], sd};
                    done <= 1'b1;
                end
            end 
            INPUT: begin 
                // Only sample for one value of WS, left OR right
                if (bclk_posedge && ws == 1'b1) begin 
                    sample <= {sample[BIT_WIDTH-2:0], sd};
                    done <= 1'b0;
                end
            end
        endcase
    end 
end 

// decide next state combinationally using current_state and other inputs
always_comb begin 
    next_state = current_state;
    case (current_state)
        RESET: 
            if (ws_edge) next_state = LAST_BIT;
        LAST_BIT: 
            if (bclk_posedge) next_state = INPUT;
        INPUT: 
            if (ws_edge) next_state = LAST_BIT;
        default: next_state = RESET;
    endcase 
end 

endmodule
