`default_nettype none 

module Memory_Controller #(
    parameter int BIT_WIDTH = 24
) (
    input logic rst_n,
    input logic clk,
    input logic [23:0] address_A,
    input logic [BIT_WIDTH-1:0] val_A,
    input logic                 start_read_A,
    input logic                 start_write_A,
    output logic res_A,
    input logic [23:0] address_B,
    input logic [BIT_WIDTH-1:0] val_B,
    input logic                 start_read_B,
    input logic                 start_write_B,
    output logic res_B,
    output logic [BIT_WIDTH-1:0] val_out,
    output logic CS,
    output logic MOSI,
    input logic MISO,
    output logic SCK
);

typedef enum logic [2:0] {
    IDLE,
    SERVICE_A,
    SERVICE_B,
    OUTPUT_A,
    OUTPUT_B
} state_t;

state_t current_state, next_state;

logic [2:0] reqA_sreg, reqB_sreg, busy_sreg;
logic [7:0] command;
logic [BIT_WIDTH-1:0] address;
logic [31:0] value_in;
logic [31:0] full_val_out;
logic reqA_posedge, reqB_posedge, busy_negedge, start_spi, service_b_after, busy; 

assign reqA_posedge = (reqA_sreg[2] == 1'b0 && reqA_sreg[1] == 1'b1);
assign reqB_posedge = (reqB_sreg[2] == 1'b0 && reqB_sreg[1] == 1'b1);
assign busy_negedge = (busy_sreg[2] == 1'b1 && busy_sreg[1] == 1'b0);
assign val_out = full_val_out[31:(32-BIT_WIDTH)];

SPI_Master u_SPI_Master (
    .start(start_spi),
    .clk(clk),
    .rst_n(rst_n),
    .cmnd_addr({command, address}),
    .sample_in(value_in),
    .sample_out(full_val_out),
    .CS(CS),
    .MOSI(MOSI),
    .MISO(MISO),
    .SCK(SCK),
    .busy(busy)
);

always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin 
        current_state <= IDLE;
        reqA_sreg <= 3'b0;
        reqB_sreg <= 3'b0;
        busy_sreg <= 3'b111;
        res_A <= 1'b1;
        res_B <= 1'b1;
        command <= 8'h00;
        address <= '0;
        value_in <= '0;
        start_spi <= 1'b0;
        service_b_after <= 1'b0;
    end else begin
        current_state <= next_state;
        reqA_sreg <= {reqA_sreg[1:0], start_read_A | start_write_A};
        reqB_sreg <= {reqB_sreg[1:0], start_read_B | start_write_B};
        busy_sreg <= {busy_sreg[1:0], busy};
        case (current_state)
            IDLE: begin
                // val_out <= '0; let val_out keep its value from OUTPUT
                value_in <= '0;
                start_spi <= 1'b0;
                if (reqA_posedge && reqB_posedge) service_b_after <= 1'b1;
            end
            SERVICE_A: begin
                res_A <= 1'b0;
                if (start_read_A) begin
                    command <= 8'h03;
                end else if (start_write_A) begin
                    command <= 8'h02;
                    value_in <= {val_A, 8'h00};
                end
                address <= address_A;
                start_spi <= 1'b1;
            end
            SERVICE_B: begin
                service_b_after <= 1'b0;
                res_B <= 1'b0;
                if (start_read_B) begin
                    command <= 8'h03;
                end else if (start_write_B) begin
                    command <= 8'h02;
                    value_in <= {val_B, 8'h00};
                end
                address <= address_B;
                start_spi <= 1'b1;
            end
            OUTPUT_A: begin
                res_A <= 1'b1;
            end
            OUTPUT_B: begin
                res_B <= 1'b1;
            end
            default: current_state <= IDLE;
        endcase
    end
end

always_comb begin
    next_state = current_state;
    case (current_state)
        IDLE: begin
            if (reqA_posedge) begin // prioritize A
                next_state = SERVICE_A;
            end else if (reqB_posedge) begin
                next_state = SERVICE_B;
            end
            if (service_b_after) next_state = SERVICE_B;
        end
        SERVICE_A: begin
            if (busy_negedge) next_state = OUTPUT_A;
        end
        SERVICE_B: begin
            if (busy_negedge) next_state = OUTPUT_B;
        end
        OUTPUT_A: next_state = IDLE;
        OUTPUT_B: next_state = IDLE;
        default: next_state = IDLE;
    endcase
end



endmodule
