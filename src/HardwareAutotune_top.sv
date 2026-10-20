`timescale 1ns / 1ps

`default_nettype none
// Basys 3 on-chip-RAM integration prototype.
// Inputs are externally generated I2S clocks (ADC/DAC both masters).
// clk is the 100 MHz FPGA system clock, NOT BCK.
// RAM0: circular resampling history and two 16-sample output banks.
// RAM1: notes, YIN autocorrelations, decimated rolling pitch frame.
// Status output flags expose dropped work; this is not yet sign-off quality.
module HardwareAutotune_top #(
    parameter int BIT_WIDTH = 24,
    parameter int BLOCK_SIZE = 16
) (
    input logic clk,
    input logic rst_n,
    input logic adc_bclk,
    input logic adc_ws,
    input logic adc_sd,
    input logic dac_bclk,
    input logic dac_ws,
    output logic dac_sd,
    input logic [3:0] root_key, // 0=C through 11=B
    input logic major_scale,
    input logic chromatic,
    output logic [BIT_WIDTH-1:0] debug_pitch,
    output logic [BIT_WIDTH-1:0] debug_shift,
    output logic [BIT_WIDTH-1:0] debug_num_notes,
    output logic overrun_flag,
    output logic missed_frame
);
    localparam logic [23:0] CIRCULAR_BASE = 24'h000810;
    localparam logic [23:0] OUT0_BASE = 24'h000410;
    localparam logic [23:0] OUT1_BASE = 24'h000510;
    // The analysis frame is a fixed 1024-word circular allocation in RAM1.
    localparam logic [23:0] FRAME_BASE_PHYS = 24'h008000;
    localparam logic [23:0] AUTOCOR_BASE = 24'h004000;
    localparam logic [23:0] ONE = 24'h001000;

    // Declare CORDIC shared interconnect before client instantiations.
    // Icarus Verilog requires these to be declared prior to port bindings.
    logic [2:0] cd_start,cd_opcode,cd_busy_client;
    logic signed [2:0][23:0] cd_A,cd_B,cd_answer;

    // ------------------------------------------------------------------
    // RAM0 arbiter: 0=ADC write, 1=resampler, 2=DAC read.
    // ------------------------------------------------------------------
    logic [2:0] ram0_rd, ram0_wr, ram0_res;
    logic [2:0][23:0] ram0_addr,ram0_din;
    logic [23:0] ram0_read_value;
    logic m0_rd,m0_wr,m0_res;
    logic [23:0] m0_addr,m0_val,m0_data;

    RAM_Request_Arbiter #(.CLIENTS(3),.BIT_WIDTH(BIT_WIDTH)) u_arb0 (
        .clk(clk),.rst_n(rst_n),.request_read(ram0_rd),
        .request_write(ram0_wr),.request_address(ram0_addr),
        .request_value(ram0_din),.response(ram0_res),
        .read_value(ram0_read_value),.mem_read(m0_rd),
        .mem_write(m0_wr),.mem_address(m0_addr),
        .mem_value_out(m0_val),.mem_response(m0_res),.mem_value_in(m0_data)
    );
    Memory_Controller #(.BIT_WIDTH(BIT_WIDTH),.DEPTH_WORDS(8192)) u_ram0 (
        .clk(clk),.rst_n(rst_n),.address_in(m0_addr),.val_in(m0_val),
        .start_read(m0_rd),.start_write(m0_wr),.res(m0_res),.val_out(m0_data),
        .CS(),.MOSI(),.MISO(1'b0),.SCK()
    );

    // ------------------------------------------------------------------
    // RAM1 arbiter: 0=filter writer, 1=YIN, 2=note table, 3=note matcher.
    // ------------------------------------------------------------------
    logic [3:0] ram1_rd,ram1_wr,ram1_res;
    logic [3:0][23:0] ram1_addr,ram1_din;
    logic [23:0] ram1_read_value;
    logic m1_rd,m1_wr,m1_res;
    logic [23:0] m1_addr,m1_val,m1_data;
    RAM_Request_Arbiter #(.CLIENTS(4),.BIT_WIDTH(BIT_WIDTH)) u_arb1 (
        .clk(clk),.rst_n(rst_n),.request_read(ram1_rd),
        .request_write(ram1_wr),.request_address(ram1_addr),
        .request_value(ram1_din),.response(ram1_res),
        .read_value(ram1_read_value),.mem_read(m1_rd),
        .mem_write(m1_wr),.mem_address(m1_addr),
        .mem_value_out(m1_val),.mem_response(m1_res),.mem_value_in(m1_data)
    );
    Memory_Controller #(.BIT_WIDTH(BIT_WIDTH),.DEPTH_WORDS(16384)) u_ram1 (
        .clk(clk),.rst_n(rst_n),.address_in(m1_addr),.val_in(m1_val),
        .start_read(m1_rd),.start_write(m1_wr),.res(m1_res),.val_out(m1_data),
        .CS(),.MOSI(),.MISO(1'b0),.SCK()
    );

    // ------------------------------------------------------------------
    // I2S input and output handlers
    // ------------------------------------------------------------------
    logic in_start,in_ready,in_read,in_write;
    logic [23:0] in_addr,in_mem_addr,in_val,in_sample;
    Input_Handler #(.BIT_WIDTH(BIT_WIDTH)) u_input (
        .rst_n(rst_n),.clk(clk),.start(in_start),.address(in_addr),
        .bclk(adc_bclk),.ws(adc_ws),.sd(adc_sd),.res_A(ram0_res[0]),
        .sample_out(in_sample),.ready(in_ready),.address_A(in_mem_addr),
        .val_A(in_val),.start_write_A(in_write)
    );
    assign ram0_rd[0] = 1'b0;
    assign ram0_wr[0] = in_write;
    assign ram0_addr[0] = in_mem_addr;
    assign ram0_din[0] = in_val;

    logic out_start,out_ready,out_read;
    logic [23:0] out_addr,out_mem_addr;
    Output_Handler #(.BIT_WIDTH(BIT_WIDTH)) u_output (
        .rst_n(rst_n),.clk(clk),.start(out_start),.address(out_addr),
        .res_A(ram0_res[2]),.bclk(dac_bclk),.ws(dac_ws),.sd(dac_sd),
        .ready(out_ready),.address_A(out_mem_addr),
        .val_in(ram0_read_value),.start_read_A(out_read)
    );
    assign ram0_rd[2] = out_read;
    assign ram0_wr[2] = 1'b0;
    assign ram0_addr[2] = out_mem_addr;
    assign ram0_din[2] = '0;

    // ------------------------------------------------------------------
    // Resampler: ADC writes directly to history ring; resampler performs
    // 16 circular reads + 16 output-bank writes per block.
    // ------------------------------------------------------------------
    logic resample_start,resample_busy,resample_rd,resample_wr;
    logic [23:0] resample_addr,resample_val;
    logic [23:0] in_write_pos,read_pos,real_read_pos;
    logic [23:0] result_read_pos,result_real_read_pos,result_write_pos;
    logic [23:0] ratio_current,ratio_job;
    logic [11:0] period_current,period_job;
    logic [23:0] output_job_base;
    logic job_bank;
    Resampler #(.BIT_WIDTH(BIT_WIDTH),.BLOCK_SIZE(BLOCK_SIZE),
                .DIRECT_CIRCULAR_INPUT(1'b1)) u_resampler (
        .clk(clk),.rst_n(rst_n),.start(resample_start),
        .shift(ratio_job),.output_buffer_base_in(output_job_base),
        .real_read_pos_in(real_read_pos),.read_pos_in(read_pos),
        .write_pos_in(in_write_pos),.period_samples(period_job),
        .real_read_pos_out(result_real_read_pos),
        .read_pos_out(result_read_pos),.write_pos_out(result_write_pos),
        .busy(resample_busy),.address_A(resample_addr),
        .val_A(resample_val),.start_read_A(resample_rd),
        .start_write_A(resample_wr),.res_A(ram0_res[1]),
        .value_in(ram0_read_value)
    );
    assign ram0_rd[1] = resample_rd;
    assign ram0_wr[1] = resample_wr;
    assign ram0_addr[1] = resample_addr;
    assign ram0_din[1] = resample_val;

    // ------------------------------------------------------------------
    // Bandpass: filter every 48 kHz sample; store one of every 8 to RAM1.
    // DSP arithmetic receives normalized Q12.12; full-scale raw PCM remains
    // in RAM0 for resampling.  6 kHz pitch-domain stream.
    // ------------------------------------------------------------------
    logic filter_start,filter_busy,filter_wr,filter_rd;
    logic [23:0] filter_addr,filter_val;
    logic [23:0] frame_write_addr;
    logic frame_ready, yin_frame_missed, yin_frame_overwrite;
    assign missed_frame = yin_frame_missed | yin_frame_overwrite;
    logic yin_busy,yin_start;
    Bandpass_Filter #(.BIT_WIDTH(BIT_WIDTH),.FRAME_LEN(480),
                      .DECIMATION(8),.DIRECT_FRAME_ADDRESS(1'b1),
                      .OUTPUT_SHIFT(0)) u_filter (
        .rst_n(rst_n),.clk(clk),.start(filter_start),
        .frame_addr_base(FRAME_BASE_PHYS),.frame_addr_direct(frame_write_addr),
        .sample_in($signed(in_sample) >>> 11),.busy(filter_busy),
        .start_read(filter_rd),.start_write(filter_wr),
        .address(filter_addr),.val_out(filter_val),
        .val_in(ram1_read_value),.res(ram1_res[0])
    );
    assign ram1_rd[0] = filter_rd;
    assign ram1_wr[0] = filter_wr;
    assign ram1_addr[0] = filter_addr;
    assign ram1_din[0] = filter_val;

    // ------------------------------------------------------------------
    // YIN at 6 kHz: 480-sample rolling frame, 60-sample hop (=100Hz).
    // 224-sample reference window and 192 lags cover C1 through G5.
    // ------------------------------------------------------------------
    logic [23:0] yin_pitch,yin_mem_addr,yin_mem_val;
    logic [11:0] yin_period;
    logic yin_rd,yin_wr,yin_cd_start,yin_cd_opcode;
    logic signed [23:0] yin_cd_A,yin_cd_B;
    Detect_Pitch #(.BIT_WIDTH(BIT_WIDTH),.ONES_BIT(12),
                   .FRAME_LEN(480),.HOP(60),.W(224),.RING_LEN(1024),
                   .TAU_MIN(7),.TAU_MAX(192),.FS(6000),
                   .AUTOCOR_BASE(AUTOCOR_BASE)) u_yin (
        .rst_n(rst_n),.clk(clk),.start(yin_start),
        .frame_base(FRAME_BASE_PHYS),
        .frame_write_complete(ram1_res[0]),
        .frame_write_address(frame_write_addr),
        .frame_ready(frame_ready),
        .frame_missed(yin_frame_missed),
        .frame_overwrite(yin_frame_overwrite),
        .busy(yin_busy),
        .pitch(yin_pitch),.period_samples(yin_period),
        .start_read(yin_rd),.start_write(yin_wr),
        .address(yin_mem_addr),.val_out(yin_mem_val),
        .val_in(ram1_read_value),.res(ram1_res[1]),
        .cordic_start(yin_cd_start),.opcode(yin_cd_opcode),
        .A_out(yin_cd_A),.B_out(yin_cd_B),
        .ans(cd_answer[1]),.cordic_busy(cd_busy_client[1])
    );
    // Detect_Pitch now maps logical frame reads to physical ring slots.
    assign ram1_rd[1] = yin_rd;
    assign ram1_wr[1] = yin_wr;
    assign ram1_addr[1] = yin_mem_addr;
    assign ram1_din[1] = yin_mem_val;

    // ------------------------------------------------------------------
    // Note table rebuild on startup and switch changes; match after YIN.
    // ------------------------------------------------------------------
    logic [4:0] selected_key,previous_key;
    logic previous_scale,note_build_pending,note_valid;
    logic note_start,note_busy,note_rd,note_wr;
    logic [23:0] note_mem_addr,note_mem_val,num_notes;
    logic note_cd_start,note_cd_opcode;
    logic signed [23:0] note_cd_A,note_cd_B;
    assign selected_key = chromatic ? 5'd12 : {1'b0,root_key};
    Note_Array #(.BIT_WIDTH(BIT_WIDTH),.FRACTIONAL_BITS(12)) u_notes (
        .rst_n(rst_n),.clk(clk),.start(note_start),
        .key(selected_key),.scale(major_scale),
        .num_notes(num_notes),.busy(note_busy),
        .start_read(note_rd),.start_write(note_wr),
        .address(note_mem_addr),.val_out(note_mem_val),
        .val_in(ram1_read_value),.res(ram1_res[2]),
        .cordic_start(note_cd_start),.opcode(note_cd_opcode),
        .A_out(note_cd_A),.B_out(note_cd_B),
        .ans(cd_answer[0]),.cordic_busy(cd_busy_client[0])
    );
    assign debug_num_notes = num_notes;
    assign ram1_rd[2] = note_rd;
    assign ram1_wr[2] = note_wr;
    assign ram1_addr[2] = note_mem_addr;
    assign ram1_din[2] = note_mem_val;

    logic match_start,match_busy,match_rd,match_wr;
    logic [23:0] matched_shift,match_mem_addr,match_mem_val;
    logic match_cd_start,match_cd_opcode;
    logic signed [23:0] match_cd_A,match_cd_B;
    Match_Note #(.BIT_WIDTH(BIT_WIDTH),.IN_FRAC_BITS(12)) u_match (
        .rst_n(rst_n),.clk(clk),.start(match_start),
        .num_notes(num_notes),.pitch(yin_pitch),.shift(matched_shift),
        .busy(match_busy),.start_read(match_rd),
        .start_write(match_wr),.address(match_mem_addr),
        .val_out(match_mem_val),.val_in(ram1_read_value),
        .res(ram1_res[3]),.cordic_start(match_cd_start),
        .opcode(match_cd_opcode),.A_out(match_cd_A),
        .B_out(match_cd_B),.ans(cd_answer[2]),
        .cordic_busy(cd_busy_client[2])
    );
    assign ram1_rd[3] = match_rd;
    assign ram1_wr[3] = match_wr;
    assign ram1_addr[3] = match_mem_addr;
    assign ram1_din[3] = match_mem_val;

    // Shared 24-bit CORDIC engine. Note Array, YIN, and Match Note all use
    // the same arithmetic resource; clients queue one outstanding request.
    logic shared_cd_start,shared_cd_opcode,shared_cd_busy;
    logic signed [23:0] shared_cd_A,shared_cd_B;
    logic signed [24:0] shared_cd_answer;
    assign cd_start = {match_cd_start,yin_cd_start,note_cd_start};
    assign cd_opcode = {match_cd_opcode,yin_cd_opcode,note_cd_opcode};
    assign cd_A[0]=note_cd_A;
    assign cd_A[1]=yin_cd_A;
    assign cd_A[2]=match_cd_A;
    assign cd_B[0]=note_cd_B;
    assign cd_B[1]=yin_cd_B;
    assign cd_B[2]=match_cd_B;
    Cordic_Request_Arbiter #(.BIT_WIDTH(BIT_WIDTH),.CLIENTS(3)) u_cordic_arb (
        .clk(clk),.rst_n(rst_n),.client_start(cd_start),
        .client_opcode(cd_opcode),.client_A(cd_A),.client_B(cd_B),
        .client_busy(cd_busy_client),.client_answer(cd_answer),
        .cordic_start(shared_cd_start),.cordic_opcode(shared_cd_opcode),
        .cordic_A(shared_cd_A),.cordic_B(shared_cd_B),
        .cordic_busy(shared_cd_busy),.cordic_answer(shared_cd_answer)
    );
    Cordic #(.BIT_WIDTH(BIT_WIDTH),.ONES_BIT(12)) u_cordic (
        .clk(clk),.rst_n(rst_n),.start(shared_cd_start),
        .opcode(shared_cd_opcode),.A_in(shared_cd_A),.B_in(shared_cd_B),
        .ans(shared_cd_answer),.busy(shared_cd_busy),
        .start_read(),.start_write(),.address(),.val_out(),
        .val_in(24'b0),.res(1'b0)
    );

    // ------------------------------------------------------------------
    // Runtime scheduler. Normalizes no audio in RAM0 (bit-preserving PCM).
    // ------------------------------------------------------------------
    logic in_ready_d,out_ready_d,yin_busy_d,note_busy_d,match_busy_d,resample_busy_d;
    logic input_pending;
    logic [3:0] in_block_count,playback_idx;
    logic [23:0] write_pointer;
    logic pending_resample;
    logic produced_valid,produced_bank,play_bank,play_valid;
    logic resample_writing_bank;
    logic [11:0] output_prime_count;
    assign in_addr = CIRCULAR_BASE + ({12'b0,write_pointer[23:12]} << 2);
    assign debug_pitch = yin_pitch;
    assign debug_shift = ratio_current;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            in_start <= 1'b0; out_start <= 1'b0;
            filter_start <= 1'b0; resample_start <= 1'b0;
            note_start <= 1'b0; yin_start <= 1'b0;
            match_start <= 1'b0;
            in_ready_d <= 1'b1; out_ready_d <= 1'b1;
            yin_busy_d <= 1'b0; note_busy_d <= 1'b0;
            match_busy_d <= 1'b0; resample_busy_d <= 1'b0;
            in_block_count <= '0; playback_idx <= '0;
            write_pointer <= '0; in_write_pos <= '0;
            read_pos <= '0; real_read_pos <= '0;
            ratio_current <= ONE; ratio_job <= ONE;
            period_current <= 12'd100; period_job <= 12'd100;
            input_pending <= 1'b0; pending_resample <= 1'b0;
            output_job_base <= OUT0_BASE; job_bank <= 1'b0;
            produced_valid <= 1'b0; produced_bank <= 1'b0;
            play_valid <= 1'b0; play_bank <= 1'b0;
            output_prime_count <= '0;
            previous_key <= 5'h1f; previous_scale <= 1'b0;
            note_build_pending <= 1'b1; note_valid <= 1'b0;
            overrun_flag <= 1'b0;
            out_addr <= OUT0_BASE;
        end else begin
            in_ready_d <= in_ready;
            out_ready_d <= out_ready;
            yin_busy_d <= yin_busy;
            note_busy_d <= note_busy;
            match_busy_d <= match_busy;
            resample_busy_d <= resample_busy;
            in_start <= 1'b0;
            out_start <= 1'b0;
            filter_start <= 1'b0;
            resample_start <= 1'b0;
            note_start <= 1'b0;
            yin_start <= 1'b0;
            match_start <= 1'b0;

            if (selected_key != previous_key || major_scale != previous_scale) begin
                previous_key <= selected_key;
                previous_scale <= major_scale;
                note_build_pending <= 1'b1;
                note_valid <= 1'b0;
            end
            if (note_build_pending && !note_busy && !note_start && !match_busy) begin
                if (selected_key <= 5'd12) begin
                    note_start <= 1'b1;
                    note_build_pending <= 1'b0;
                end
            end
            if (note_busy_d && !note_busy)
                note_valid <= 1'b1;

            // Input sample is ready after I2S RX goes idle at end of WS-high.
            if (in_ready && !in_ready_d && !input_pending && !filter_busy) begin
                in_start <= 1'b1;
                filter_start <= 1'b1;
                input_pending <= 1'b1;
            end else if (in_ready && !in_ready_d && (input_pending || filter_busy)) begin
                overrun_flag <= 1'b1;
            end
            // Each memory acknowledgment means one accepted ADC sample.
            if (ram0_res[0] && input_pending) begin
                input_pending <= 1'b0;
                write_pointer <= write_pointer + ONE;
                in_write_pos <= write_pointer + ONE;
                if (output_prime_count != 12'd2048)
                    output_prime_count <= output_prime_count + 1'b1;
                in_block_count <= in_block_count + 1'b1;
                if (in_block_count == 4'(BLOCK_SIZE-1)) begin
                    in_block_count <= '0;
                    if (pending_resample) overrun_flag <= 1'b1;
                    pending_resample <= 1'b1;
                end
            end
            if (pending_resample && !resample_busy && !resample_start &&
                output_prime_count >= 12'd2048 &&
                !(play_valid && playback_idx != 0 && job_bank == play_bank)) begin
                // Only begin when the previous resampling job has completed.
                resample_start <= 1'b1;
                ratio_job <= ratio_current;
                period_job <= period_current;
                output_job_base <= job_bank ? OUT1_BASE : OUT0_BASE;
                resample_writing_bank <= job_bank;
                pending_resample <= 1'b0;
            end
            if (resample_busy_d && !resample_busy) begin
                read_pos <= result_read_pos;
                real_read_pos <= result_real_read_pos;
                produced_valid <= 1'b1;
                produced_bank <= resample_writing_bank;
                job_bank <= ~resample_writing_bank;
            end
            // Read a completed output bank one sample ahead of DAC WS-high.
            if (out_ready && !out_ready_d) begin
                if (playback_idx == 0 && produced_valid) begin
                    play_bank <= produced_bank;
                    play_valid <= 1'b1;
                    // fetch from the newly completed bank
                    out_addr <= (produced_bank ? OUT1_BASE : OUT0_BASE);
                    out_start <= 1'b1;
                end else if (play_valid) begin
                    out_addr <= (play_bank ? OUT1_BASE : OUT0_BASE) +
                                (24'(playback_idx) << 2);
                    out_start <= 1'b1;
                end
                playback_idx <= playback_idx + 1'b1;
            end
            // Snapshot latest 480 decimated samples every 60 fresh samples.
            if (frame_ready && !yin_busy && !yin_start) begin
                yin_start <= 1'b1;
            end
            if (yin_busy_d && !yin_busy) begin
                period_current <= yin_period << 3; // period at 48kHz = 8 x 6k period
                if (note_valid && !match_busy && yin_pitch != '0)
                    match_start <= 1'b1;
            end
            if (match_busy_d && !match_busy && matched_shift != '0)
                ratio_current <= matched_shift;
        end
    end
endmodule

