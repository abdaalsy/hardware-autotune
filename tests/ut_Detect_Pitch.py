"""
cocotb test for Detect_Pitch (YIN, streaming, incremental autocorrelation).

Why a small configuration
-------------------------
With the production constants (FRAME_LEN=1920, W=900, TAU_MAX=960) the first
run does ~860k products, each needing a CORDIC division plus memory traffic:
~40 million cycles.  That is not a deadlock, it is just far beyond what a
cocotb simulation can do.  This test therefore builds the DUT with the small
configuration in CONFIG (~10^5 cycles per run) -- the algorithm is identical.
It keeps the real sample rate (FS = 48000), so pitch / period are checked at
the rate the design is used at; only the frame / window / lag range are shrunk
(lags 32..111 -> pitches of ~430 Hz and up).

What is modelled
----------------
* Memory  : dict-backed.  `res` idles HIGH (like the real memory controller).
            After a request it stays high for 1-3 more cycles (arbitration),
            then drops for 1-3 cycles, then rises with val_in valid -- the DUT
            must wait for that rising edge.  A DUT that only looks at the LEVEL
            of res sees the stale idle-high value and reads junk.
* CORDIC  : ideal Q12.12 divider (B/A, truncated), 3-10 busy cycles.
* Both responders sample on the falling clock edge (no races with the DUT).

What is checked
---------------
* Every memory address is 4-byte aligned and inside the frame buffer or the
  autocorrelation region; no overlapping requests; CORDIC opcode is divide and
  never divides by zero, and the DENOMINATOR (A_out) is never negative (the real
  CORDIC engine can't handle that; numerators may be signed).
* The autocorrelation region is junk before the first run and must be zeroed /
  rebuilt by the DUT: r(lag) in memory must match a floating point reference
  after EVERY run (this also checks the incremental add-new / remove-old path
  on the 2nd and 3rd run of each tone).
* Detected pitch is within PITCH_TOL of the tone frequency after every run.
* period_samples is BIT_WIDTH/2 bits wide and holds the WHOLE number of samples
  per period, ROUNDED to the nearest sample: it must be within PITCH_TOL of
  FS/tone (plus half a sample) and equal round(FS / pitch).  FS/pitch is only
  uncertain by ~1e-5 (pitch quantisation), so ROUND_TOL = 0.02 % is used and
  only values that fall that close to a .5 boundary may round either way.

Between tones the DUT is reset, because the frame contract (each run sees the
previous frame shifted by HOP samples) is broken when the tone changes.

    python tests/ut_Detect_Pitch.py          # from the project root
"""

import os
import math
import random
import cocotb
from pathlib import Path
from cocotb.clock import Clock
from cocotb_tools.runner import get_runner
from cocotb.triggers import FallingEdge

# -----------------------------------------------------------------------------
# DUT configuration (passed to the DUT as parameters AND used by the checks)
# -----------------------------------------------------------------------------
CONFIG = dict(HOP=16, FRAME_LEN=224, W=96, TAU_MIN=32, TAU_MAX=112, FS=48000)
HOP, FRAME_LEN, W = CONFIG["HOP"], CONFIG["FRAME_LEN"], CONFIG["W"]
TAU_MIN, TAU_MAX, FS = CONFIG["TAU_MIN"], CONFIG["TAU_MAX"], CONFIG["FS"]

FRAME_BASE = 0x000000
NEG_DEN_CALLS = {}               # DUT return-state code -> number of negative-denominator calls
AUTOCOR_BASE = 0x004000          # DUT default
TONES_HZ = [500.0, 700.0, 1200.0]         # periods 96, 68.6, 40 samples at 48 kHz
RUNS_PER_TONE = 3                # 1 first run + 2 incremental runs
AMPLITUDE = 0.8
PITCH_TOL = 0.03                 # 3 %
ROUND_TOL = 0.0002              # uncertainty of FS/pitch vs the DUT's internal period, 0.02 %
AUTOCOR_TOL = 0.05               # absolute, in units of the normalised signal^2

# Fixed-point (Q12.12, 24 bit)
ONES_BIT = 12
SCALE = 1 << ONES_BIT
MASK = (1 << 24) - 1


def float_to_q12_12(val):
    scaled = round(val * SCALE)
    scaled = max(-(1 << 23), min((1 << 23) - 1, scaled))
    return scaled & MASK


def q12_12_to_float(val):
    if val & (1 << 23):
        val -= (1 << 24)
    return val / SCALE


def to_signed(v):
    v &= MASK
    return v - (1 << 24) if v & (1 << 23) else v


# -----------------------------------------------------------------------------
# Background responders
# -----------------------------------------------------------------------------
def addr_ok(addr):
    if addr % 4:
        return False
    in_frame = FRAME_BASE <= addr < FRAME_BASE + 4 * FRAME_LEN
    in_ac = AUTOCOR_BASE <= addr < AUTOCOR_BASE + 4 * TAU_MAX
    return in_frame or in_ac


async def memory_responder(dut, memory, errors, stats):
    """Memory controller model: res idles high; after a request it stays high
    for a few cycles, drops, then rises again with val_in valid."""
    dut.res.value = 1
    pending = None                       # [cycles_until_drop, cycles_low, value]
    while True:
        await FallingEdge(dut.clk)

        if pending is not None:
            if pending[0] > 0:                       # still idle-high, not yet serving
                pending[0] -= 1
                if pending[0] == 0:
                    dut.res.value = 0
            else:                                    # res low: serving
                pending[1] -= 1
                if pending[1] == 0:
                    dut.val_in.value = pending[2]    # data valid when res rises
                    dut.res.value = 1
                    pending = None

        rd = int(dut.start_read.value)
        wr = int(dut.start_write.value)
        if rd or wr:
            addr = int(dut.address.value)
            if rd and wr:
                errors.append("start_read and start_write asserted together")
            if pending is not None:
                errors.append(f"request to 0x{addr:06x} while a previous one is outstanding")
            if not addr_ok(addr):
                errors.append(f"bad memory address 0x{addr:06x} (unaligned or outside frame/autocorr regions)")
            if rd:
                if addr not in memory:
                    errors.append(f"read of never-written address 0x{addr:06x}")
                val = memory.get(addr, 0)
                stats["reads"] += 1
            else:
                memory[addr] = int(dut.val_out.value)
                val = random.getrandbits(24)
                stats["writes"] += 1
            dut.val_in.value = random.getrandbits(24)      # junk until the response
            pending = [random.randint(1, 3), random.randint(1, 3), val & MASK]


async def cordic_responder(dut, errors, stats):
    """Ideal Q12.12 divider, B/A truncated toward zero.  ans is junk while busy."""
    dut.cordic_busy.value = 0
    cnt = 0
    result = 0
    while True:
        await FallingEdge(dut.clk)

        if cnt > 0:
            cnt -= 1
            if cnt == 0:
                dut.ans.value = result & MASK
                dut.cordic_busy.value = 0

        if int(dut.cordic_start.value):
            a = to_signed(int(dut.A_out.value))
            b = to_signed(int(dut.B_out.value))
            if int(dut.opcode.value) != 0:
                errors.append("CORDIC opcode is not divide (0)")
            if cnt > 0:
                errors.append("cordic_start while CORDIC busy")
            if a < 0:
                code = int(dut.ret.value)
                NEG_DEN_CALLS[code] = NEG_DEN_CALLS.get(code, 0) + 1
            if a == 0:
                errors.append("CORDIC division by zero")
                result = 0
            else:
                q = (abs(b) << ONES_BIT) // abs(a)
                result = -q if (a < 0) != (b < 0) else q
                result = max(-(1 << 23), min((1 << 23) - 1, result))
            stats["cordic"] += 1
            dut.ans.value = random.getrandbits(24)
            dut.cordic_busy.value = 1
            cnt = random.randint(3, 10)


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
async def reset_dut(dut):
    dut.rst_n.value = 0
    dut.start.value = 0
    dut.frame_base.value = FRAME_BASE
    for _ in range(5):
        await FallingEdge(dut.clk)
    dut.rst_n.value = 1
    for _ in range(3):
        await FallingEdge(dut.clk)


def reference_autocorr(frame_q):
    """r(lag) over the last W samples of the frame, using the quantised samples."""
    x = [v / SCALE for v in frame_q]
    return [sum(x[n] * x[n - lag] for n in range(FRAME_LEN - W, FRAME_LEN))
            for lag in range(TAU_MAX)]


async def run_detect(dut, memory, errors, stats, frame_q, label):
    """Load one frame, run the detector, return (pitch_hz, whole-sample period, [r(lag) from memory])."""
    for i, v in enumerate(frame_q):
        memory[FRAME_BASE + 4 * i] = v & MASK
    errors.clear()
    NEG_DEN_CALLS.clear()
    for k in stats:
        stats[k] = 0

    dut.frame_base.value = FRAME_BASE
    dut.start.value = 1
    await FallingEdge(dut.clk)
    dut.start.value = 0
    assert int(dut.busy.value) == 1, f"[{label}] busy did not assert after start"

    timeout = 2_000_000
    cycles = 0
    while int(dut.busy.value) == 1:
        await FallingEdge(dut.clk)
        cycles += 1
        if cycles % 50000 == 0:
            dut._log.info(f"[{label}] ... {cycles} cycles, n={int(dut.n.value)} "
                          f"tau={int(dut.tau.value)} state={int(dut.state.value)}")
        if cycles > timeout:
            raise AssertionError(
                f"[{label}] timeout after {timeout} cycles: n={int(dut.n.value)} "
                f"tau={int(dut.tau.value)} state={int(dut.state.value)}")

    assert not errors, f"[{label}] protocol errors:\n  " + "\n  ".join(errors[:10])
    assert not NEG_DEN_CALLS, (
        f"[{label}] CORDIC called with a NEGATIVE denominator {sum(NEG_DEN_CALLS.values())} times "
        f"(count per DUT return-state code: {NEG_DEN_CALLS})")

    pitch_hz = q12_12_to_float(int(dut.pitch.value))
    assert len(dut.period_samples) == 24 // 2, "period_samples should be BIT_WIDTH/2 bits wide"
    period = int(dut.period_samples.value)                  # whole samples per period
    ac = [q12_12_to_float(memory[AUTOCOR_BASE + 4 * lag]) for lag in range(TAU_MAX)]
    dut._log.info(f"[{label}] {cycles} cycles, {stats['reads']} reads, {stats['writes']} writes, "
                  f"{stats['cordic']} CORDIC calls -> pitch {pitch_hz:.2f} Hz, "
                  f"period {period} samples")
    return pitch_hz, period, ac


# -----------------------------------------------------------------------------
# Test
# -----------------------------------------------------------------------------
@cocotb.test()
async def test_detect_pitch_frequencies(dut):
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())   # 50 MHz

    memory = {}
    errors = []
    stats = {"reads": 0, "writes": 0, "cordic": 0}
    cocotb.start_soon(memory_responder(dut, memory, errors, stats))
    cocotb.start_soon(cordic_responder(dut, errors, stats))

    dut.rst_n.value = 0
    dut.start.value = 0
    dut.frame_base.value = FRAME_BASE
    dut.val_in.value = 0
    dut.ans.value = 0
    dut.cordic_busy.value = 0

    for tone in TONES_HZ:
        # continuous tone; run k sees samples [k*HOP, k*HOP + FRAME_LEN)
        total = FRAME_LEN + HOP * RUNS_PER_TONE
        sig_q = [to_signed(float_to_q12_12(AMPLITUDE * math.sin(2 * math.pi * tone * i / FS)))
                 for i in range(total)]

        await reset_dut(dut)                       # new tone = new first run
        for lag in range(TAU_MAX):                 # junk the DUT must overwrite
            memory[AUTOCOR_BASE + 4 * lag] = random.getrandbits(24)

        for k in range(RUNS_PER_TONE):
            frame_q = sig_q[k * HOP: k * HOP + FRAME_LEN]
            label = f"{tone:.0f} Hz run {k}" + (" (first)" if k == 0 else " (incremental)")
            pitch_hz, period, ac = await run_detect(dut, memory, errors, stats, frame_q, label)

            ref = reference_autocorr(frame_q)
            worst = max(range(TAU_MAX), key=lambda l: abs(ac[l] - ref[l]))
            err = abs(ac[worst] - ref[worst])
            exp_period = FS / tone
            dut._log.info(f"[{label}] worst autocorr error {err:.4f} at lag {worst} "
                          f"(r={ref[worst]:.3f}); pitch error {100 * (pitch_hz - tone) / tone:+.2f} %; "
                          f"period_samples {period} vs FS/tone = {exp_period:.3f}, FS/pitch = {FS / pitch_hz:.3f}")
            assert err <= AUTOCOR_TOL, (
                f"[{label}] r({worst}) = {ac[worst]:.4f} in memory, reference {ref[worst]:.4f}")
            assert abs(pitch_hz - tone) <= PITCH_TOL * tone, (
                f"[{label}] pitch {pitch_hz:.2f} Hz, expected {tone:.1f} Hz +/- {100 * PITCH_TOL:.0f}%")
            assert abs(period - exp_period) <= PITCH_TOL * exp_period + 0.5, (
                f"[{label}] period_samples {period}, expected about {exp_period:.3f} samples")
            # nearest whole sample of the refined period that produced `pitch`
            p_from_pitch = FS / pitch_hz
            lo = math.floor(p_from_pitch * (1 - ROUND_TOL) + 0.5)
            hi = math.floor(p_from_pitch * (1 + ROUND_TOL) + 0.5)
            assert lo <= period <= hi, (
                f"[{label}] period_samples {period} is not round(FS/pitch) "
                f"= round({p_from_pitch:.4f}) = {lo}..{hi}")

    dut._log.info("All tones passed")


# -----------------------------------------------------------------------------
# Runner
# -----------------------------------------------------------------------------
def test_runner():
    sim = os.getenv("SIM", "icarus")
    sources = ["src/Detect_Pitch.sv"]

    runner = get_runner(sim)
    runner.build(
        sources=sources,
        hdl_toplevel="Detect_Pitch",
        parameters=CONFIG,                 # small configuration, see top of file
        always=True,
        timescale=("1ns", "1ps"),
        waves=True,
    )
    runner.test(
        hdl_toplevel="Detect_Pitch",
        test_module="ut_Detect_Pitch",
        test_dir=str(Path(__file__).resolve().parent),
        waves=True,
    )


if __name__ == "__main__":
    test_runner()
