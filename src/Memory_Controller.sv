`default_nettype none 

module Memory_Controller #(
    parameter int BIT_WIDTH = 24
) (
    input logic rst_n,
    input logic clk,
    input logic [23:0] address_in,
    input logic [BIT_WIDTH-1:0] val_in,
    input logic                 start_read,
    input logic                 start_write,
    output logic res,
    output logic [BIT_WIDTH-1:0] val_out,
    output logic CS,
    output logic MOSI,
    input logic MISO,
    output logic SCK
);

typedef enum logic [1:0] {
    IDLE,
    SERVICE,
    OUTPUT
} state_t;

state_t current_state, next_state;

logic [2:0] req_sreg, busy_sreg;
logic [7:0] command;
logic [BIT_WIDTH-1:0] address;
logic [31:0] value_in;
logic [31:0] full_val_out;
logic req_posedge, busy_negedge, start_spi, busy; 

assign req_posedge = (req_sreg[2] == 1'b0 && req_sreg[1] == 1'b1);
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
        req_sreg <= 3'b0;
        busy_sreg <= 3'b111;
        res <= 1'b1;
        command <= 8'h00;
        address <= '0;
        value_in <= '0;
        start_spi <= 1'b0;
    end else begin
        current_state <= next_state;
        req_sreg <= {req_sreg[1:0], start_read | start_write};
        busy_sreg <= {busy_sreg[1:0], busy};
        case (current_state)
            IDLE: begin
                // val_out <= '0; let val_out keep its value from OUTPUT
                value_in <= '0;
                start_spi <= 1'b0;
            end
            SERVICE: begin
                res <= 1'b0;
                if (start_read) begin
                    command <= 8'h03;
                end else if (start_write) begin
                    command <= 8'h02;
                    value_in <= {val_in, 8'h00};
                end
                address <= address_in;
                start_spi <= 1'b1;
            end
            OUTPUT: begin
                res <= 1'b1;
            end
            default: current_state <= IDLE;
        endcase
    end
end

always_comb begin
    next_state = current_state;
    case (current_state)
        IDLE: begin
            if (req_posedge) next_state = SERVICE;
        end
        SERVICE: begin
            if (busy_negedge) next_state = OUTPUT;
        end
        OUTPUT: next_state = IDLE;
        default: next_state = IDLE;
    endcase
end



endmodule
