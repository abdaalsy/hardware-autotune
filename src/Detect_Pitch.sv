// ============================================================================
// Detect_Pitch : YIN pitch detector, memory-streaming, incremental
//                autocorrelation; FPGA fast path uses signed DSP multipliers
//                for products/squares. CORDIC remains for normalizations.
//
// Number format : signed Q(BIT_WIDTH-ONES_BIT).ONES_BIT (Q12.12), samples
//                 numerically bounded by upstream audio scaling; pitch is
//                 unsigned Q12.12 in Hz.
//                 period_samples is BIT_WIDTH/2 bits wide and holds only the
//                 WHOLE number of samples in one period (no fraction), rounded
//                 to the nearest sample: round(tau + interpolation correction).
//                 It is derived from the same refined period that gives
//                 pitch = FS / period (FS = 48000 by default) and does not
//                 depend on FS itself.
//
// Memory handshake
//   A transaction is started by pulsing start_read / start_write with
//   address (and val_out for writes) driven.  It COMPLETES on a RISING EDGE of
//   `res` (res_posedge, derived from the res shift register below), never on
//   the level of res -- the memory controller can hold res high while idle, so
//   a level check would fall straight through before the data is ready.
//   val_in is sampled in the cycle res_posedge is high.
//
// CORDIC assumptions (the only things the design relies on)
//   * opcode 0 : ans = B_out / A_out, operands and result all Q12.12,
//                i.e. ans = (B << ONES_BIT) / A, valid over the full signed
//                range of the result (quotients up to ~2047, e.g. 1/0.001).
//   * Multiplication results are deterministic Q12.12 fixed-point products
//     (needed for add/remove autocorrelation symmetry).
//   * ans holds its value until the next start.
//   * cordic_busy is high by two cycles after the cycle cordic_start is high.
//   * The denominator A_out is NEVER NEGATIVE and never zero when a division is
//     issued: reciprocals are taken of magnitudes and signs are applied here,
//     outside the CORDIC (sign-magnitude).  Numerators (B_out) may be signed.
//
// Memory map (byte addresses, one 24-bit value per 4-byte slot)
//   frame_base + 4*k     : physical ring slot k, k = 0 .. RING_LEN-1
//   The module translates frame index i to slot
//       (snapshot_idx + i) modulo RING_LEN, capturing snapshot_idx at start.
//   The input sample writer uses frame_write_address and asserts
//   frame_write_complete once its BRAM write is ACKNOWLEDGED.
//   AUTOCOR_BASE + 4*lag : r(lag), lag = 0 .. TAU_MAX-1  (slot 0 = r(0) =
//                          reference energy)
//
// Frame contract
//   FRAME_LEN consecutive *time samples* are read in order despite physical
//   ring wrap.  Every FRAME_LEN samples produces the first frame_ready; each
//   subsequent HOP committed samples produces another frame_ready.  Pulse
//   start after frame_ready to snapshot the oldest index of that logical frame.
//   Incremental autocorrelation is only used if frame sequence numbers prove
//   exactly one hop elapsed since the previous run.  Otherwise r is rebuilt.
//   frame_base is the fixed *physical* ring base, NOT the moving oldest address.
//   The writer may continue during detection, as long as it does not wrap
//   around and overwrite the oldest still-needed sample. If it does, an
//   overrun flag is raised and that YIN result is discarded.
//   Normal ring wrap does NOT add discontinuities: sample i and i+1 remain
//   consecutive in time even if they occupy last and first BRAM slots.
//
// Algorithm
//   Pass 1  r(lag) += sum(new products) - sum(old products), for every lag.
//           Loop is sample-major, each product uses a signed DSP multiply
//           and a read-modify-write of r(lag).
//             new : n = FRAME_LEN-HOP .. FRAME_LEN-1
//             old : n = FRAME_LEN-W-HOP .. FRAME_LEN-W-1
//           (first run: n = FRAME_LEN-W .. FRAME_LEN-1 after zeroing r)
//           Sign handling: 1/|x[n]| is computed, y / (1/|x[n]|) = y*|x[n]|, and
//           the product is negated afterwards if x[n] < 0.
//           A product that is exactly zero (|x[n]| tiny, or x[n-lag] == 0) is
//           skipped; this is the same function every time, so add/remove match.
//   Pass 2  per lag: lagged energy (recurrence, 2 squares), d(lag), CMNDF with
//           the recursive mean M += (d-M)/lag, threshold / local-min search,
//           parabolic interpolation, pitch = FS / tau.  Pass 2 may stop as soon
//           as the pitch is decided, because every r(lag) is already final.
//
// `pitch` and `period_samples` are valid when `busy` falls.
// ============================================================================
`default_nettype none

module Detect_Pitch #(
    parameter int BIT_WIDTH = 24,
    parameter int ONES_BIT  = BIT_WIDTH/2,
    parameter int HOP       = 16,
    parameter int RING_LEN  = 2048,    // power of two; fixed external frame region

    // Algorithm configuration (set these for the system; the defaults are the
    // production values.  Tests override them with a small configuration so a
    // run takes ~10^5 cycles instead of ~10^7.)
    parameter int  FRAME_LEN = 1920,     // samples per frame buffer
    parameter int  W         = 900,      // YIN window length
    parameter int  TAU_MIN   = 32,       // smallest lag searched
    parameter int  TAU_MAX   = 960,      // lags 0 .. TAU_MAX-1 are computed
    parameter int  FS        = 48000,    // sample rate in Hz
    parameter real THRESHOLD = 0.1,      // CMNDF threshold
    parameter logic [BIT_WIDTH-1:0] AUTOCOR_BASE = 'h004000
) (
    input  logic                        rst_n,
    input  logic                        clk,
    input  logic                        start,
    input  logic [BIT_WIDTH-1:0]        frame_base,   // fixed physical ring region base
    input  logic                        frame_write_complete, // 1 clock per committed filtered sample
    output logic [BIT_WIDTH-1:0]        frame_write_address,  // address for the NEXT filtered sample
    output logic                        frame_ready,   // 1 clock after initial fill and every HOP thereafter
    output logic                        frame_missed,  // sticky: update arrived while YIN busy
    output logic                        frame_overwrite, // sticky: writer overwrote active snapshot
    output logic                        busy,
    output logic [BIT_WIDTH-1:0]        pitch,            // Hz, Q12.12
    output logic [BIT_WIDTH/2-1:0]      period_samples,   // whole samples per period

    // Memory interface
    output logic                        start_read,
    output logic                        start_write,
    output logic [BIT_WIDTH-1:0]        address,
    output logic [BIT_WIDTH-1:0]        val_out,
    input  logic [BIT_WIDTH-1:0]        val_in,
    input  logic                        res,

    // CORDIC engine interface.  Division (opcode=0) performs B/A.
    // (cordic_start / cordic_busy are renamed: start / busy are taken above.)
    output logic                        cordic_start,
    output logic                        opcode,
    output logic signed [BIT_WIDTH-1:0] A_out,
    output logic signed [BIT_WIDTH-1:0] B_out,
    input  logic signed [BIT_WIDTH-1:0] ans,
    input  logic                        cordic_busy
);

    // ------------------------------------------------------------------------
    // Derived constants
    // ------------------------------------------------------------------------
    localparam int PERIOD_W = BIT_WIDTH/2;             // width of period_samples
    localparam int RING_W = (RING_LEN > 1) ? $clog2(RING_LEN) : 1;
    localparam int FILL_W = $clog2(FRAME_LEN + 1);
    localparam int HOP_W = (HOP > 1) ? $clog2(HOP) : 1;
    localparam int IDX_W    = $clog2(FRAME_LEN + 1);
    localparam int LAG_W    = $clog2(TAU_MAX);
    localparam int D_SHIFT  = 2;                       // d is held >>2 so it fits 24 bits
    localparam int FS_SHIFT = $clog2(FS / (1 << (BIT_WIDTH-1-ONES_BIT)) + 1);
    localparam int FS_Q     = (FS << ONES_BIT) >> FS_SHIFT;   // FS / 2^FS_SHIFT in Q12.12
    localparam int THRESH_Q = $rtoi(THRESHOLD * real'(1 << ONES_BIT) + 0.5);
    localparam int TINY     = 1 << (2*ONES_BIT - (BIT_WIDTH-1)); // |x| <= TINY -> 1/x overflows

    localparam logic signed [BIT_WIDTH-1:0] ONE_W  = BIT_WIDTH'(1) << ONES_BIT;
    localparam logic signed [BIT_WIDTH-1:0] HALF_W = BIT_WIDTH'(1) << (ONES_BIT-1);
    localparam logic signed [BIT_WIDTH-1:0] SAT_MAX = {1'b0, {(BIT_WIDTH-1){1'b1}}};
    localparam logic signed [BIT_WIDTH-1:0] SAT_MIN = {1'b1, {(BIT_WIDTH-1){1'b0}}};
    localparam logic signed [BIT_WIDTH-1:0] TINY_S = BIT_WIDTH'(TINY);
    localparam logic signed [BIT_WIDTH-1:0] FSQ_W  = BIT_WIDTH'(FS_Q);
    localparam logic signed [BIT_WIDTH+2:0] SMAX   = (BIT_WIDTH+3)'((1 << (BIT_WIDTH-1)) - 1);

    localparam logic [IDX_W-1:0] N_LAST     = IDX_W'(FRAME_LEN - 1);
    localparam logic [IDX_W-1:0] N_FIRST0   = IDX_W'(FRAME_LEN - W);
    localparam logic [IDX_W-1:0] N_NEW0     = IDX_W'(FRAME_LEN - HOP);
    localparam logic [IDX_W-1:0] N_OLD0     = IDX_W'(FRAME_LEN - W - HOP);
    localparam logic [IDX_W-1:0] N_OLD_LAST = IDX_W'(FRAME_LEN - W - 1);

    // Elaboration-time sanity checks
    if (RING_LEN < FRAME_LEN + HOP || (RING_LEN & (RING_LEN-1)) != 0) begin : g_chk_ring
        $error("RING_LEN must be a power of two and >= FRAME_LEN + HOP");
    end
    if (FRAME_LEN <= 0 || HOP <= 0) begin : g_chk_nonzero
        $error("FRAME_LEN and HOP must be positive");
    end
    if (FRAME_LEN < W + HOP + TAU_MAX - 1) begin : g_chk_frame
        $error("FRAME_LEN must be >= W + HOP + TAU_MAX - 1 (old samples must still be in the frame)");
    end
    if (W < HOP) begin : g_chk_hop
        $error("W must be >= HOP");
    end
    if (TAU_MIN < 1 || TAU_MIN + 2 > TAU_MAX) begin : g_chk_tau
        $error("need 1 <= TAU_MIN and TAU_MIN + 2 <= TAU_MAX");
    end
    if (TAU_MAX >= (1 << (BIT_WIDTH-1-ONES_BIT))) begin : g_chk_taumax
        $error("TAU_MAX in Q12.12 must fit a signed operand");
    end
    if (FS >= TAU_MIN * (1 << ONES_BIT) || FS_Q >= (1 << (BIT_WIDTH-1))) begin : g_chk_fs
        $error("FS / TAU_MIN does not fit the pitch format");
    end

    // ------------------------------------------------------------------------
    // State
    // ------------------------------------------------------------------------
    typedef enum logic [4:0] {
        S_IDLE,
        S_INIT,       // first run: zero r(0..TAU_MAX-1)
        S_NREAD,      // read x[n], start 1/x[n]
        S_NREC,       // A_out <= 1/x[n]
        S_TREAD,      // read x[n-lag]
        S_TPROD,      // product ready
        S_RRD,        // read r(lag)
        S_RWR,        // write r(lag) +/- product
        S_TNEXT,      // next lag
        S_NNEXT,      // next sample
        S_REF,        // read r(0) = reference energy
        S_ERD,        // read a sample for the lagged-energy recurrence
        S_EREC,       // square = x / (1/x)
        S_ESQ,        // apply square to lagged energy
        S_R2RD,       // read r(lag), form d(lag)
        S_MEAN,       // mean update: (d - M) / lag
        S_MUPD,       // M += quotient
        S_DPR,        // d' = d / M
        S_DEC,        // threshold / local-min / global-min tracking
        S_P2NEXT,     // next lag in pass 2
        S_FIN0,       // set up parabolic interpolation
        S_FIN_D,      // tau + delta
        S_FIN_P,      // pitch = FS / tau
        S_DONE,
        S_CALL,       // pulse cordic_start
        S_CWAIT       // wait for cordic_busy to fall
    } state_t;

    state_t state, ret;
    logic saw_cordic_busy;                  // wait for a real high-then-low completion
    logic       pend;                     // memory transaction in flight
    logic       warm;                     // a full run has completed since reset
    logic [RING_W-1:0] wr_idx;              // next physical slot for incoming sample
    logic [RING_W-1:0] snapshot_idx;        // physical slot of logical frame sample 0
    logic [FILL_W-1:0] fill_count;
    logic [HOP_W-1:0] hop_count;
    logic filled;
    logic [31:0] frame_seq, last_run_seq;   // identify skipped HOP updates
    logic snapshot_overwritten;            // current frame can no longer be trusted
    logic       sub;                      // pass 1: removing old products
    logic       eph;                      // pass 2 energy term: 0 = remove, 1 = add
    logic       zbyp;                     // d' bypassed to 1.0 (mean == 0)
    logic       nneg;                     // retained for compatibility
    logic signed [BIT_WIDTH-1:0] x_saved;

    // res edge detector.  res_sr is a one-deep shift register of res: it keeps
    // clocking in every state, so it always holds the value res had on the
    // previous cycle.  res_posedge is high for exactly one cycle, in the same
    // cycle res first reads 1 -- so val_in, which is valid when res rises, can
    // be used directly in that cycle.
    logic res_sr;
    wire  res_posedge = res && !res_sr;

    logic [BIT_WIDTH-1:0]      base_q;    // latched fixed physical frame_base
    assign frame_write_address = frame_base + (BIT_WIDTH'(wr_idx) << 2);
    logic [LAG_W-1:0]          tau;
    logic [IDX_W-1:0]          n;
    logic signed [BIT_WIDTH-1:0] ref_e;   // r(0)
    logic signed [BIT_WIDTH-1:0] lagged;  // lagged energy
    logic [BIT_WIDTH-1:0]      M;         // running mean of d
    logic [BIT_WIDTH-1:0]      dprime;    // d(lag) >> D_SHIFT
    logic [BIT_WIDTH-1:0]      prev1, prev2, best_beta;
    logic [LAG_W-1:0]          min_tau;
    logic signed [BIT_WIDTH:0]   snap_num;   // gamma - alpha
    logic signed [BIT_WIDTH+1:0] snap_den;   // 2 beta - alpha - gamma

    assign opcode = 1'b0;                 // division only

    // ------------------------------------------------------------------------
    // Address generation (one adder for frame and autocorrelation accesses)
    // ------------------------------------------------------------------------
    logic [IDX_W-1:0] idx;
    always_comb begin
        unique case (state)
            S_NREAD: idx = n;
            S_TREAD: idx = n - IDX_W'(tau);
            default: idx = eph ? (IDX_W'(FRAME_LEN - W) - IDX_W'(tau))
                               : (IDX_W'(FRAME_LEN)     - IDX_W'(tau));
        endcase
    end

    wire ac_sel = (state == S_INIT) || (state == S_RRD) || (state == S_RWR)
               || (state == S_REF)  || (state == S_R2RD);
    // Addition deliberately truncates to RING_W bits.  Since RING_LEN is a
    // power of 2, this wraps ONLY inside the dedicated ring allocation.
    // The autocorrelation region bypasses this translation completely.
    wire [RING_W-1:0] physical_idx = snapshot_idx + RING_W'(idx);
    wire [BIT_WIDTH-1:0] addr_next = ac_sel
        ? (AUTOCOR_BASE + (BIT_WIDTH'(tau) << 2))
        : (base_q + (BIT_WIDTH'(physical_idx) << 2));

    // ------------------------------------------------------------------------
    // Helpers
    // ------------------------------------------------------------------------
    wire signed [BIT_WIDTH-1:0] vin_s    = val_in;
    wire signed [2*BIT_WIDTH-1:0] corr_product_full = x_saved * vin_s;
    wire signed [BIT_WIDTH-1:0] corr_product = corr_product_full >>> ONES_BIT;
    wire signed [2*BIT_WIDTH-1:0] square_full = vin_s * vin_s;
    wire signed [BIT_WIDTH-1:0] square_q = square_full >>> ONES_BIT;
    wire                        vin_tiny = (vin_s <= TINY_S) && (vin_s >= -TINY_S);
    wire                        vin_zero = (val_in == '0);
    // |val_in|, saturating (|x| <= 1.0 in practice, so saturation never triggers)
    wire signed [BIT_WIDTH-1:0] vin_abs  = !vin_s[BIT_WIDTH-1] ? vin_s :
                                           (vin_s == SAT_MIN)  ? SAT_MAX : -vin_s;

    // d(lag) = ref + lagged - 2 r(lag), clamped at 0, then >> D_SHIFT
    wire signed [BIT_WIDTH+2:0] d_raw = (BIT_WIDTH+3)'(ref_e) + (BIT_WIDTH+3)'(lagged)
                                        - ((BIT_WIDTH+3)'(vin_s) <<< 1);
    wire [BIT_WIDTH-1:0] d_sh = d_raw[BIT_WIDTH-1+D_SHIFT : D_SHIFT];

    // mean update operands
    wire signed [BIT_WIDTH:0] bdiff = $signed({1'b0, dprime}) - $signed({1'b0, M});
    wire signed [BIT_WIDTH:0] m_n   = $signed({1'b0, M}) + (BIT_WIDTH+1)'(ans);

    // decision logic: d'(c-1)=prev2, d'(c)=prev1, d'(c+1)=dnew
    wire [BIT_WIDTH-1:0] ans_u   = ans[BIT_WIDTH-1] ? '0 : ans[BIT_WIDTH-1:0];
    wire [BIT_WIDTH-1:0] dnew    = zbyp ? BIT_WIDTH'(ONE_W) : ans_u;
    wire thr_hit = (prev1 < BIT_WIDTH'(THRESH_Q)) && (prev1 < prev2) && (prev1 < dnew);
    wire upd     = (tau == LAG_W'(TAU_MIN + 1)) || (prev1 < best_beta);
    wire signed [BIT_WIDTH:0]   sn_n = $signed({1'b0, dnew}) - $signed({1'b0, prev2});
    wire signed [BIT_WIDTH+1:0] sd_n = $signed({1'b0, prev1, 1'b0}) - $signed({2'b00, prev2})
                                       - $signed({2'b00, dnew});

    // interpolation operands: delta = (gamma-alpha) / (2 (2 beta - alpha - gamma))
    wire signed [BIT_WIDTH+2:0] den2   = {snap_den, 1'b0};
    wire signed [BIT_WIDTH-1:0] a24    = (den2 > SMAX) ? SMAX[BIT_WIDTH-1:0] :
                                         (den2 < -SMAX) ? (-SMAX[BIT_WIDTH-1:0]) : den2[BIT_WIDTH-1:0];
    wire signed [BIT_WIDTH:0]   a24_x  = (BIT_WIDTH+1)'(a24);                                 // sign-extended
    wire signed [BIT_WIDTH:0]   abs_n  = snap_num[BIT_WIDTH] ? -snap_num : snap_num;
    wire signed [BIT_WIDTH:0]   abs_a  = a24[BIT_WIDTH-1]    ? -a24_x    : a24_x;
    wire                        dneg   = snap_num[BIT_WIDTH] ^ a24[BIT_WIDTH-1];
    wire signed [BIT_WIDTH-1:0] tau_base = BIT_WIDTH'({min_tau, {ONES_BIT{1'b0}}});
    // delta = sign * (|num| / |den2|): the CORDIC returns the magnitude (>= 0),
    // clamp it to 0.5 and apply the sign here
    wire signed [BIT_WIDTH-1:0] dlt_mag = ans[BIT_WIDTH-1] ? '0 : (ans > HALF_W) ? HALF_W : ans;
    wire signed [BIT_WIDTH-1:0] dlt_c   = dneg ? -dlt_mag : dlt_mag;

    // ------------------------------------------------------------------------
    // Ring writer progress and frame scheduler. The actual write is performed
    // by Bandpass_Filter through the RAM1 arbiter and Memory_Controller.
    // The caller must pulse frame_write_complete only for ACKNOWLEDGED writes.
    // ------------------------------------------------------------------------
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            wr_idx <= '0;
            snapshot_idx <= '0;
            fill_count <= '0;
            hop_count <= '0;
            filled <= 1'b0;
            frame_ready <= 1'b0;
            frame_missed <= 1'b0;
            frame_overwrite <= 1'b0;
            snapshot_overwritten <= 1'b0;
            frame_seq <= '0;
        end else begin
            frame_ready <= 1'b0;
            if (start && !busy) begin
                // wr_idx always points at the first *unwritten* ring slot.
                snapshot_idx <= wr_idx - RING_W'(FRAME_LEN);
                snapshot_overwritten <= 1'b0;
            end
            if (frame_write_complete) begin
                // A snapshot is valid until wr_idx reaches its oldest sample.
                // This guard is independent of normal wrap-around events.
                if (busy && (wr_idx == snapshot_idx)) begin
                    frame_overwrite <= 1'b1;
                    snapshot_overwritten <= 1'b1;
                end
                wr_idx <= wr_idx + 1'b1;
                if (!filled) begin
                    if (fill_count == FILL_W'(FRAME_LEN-1)) begin
                        filled <= 1'b1;
                        frame_ready <= 1'b1;
                        frame_seq <= frame_seq + 1'b1;
                        hop_count <= '0;
                        if (busy) frame_missed <= 1'b1;
                    end else begin
                        fill_count <= fill_count + 1'b1;
                    end
                end else if (hop_count == HOP_W'(HOP-1)) begin
                    hop_count <= '0;
                    frame_ready <= 1'b1;
                    frame_seq <= frame_seq + 1'b1;
                    if (busy) frame_missed <= 1'b1;
                end else begin
                    hop_count <= hop_count + 1'b1;
                end
            end
        end
    end

    // ------------------------------------------------------------------------
    // Sequencer
    // ------------------------------------------------------------------------
    always_ff @(posedge clk) begin
        // A RAM request must remain asserted until THIS client receives its
        // acknowledgment.  A one-clock request pulse can be dropped when
        // RAM_Request_Arbiter is servicing another client (e.g. the filter).
        // The arbiter's WAIT_DROP state ensures the acknowledged request is
        // released before it accepts another one from the same client.
        start_read <= rst_n && pend && !res_posedge &&
                      ((state == S_NREAD) || (state == S_TREAD) ||
                       (state == S_RRD)   || (state == S_REF)   ||
                       (state == S_ERD)   || (state == S_R2RD));
        start_write <= rst_n && pend && !res_posedge &&
                       ((state == S_INIT) || (state == S_RWR));
        cordic_start <= 1'b0;
        res_sr       <= res;                  // runs in every state, including reset

        if (!rst_n) begin
            state <= S_IDLE;
            pend  <= 1'b0;
            busy  <= 1'b0;
            warm  <= 1'b0;
            pitch <= '0;
            period_samples <= '0;
            last_run_seq <= '0;
        end else begin
            unique case (state)
            // ----------------------------------------------------------------
            S_IDLE: begin
                if (start) begin
                    busy   <= 1'b1;
                    base_q <= frame_base;
                    last_run_seq <= frame_seq;
                    tau    <= '0;
                    sub    <= 1'b0;
                    eph    <= 1'b0;
                    M      <= '0;
                    prev1  <= BIT_WIDTH'(ONE_W);
                    if (warm && frame_seq == (last_run_seq + 32'd1)) begin
                        n     <= N_NEW0;
                        state <= S_NREAD;
                    end else begin
                        n     <= N_FIRST0;
                        state <= S_INIT;
                    end
                end
            end

            // ---- first run: clear the autocorrelation region ---------------
            S_INIT: begin
                if (!pend) begin
                    address     <= addr_next;
                    val_out     <= '0;
                    start_write <= 1'b1;
                    pend        <= 1'b1;
                end else if (res_posedge) begin
                    pend <= 1'b0;
                    if (tau == LAG_W'(TAU_MAX - 1)) begin
                        tau   <= '0;
                        state <= S_NREAD;
                    end else begin
                        tau <= tau + 1'b1;
                    end
                end
            end

            // ================= pass 1: autocorrelation update ===============
            S_NREAD: begin
                if (!pend) begin
                    address    <= addr_next;
                    start_read <= 1'b1;
                    pend       <= 1'b1;
                end else if (res_posedge) begin
                    pend <= 1'b0;
                    // FPGA fast path: use exact signed fixed-point product
                    // instead of two serial CORDIC divides per multiplication.
                    x_saved <= vin_s;
                    tau <= '0;
                    if (vin_tiny) state <= S_NNEXT;
                    else          state <= S_TREAD;
                end
            end

            S_NREC: begin
                A_out <= ans;                          // 1/|x[n]|, held for all lags
                state <= S_TREAD;
            end

            S_TREAD: begin
                if (!pend) begin
                    address    <= addr_next;
                    start_read <= 1'b1;
                    pend       <= 1'b1;
                end else if (res_posedge) begin
                    pend <= 1'b0;
                    if (vin_zero || corr_product == '0) begin
                        state <= S_TNEXT;
                    end else begin
                        val_out <= corr_product;
                        state <= S_RRD;
                    end
                end
            end

            S_TPROD: begin
                val_out <= nneg ? (-ans) : ans;        // apply the sign of x[n]
                state   <= state_t'((ans == '0) ? S_TNEXT : S_RRD);
            end

            S_RRD: begin
                if (!pend) begin
                    address    <= addr_next;
                    start_read <= 1'b1;
                    pend       <= 1'b1;
                end else if (res_posedge) begin
                    pend    <= 1'b0;
                    val_out <= sub ? (val_in - val_out) : (val_in + val_out);
                    state   <= S_RWR;
                end
            end

            S_RWR: begin
                if (!pend) begin
                    address     <= addr_next;
                    start_write <= 1'b1;
                    pend        <= 1'b1;
                end else if (res_posedge) begin
                    pend  <= 1'b0;
                    state <= S_TNEXT;
                end
            end

            S_TNEXT: begin
                if (tau == LAG_W'(TAU_MAX - 1)) begin
                    state <= S_NNEXT;
                end else begin
                    tau   <= tau + 1'b1;
                    state <= S_TREAD;
                end
            end

            S_NNEXT: begin
                if (!sub) begin
                    if (n == N_LAST) begin
                        if (!warm) begin
                            tau   <= '0;
                            state <= S_REF;
                        end else begin
                            sub   <= 1'b1;
                            n     <= N_OLD0;
                            state <= S_NREAD;
                        end
                    end else begin
                        n     <= n + 1'b1;
                        state <= S_NREAD;
                    end
                end else begin
                    if (n == N_OLD_LAST) begin
                        tau   <= '0;
                        state <= S_REF;
                    end else begin
                        n     <= n + 1'b1;
                        state <= S_NREAD;
                    end
                end
            end

            // ================= pass 2: squared difference + CMNDF ===========
            S_REF: begin
                if (!pend) begin
                    address    <= addr_next;
                    start_read <= 1'b1;
                    pend       <= 1'b1;
                end else if (res_posedge) begin
                    pend   <= 1'b0;
                    ref_e  <= vin_s;
                    lagged <= vin_s;
                    tau    <= LAG_W'(1);
                    eph    <= 1'b0;
                    state  <= S_ERD;
                end
            end

            S_ERD: begin                               // x[L-tau] then x[L-W-tau]
                if (!pend) begin
                    address    <= addr_next;
                    start_read <= 1'b1;
                    pend       <= 1'b1;
                end else if (res_posedge) begin
                    pend <= 1'b0;
                    if (vin_tiny) begin                // square is 0
                        if (!eph) begin
                            eph   <= 1'b1;
                            state <= S_ERD;
                        end else begin
                            state <= S_R2RD;
                        end
                    end else begin
                        // Square in Q12.12. One DSP multiply instead of two
                        // divisions. No sign handling needed for a square.
                        lagged <= eph ? (lagged + square_q) : (lagged - square_q);
                        if (!eph) begin
                            eph   <= 1'b1;
                            state <= S_ERD;
                        end else begin
                            state <= S_R2RD;
                        end
                    end
                end
            end

            S_EREC: begin
                A_out <= ans;                          // |x| / (1/|x|) = x^2
                B_out <= $signed(val_out);
                ret   <= S_ESQ;
                state <= S_CALL;
            end

            S_ESQ: begin
                lagged <= eph ? (lagged + ans) : (lagged - ans);
                if (!eph) begin
                    eph   <= 1'b1;
                    state <= S_ERD;
                end else begin
                    state <= S_R2RD;
                end
            end

            S_R2RD: begin
                if (!pend) begin
                    address    <= addr_next;
                    start_read <= 1'b1;
                    pend       <= 1'b1;
                end else if (res_posedge) begin
                    pend   <= 1'b0;
                    dprime <= d_raw[BIT_WIDTH+2] ? '0 : d_sh;
                    state  <= S_MEAN;
                end
            end

            S_MEAN: begin                              // M += (d - M) / tau
                if (bdiff == '0) begin
                    state <= state_t'((tau >= LAG_W'(TAU_MIN - 1)) ? S_DPR : S_P2NEXT);
                end else begin
                    A_out <= BIT_WIDTH'({tau, {ONES_BIT{1'b0}}});
                    B_out <= bdiff[BIT_WIDTH-1:0];
                    ret   <= S_MUPD;
                    state <= S_CALL;
                end
            end

            S_MUPD: begin
                // M is a denominator in S_DPR: keep it in [0, SAT_MAX]
                M     <= m_n[BIT_WIDTH]   ? '0 :
                         m_n[BIT_WIDTH-1] ? BIT_WIDTH'(SAT_MAX) : m_n[BIT_WIDTH-1:0];
                state <= state_t'((tau >= LAG_W'(TAU_MIN - 1)) ? S_DPR : S_P2NEXT);
            end

            S_DPR: begin                               // d' = d / M  (M == 0 -> 1.0)
                if (M == '0) begin
                    zbyp  <= 1'b1;
                    state <= S_DEC;
                end else begin
                    zbyp  <= 1'b0;
                    A_out <= $signed(M);
                    B_out <= $signed(dprime);
                    ret   <= S_DEC;
                    state <= S_CALL;
                end
            end

            S_DEC: begin
                prev2 <= prev1;
                prev1 <= dnew;
                state <= S_P2NEXT;
                if (tau >= LAG_W'(TAU_MIN + 1)) begin
                    if (upd || thr_hit) begin
                        best_beta <= prev1;
                        min_tau   <= tau - LAG_W'(1);
                        snap_num  <= sn_n;
                        snap_den  <= sd_n;
                    end
                    if (thr_hit) state <= S_FIN0;      // pass 1 already finished: stop here
                end
            end

            S_P2NEXT: begin
                if (tau == LAG_W'(TAU_MAX - 1)) begin  // no threshold crossing: global min
                    state <= S_FIN0;
                end else begin
                    tau   <= tau + 1'b1;
                    eph   <= 1'b0;
                    state <= S_ERD;
                end
            end

            // ---- parabolic interpolation + pitch ----------------------------
            S_FIN0: begin
                B_out <= FSQ_W;
                ret   <= S_FIN_P;
                state <= S_CALL;
                if (min_tau > LAG_W'(TAU_MIN) && snap_den != '0 && snap_num != '0) begin
                    if (abs_n >= abs_a) begin          // |delta| clamped to 0.5
                        A_out <= dneg ? (tau_base - HALF_W) : (tau_base + HALF_W);
                    end else begin                     // |delta| = |num| / |2 den|, sign in dlt_c
                        A_out <= abs_a[BIT_WIDTH-1:0];
                        B_out <= abs_n[BIT_WIDTH-1:0];
                        ret   <= S_FIN_D;
                    end
                end else begin
                    A_out <= tau_base;
                end
            end

            S_FIN_D: begin
                A_out <= tau_base + dlt_c;
                B_out <= FSQ_W;
                ret   <= S_FIN_P;
                state <= S_CALL;
            end

            S_FIN_P: begin
                // A_out still holds the refined period (tau + delta, Q12.12) that
                // the division above used as its denominator: pitch = FS / A_out.
                // period_samples = nearest whole sample: add 0.5, drop the fraction.
                period_samples <= PERIOD_W'((BIT_WIDTH'(A_out) + BIT_WIDTH'(HALF_W)) >> ONES_BIT);
                pitch          <= BIT_WIDTH'(ans_u) << FS_SHIFT;
                state          <= S_DONE;
            end

            // ----------------------------------------------------------------
            S_DONE: begin
                busy  <= 1'b0;
                warm  <= !snapshot_overwritten;
                if (snapshot_overwritten) begin
                    pitch <= '0;
                    period_samples <= '0;
                end
                state <= S_IDLE;
            end

            // ---- CORDIC call: operands are already in A_out / B_out ---------
            S_CALL: begin
                cordic_start <= 1'b1;
                saw_cordic_busy <= 1'b0;
                state        <= S_CWAIT;
            end

            S_CWAIT: begin
                if (cordic_busy) saw_cordic_busy <= 1'b1;
                if (saw_cordic_busy && !cordic_busy) state <= ret;
            end

            default: state <= S_IDLE;
            endcase
        end
    end

endmodule

