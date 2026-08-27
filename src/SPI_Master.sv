`default_nettype none 

module SPI_Master #(
    parameter int BIT_WIDTH = 24
) (
    input logic start,
    input logic clk,    // 50 MHz
    input logic rst_n,
    input logic [31:0] cmnd_addr,
    input logic [31:0] sample_in,
    output logic [31:0] sample_out,
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

logic [2:0] clk6_sreg, start_sreg, miso_sreg;
logic clk6_posedge, clk6_negedge, start_posedge;
state_t current_state, next_state;

assign clk6_posedge = (clk6_sreg[2] == 1'b0 && clk6_sreg[1] == 1'b1);
assign clk6_negedge = (clk6_sreg[2] == 1'b1 && clk6_sreg[1] == 1'b0);
assign start_posedge = (start_sreg[2] == 1'b0 && start_sreg[1] == 1'b1);

logic [5:0] bit_count_cmndaddr, bit_count_rw;
logic clk_6mhz;
logic [2:0] cycles;
logic start_sck;
logic [7:0] cmd_op;

assign cmd_op = cmnd_addr[31:24];
assign clk_6mhz = cycles[2];

always_ff @(posedge clk or negedge rst_n) begin 
    if (!rst_n) begin
        current_state <= IDLE;
        bit_count_cmndaddr <= 6'd32;
        bit_count_rw <= 6'd32;
        cycles <= '0;
        clk6_sreg <= '0;
        start_sreg <= '0;
        miso_sreg <= '0;
        start_sck <= 1'b0;
        busy <= 1'b1;
        CS <= 1'b1;
        SCK <= 1'b0;
        MOSI <= 1'b0;
        sample_out <= '0;
    end else begin
        current_state <= next_state;
        clk6_sreg <= {clk6_sreg[1:0], clk_6mhz};
        start_sreg <= {start_sreg[1:0], start};
        miso_sreg <= {miso_sreg[1:0], MISO};
        cycles <= cycles + start_sck;
        case (current_state)
            IDLE: begin
                bit_count_cmndaddr <= 6'd32;
                bit_count_rw <= 6'd32;
                start_sck <= 1'b0;
                busy <= 1'b0;
                CS <= 1'b1;
                SCK <= 1'b0;
                MOSI <= 1'b0;
            end
            START: begin 
                busy <= 1'b1;
                CS <= 1'b0; // Select ram module 
                SCK <= 1'b0;
                MOSI <= 1'b0;
            end 
            START_SCK: begin
                start_sck <= 1'b1;
                bit_count_cmndaddr <= bit_count_cmndaddr - 1'b1;
                busy <= 1'b1;
                CS <= 1'b0;
                SCK <= clk6_sreg[2];
                MOSI <= cmnd_addr[5'd31];
            end
            SEND_CMND_ADDR: begin
                busy <= 1'b1;
                CS <= 1'b0;
                SCK <= clk6_sreg[2];
                if (clk6_negedge) begin
                    bit_count_cmndaddr <= bit_count_cmndaddr - 1'b1;
                    MOSI <= cmnd_addr[bit_count_cmndaddr - 1'b1];
                end
            end
            WRITE: begin
                busy <= 1'b1;
                CS <= 1'b0;
                SCK <= clk6_sreg[2];
                if (clk6_negedge) begin 
                    bit_count_rw <= bit_count_rw - 1'b1;
                    MOSI <= sample_in[bit_count_rw - 1'b1];
                end
            end
            READ: begin
                busy <= 1'b1;
                CS <= 1'b0;
                SCK <= clk6_sreg[2];
                MOSI <= 1'b0;
                if (clk6_posedge == 1'b1) begin 
                    sample_out <= {sample_out[30:0], miso_sreg[2]};
                end
                if (clk6_negedge == 1'b1) begin
                    bit_count_rw <= bit_count_rw - 1'b1;
                end
            end
            END_SCK: begin
                busy <= 1'b1;
                CS <= 1'b0;
                SCK <= clk6_sreg[2];
                // Grab last bit when transitioning from READ state
                if (cmd_op == 8'h03 && clk6_posedge == 1'b1) begin
                    sample_out <= {sample_out[30:0], miso_sreg[2]};
                end
                if (clk6_negedge == 1'b1) begin
                    start_sck <= 1'b0;
                    cycles <= '0;
                    MOSI <= 1'b0;
                end
            end
            END: begin
                busy <= 1'b1;
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
            if (bit_count_cmndaddr == 6'b0) begin
                if (cmd_op == 8'h03) next_state = READ;
                if (cmd_op == 8'h02) next_state = WRITE;
            end
        end 
        READ: if (bit_count_rw == 6'd63) next_state = END_SCK;
        WRITE: if (bit_count_rw == 6'b0) next_state = END_SCK;
        END_SCK: if (start_sck == 1'b0) next_state = END;
        END: next_state = IDLE;
    endcase 
end

endmodule
