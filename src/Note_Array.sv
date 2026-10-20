`timescale 1ns / 1ps

`default_nettype none

module Note_Array #(
    parameter int BIT_WIDTH = 24,
    parameter int FRACTIONAL_BITS = 12
) (
    input  logic rst_n,
    input  logic clk,
    input  logic start,
    input  logic [4:0] key,  // 0-11 = C through B, 12 = chromatic
    input  logic scale,      // 1 = major, 0 = natural minor

    output logic [BIT_WIDTH-1:0] num_notes,
    output logic busy,

    // Memory interface
    output logic                        start_read,
    output logic                        start_write,
    output logic [BIT_WIDTH-1:0]        address,
    output logic [BIT_WIDTH-1:0]        val_out,
    input  logic [BIT_WIDTH-1:0]        val_in,
    input  logic                        res,

    // CORDIC engine interface
    // Division (opcode=0) performs B/A
    output logic                        cordic_start,
    output logic                        opcode,
    output logic signed [BIT_WIDTH-1:0] A_out,
    output logic signed [BIT_WIDTH-1:0] B_out,
    input  logic signed [BIT_WIDTH-1:0] ans,
    input  logic                        cordic_busy
);

// ============================================================
// Constants
// ============================================================

localparam logic [BIT_WIDTH-1:0] NOTE_FREQ_BASE = 24'h10;

localparam real SCALE_FACTOR = 2.0 ** FRACTIONAL_BITS;

localparam logic [BIT_WIDTH-1:0] MAX_FREQ =
    800.0 * SCALE_FACTOR;

// Reciprocals of semitone and whole-tone ratios.
// Dividing by these produces the next frequency.
localparam real HALF_STEP_REAL  = 1.0 / 1.0594630944;
localparam real WHOLE_STEP_REAL = 1.0 / 1.1224620483;

localparam logic [BIT_WIDTH-1:0] HALF_STEP =
    HALF_STEP_REAL * SCALE_FACTOR;

localparam logic [BIT_WIDTH-1:0] WHOLE_STEP =
    WHOLE_STEP_REAL * SCALE_FACTOR;

// ============================================================
// Octave 1 Frequencies (Hz)
// ============================================================

localparam real C1_REAL  = 32.703195663;
localparam real CS1_REAL = 34.647828872;
localparam real D1_REAL  = 36.708095990;
localparam real DS1_REAL = 38.890872965;
localparam real E1_REAL  = 41.203444614;
localparam real F1_REAL  = 43.653528929;
localparam real FS1_REAL = 46.249302839;
localparam real G1_REAL  = 48.999429498;
localparam real GS1_REAL = 51.913087197;
localparam real A1_REAL  = 55.000000000;
localparam real AS1_REAL = 58.270470190;
localparam real B1_REAL  = 61.735412657;

// ============================================================
// Fixed-Point Frequency Constants
// ============================================================

localparam logic [BIT_WIDTH-1:0] C1  = C1_REAL  * SCALE_FACTOR;
localparam logic [BIT_WIDTH-1:0] CS1 = CS1_REAL * SCALE_FACTOR;
localparam logic [BIT_WIDTH-1:0] D1  = D1_REAL  * SCALE_FACTOR;
localparam logic [BIT_WIDTH-1:0] DS1 = DS1_REAL * SCALE_FACTOR;
localparam logic [BIT_WIDTH-1:0] E1  = E1_REAL  * SCALE_FACTOR;
localparam logic [BIT_WIDTH-1:0] F1  = F1_REAL  * SCALE_FACTOR;
localparam logic [BIT_WIDTH-1:0] FS1 = FS1_REAL * SCALE_FACTOR;
localparam logic [BIT_WIDTH-1:0] G1  = G1_REAL  * SCALE_FACTOR;
localparam logic [BIT_WIDTH-1:0] GS1 = GS1_REAL * SCALE_FACTOR;
localparam logic [BIT_WIDTH-1:0] A1  = A1_REAL  * SCALE_FACTOR;
localparam logic [BIT_WIDTH-1:0] AS1 = AS1_REAL * SCALE_FACTOR;
localparam logic [BIT_WIDTH-1:0] B1  = B1_REAL  * SCALE_FACTOR;

// ============================================================
// Root Note Lookup Table
// ============================================================

function automatic logic [BIT_WIDTH-1:0] get_root_note(
    input logic [4:0] note_key
);
    case (note_key)
        5'd0:  get_root_note = C1;
        5'd1:  get_root_note = CS1;
        5'd2:  get_root_note = D1;
        5'd3:  get_root_note = DS1;
        5'd4:  get_root_note = E1;
        5'd5:  get_root_note = F1;
        5'd6:  get_root_note = FS1;
        5'd7:  get_root_note = G1;
        5'd8:  get_root_note = GS1;
        5'd9:  get_root_note = A1;
        5'd10: get_root_note = AS1;
        5'd11: get_root_note = B1;
        default: get_root_note = C1;
    endcase
endfunction


// ============================================================
// Scale Step Patterns
// 1 = whole step, 0 = half step
// Index 0 is the first interval.
// ============================================================

localparam logic [6:0] MAJOR_STEPS = 7'b0111011;
localparam logic [6:0] MINOR_STEPS = 7'b1101101;

// ============================================================
// FSM States
// ============================================================

typedef enum logic [2:0] {
    IDLE,
    WRITE_REQ,
    WRITE_WAIT,
    DIV_START,
    DIV_WAIT_BUSY,
    DIV_WAIT_DONE,
    CHECK_LIMIT
} state_t;

state_t current_state;

// ============================================================
// Internal Registers and Signals
// ============================================================

logic [BIT_WIDTH-1:0] current_freq;

logic [2:0] step_index;

logic prev_start;
logic prev_res;

logic run_chromatic;
logic run_scale;

logic whole_step;

// ============================================================
// Combinational Logic
// ============================================================

assign busy = (current_state != IDLE) || !rst_n;

assign start_read = 1'b0;

assign whole_step = run_scale ?
                    MAJOR_STEPS[step_index] :
                    MINOR_STEPS[step_index];

// ============================================================
// Sequential FSM
// ============================================================

always_ff @(posedge clk or negedge rst_n) begin

    if (!rst_n) begin

        current_state <= IDLE;

        num_notes <= '0;
        current_freq <= '0;
        step_index <= '0;

        prev_start <= 1'b0;
        prev_res <= 1'b0;

        run_chromatic <= 1'b0;
        run_scale <= 1'b0;

        start_write <= 1'b0;

        cordic_start <= 1'b0;
        opcode <= 1'b0;

        A_out <= '0;
        B_out <= '0;

        address <= '0;
        val_out <= '0;

    end else begin

        // Previous input values for edge detection
        prev_start <= start;
        prev_res <= res;

        // Default: request signals are one-cycle pulses
        start_write <= 1'b0;
        cordic_start <= 1'b0;

        case (current_state)

            // =================================================
            // IDLE
            // Wait for start and initialize note generation
            // =================================================

            IDLE: begin

                if (start && !prev_start && key <= 5'd12) begin

                    num_notes <= '0;
                    step_index <= '0;

                    // Capture settings for the run
                    run_chromatic <= (key == 5'd12);
                    run_scale <= scale;

                    // Select initial frequency
                    current_freq <= get_root_note(key);

                    current_state <= WRITE_REQ;

                end

            end

            // =================================================
            // WRITE_REQ
            // Send current frequency to memory
            // =================================================

            WRITE_REQ: begin

                address <= NOTE_FREQ_BASE + (num_notes << 2);

                val_out <= current_freq;

                start_write <= 1'b1;

                current_state <= WRITE_WAIT;

            end

            // =================================================
            // WRITE_WAIT
            // Wait for memory write acknowledgment
            // =================================================

            WRITE_WAIT: begin

                if (res && !prev_res) begin

                    num_notes <= num_notes + 1'b1;

                    current_state <= DIV_START;

                end

            end

            // =================================================
            // DIV_START
            // Configure CORDIC to generate next frequency
            // =================================================

            DIV_START: begin

                opcode <= 1'b0;

                B_out <= $signed(current_freq);

                if (run_chromatic) begin

                    A_out <= $signed(HALF_STEP);

                end else begin

                    A_out <= $signed(
                        whole_step ? WHOLE_STEP : HALF_STEP
                    );

                end

                cordic_start <= 1'b1;

                current_state <= DIV_WAIT_BUSY;

            end

            // =================================================
            // DIV_WAIT_BUSY
            // Wait for CORDIC to begin computation
            // =================================================

            DIV_WAIT_BUSY: begin

                if (cordic_busy) begin

                    current_state <= DIV_WAIT_DONE;

                end

            end

            // =================================================
            // DIV_WAIT_DONE
            // Wait for CORDIC completion and store result
            // =================================================

            DIV_WAIT_DONE: begin

                if (!cordic_busy) begin

                    current_freq <= $unsigned(ans);

                    // Advance the scale interval
                    if (!run_chromatic) begin

                        if (step_index == 3'd6)
                            step_index <= '0;
                        else
                            step_index <= step_index + 1'b1;

                    end

                    current_state <= CHECK_LIMIT;

                end

            end

            // =================================================
            // CHECK_LIMIT
            // Stop if next note exceeds 800 Hz
            // =================================================

            CHECK_LIMIT: begin

                if (current_freq > MAX_FREQ) begin

                    current_state <= IDLE;

                end else begin

                    current_state <= WRITE_REQ;

                end

            end

            default: begin

                current_state <= IDLE;

            end

        endcase

    end

end

endmodule
