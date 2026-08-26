`default_nettype none 

// TODO: keep clk at regular 100 MHz so that we can synchronize MISO samples

module SPI_Master #(
    parameter int BIT_WIDTH = 24
) (
    input logic start,
    input logic clk,    // 20 MHz
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
        current_state <= IDLE;
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
            START: begin 
                bit_count <= '0;
                busy <= 1'b1;
                CS <= 1'b0; // Select ram module 
                SCK <= 1'b0;
                MOSI <= 1'b0;
            end 
            START_SCK: begin
                bit_count <= '0;
                busy <= 1'b1;
                CS <= 1'b0;
                SCK <= clk;
                MOSI <= 1'b0;
            end
            READ: begin
                bit_count <= bit_count + 1'b1;
                busy <= 1'b1;
                CS <= 1'b0;
                SCK <= clk;
                MOSI <= 1'b0;
                sample_out <= {sample_out[BIT_WIDTH-2:0], MISO};
            end
            WRITE: begin
                ; // do nothing for WRITE on rising edges
            end
            END_SCK: begin
                bit_count <= '0;
                busy <= 1'b1;
                CS <= 1'b0;
                SCK <= 1'b0; // SCK is scheduled to be zero at the end of this edge, verify this in sim 
                MOSI <= 1'b0;
            end
            END: begin
                bit_count <= '0;
                busy <= 1'b1;
                CS <= 1'b1;
                SCK <= 1'b0;
                MOSI <= 1'b0;
            end 
        endcase
    end
end 

// always_ff block for clock falling edge.
// when in write state we need to drive MOSI at falling edges.
always_ff @(negedge clk) begin
    case (current_state)
        WRITE: begin
            bit_count <= bit_count + 1'b1;
            busy <= 1'b1;
            CS <= 1'b0;
            SCK <= clk;
            MOSI <= sample_in[bit_count];
        end
        default: begin
            ;   // do nothing for all other state on falling edges
            // That might hcange for like END, or END_SCK
        end
    endcase
end

logic [7:0] cmd_op;
assign cmd_op = cmnd_addr[31:24];

always_comb begin 
    next_state = current_state;
    case (current_state)
        IDLE: if (start_posedge) next_state = START;
        START: next_state = START_SCK;
        START_SCK: next_state = SEND_CMND_ADDR;
        SEND_CMND_ADDR: begin 
            if (bit_count == 0) begin // might cause early transition to READ/WRITE
                if (cmd_op == 8'h03) next_state = READ;
                if (cmd_op == 8'h02) next_state = WRITE;
            end else begin 
                next_state = END;
            end
        end 
        READ: if (bit_count == 5'(BIT_WIDTH-1)) next_state = END_SCK;
        WRITE: if (bit_count == 5'(BIT_WIDTH-1)) next_state = END_SCK;
        END_SCK: next_state = END;
        END: next_state = IDLE;
    endcase 
end

endmodule
