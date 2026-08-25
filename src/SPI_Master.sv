`default_nettype none 

module SPI_Master #(
    parameter int BIT_WIDTH = 24
) (
    input logic start,
    input logic clk,
    input logic rst_n,
    input logic [31:0] cmnd_addr,
    input logic [BIT_WIDTH-1:0] sample_in,
    output logic [BIT_WIDTH-1:0] sample_out,
    output logic CS,
    output logic MOSI,
    input logic MISO,
    output logic SCK,
    output logic busy
);

typedef enum logic [2:0] {
    IDLE,
    START,
    START_SCK,
    SEND_CMND_ADDR,
    READ,
    WRITE,
    END,
    END_SCK
} state_t;

logic [2:0] sck_sreg, start_sreg;
logic sck_posedge, sck_negedge, start_posedge;
state_t current_state, next_state;

assign sck_posedge = (sck_sreg[2] == 1'b0 && sck_sreg[1] == 1'b1);
assign sck_negedge = (sck_sreg[2] == 1'b1 && sck_sreg[1] == 1'b0);
assign start_posedge = (start_sreg[2] == 1'b0 && start_sreg[1] == 1'b1);

logic [4:0] bit_count; 

always_ff @(posedge clk or negedge rst_n) begin 
    if (!rst_n) begin 
        bit_count <= '0;
        sck_sreg <= '0;
        start_sreg <= '0;
        busy <= 1'b1;
        CS <= 1'b1;
        SCK <= 1'b0;
        MOSI <= 1'b0;
    end else begin 
        current_state <= next_state;
        sck_sreg <= {sck_sreg[1:0], SCK};
        start_sreg <= {start_sreg[1:0], start};
        case (current_state)
            IDLE: begin 
                bit_count <= '0;
                busy <= 1'b0;
                CS <= 1'b1;
                SCK <= 1'b0;
                MOSI <= 1'b0;
            end
        endcase
    end

end 

always_comb begin 
    next_state = current_state;
    case (current_state)
        IDLE: if (start_posedge) next_state = START;
        START: next_state = START_SCK;
        START_SCK: next_state = SEND_CMND_ADDR;
        SEND_CMND_ADDR: begin 
            if (bit_count == 0) begin // might cause early transition to READ/WRITE
                if (cmnd_addr[31:24] == 8'h03) next_state = READ;
                if (cmnd_addr[31:24] == 8'h02) next_state = WRITE;
                else next_state = END;
            end 
        end 
        READ: if (bit_count == 5'BIT_WIDTH) next_state = END_SCK;
        WRITE: if (bit_count == 5'BIT_WIDTH) next_state = END_SCK;
        END_SCK: next_state = END;
        END: next_state = IDLE;
    endcase 
end 
