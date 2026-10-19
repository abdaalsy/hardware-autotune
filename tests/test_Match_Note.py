"""
cocotb testbench for Match_Note.

Run with pytest (builds the DUT and launches the simulator via test_runner):

    python -m pytest test_Match_Note.py
    SIM=verilator python -m pytest test_Match_Note.py      # default is icarus
    SEED=1234 python -m pytest test_Match_Note.py -s       # reproduce a run
    IN_FRAC_BITS=0 python -m pytest test_Match_Note.py     # integer-Hz pitch/table

What is modelled
----------------
* Memory      : notes array (12-TET, A4 = 440 Hz) at NOTES_ADDR_BASE, 4 byte
                stride.  Every read is answered with random latency and a
                1-2 cycle wide `res` pulse.  `val_in` carries junk except when
                `res` rises, so a DUT that samples early gets caught.
* CORDIC      : ideal 12.12 divider (B/A, truncated toward zero) with random
                busy time.  `ans` carries junk while busy.  Opcode must be 0.

What is checked
---------------
* Every read address is 4-byte aligned, in bounds, and maps to exactly the
  index a binary search would read next (mid, mid, ..., then lo, then hi).
* start_read / cordic_start are single-cycle pulses, never issued while a
  previous request is still outstanding; start_write never fires.
* CORDIC operands are true 12.12 values (pitch / table entry << SHIFT_UP).
* Final `shift` equals note/pitch (12.12) for the closest note on a log scale,
  where the closest note comes from an independent brute-force search over the
  whole table (not from a binary search).  Pitches within 1 cent of the
  geometric mean of two notes may legally resolve either way (CORDIC results
  are quantised to 2^-12), so both neighbours are accepted there.

Environment variables
---------------------
IN_FRAC_BITS    fractional bits of pitch / table format (default 12, must match
                the DUT parameter; the Makefile passes it to both).
SEED            RNG seed (default: random, printed at start of each test).
"""

import math
import os
import random
from collections import deque

import cocotb
from pathlib import Path
from cocotb.clock import Clock
from cocotb_tools.runner import get_runner
from cocotb.triggers import FallingEdge

# -----------------------------------------------------------------------------
# Configuration (keep in sync with Match_Note.sv)
# -----------------------------------------------------------------------------
BIT_WIDTH = 24
MASK = (1 << BIT_WIDTH) - 1
CORDIC_FRAC = 12
IN_FRAC = int(os.environ.get("IN_FRAC_BITS", "12"))
SHIFT_UP = CORDIC_FRAC - IN_FRAC
NOTES_ADDR_BASE_DEFAULT = 0x001000       # fallback if the DUT can't be probed
OP_DIV = 0

PITCH_MIN_HZ = 50.0
PITCH_MAX_HZ = 800.0
TIE_TOL_NEPER = math.log(2) / 1200.0     # 1 cent

TXN_TIMEOUT_CYCLES = 20000
LOG_TRAFFIC = os.environ.get("LOG_TRAFFIC", "1") != "0"   # set LOG_TRAFFIC=0 to silence per-read/CORDIC logs


# -----------------------------------------------------------------------------
# pytest entry point: builds the DUT and runs the cocotb tests below
# -----------------------------------------------------------------------------
def test_runner():
    sim = os.getenv("SIM", "icarus")
    proj_root = Path(__file__).resolve().parent.parent

    # src/Match_Note.sv like the other modules; fall back to next to this file
    dut_file = proj_root / "src" / "Match_Note.sv"
    if not dut_file.exists():
        dut_file = proj_root / "Match_Note.sv"
    sources = [str(dut_file)]

    runner = get_runner(sim)
    runner.build(
        sources=sources,
        hdl_toplevel="Match_Note",
        parameters={"IN_FRAC_BITS": IN_FRAC},   # same value the tests below use
        always=True,
        timescale=("1ns", "1ps"),
        waves=True,
    )
    runner.test(
        hdl_toplevel="Match_Note",
        test_module="test_Match_Note",
        test_dir=str(proj_root),
        waves=True,
    )


# -----------------------------------------------------------------------------
# Small helpers
# -----------------------------------------------------------------------------
def to_signed(v: int) -> int:
    v &= MASK
    return v - (1 << BIT_WIDTH) if v & (1 << (BIT_WIDTH - 1)) else v


def cordic_div(b: int, a: int) -> int:
    """Ideal 12.12 divider: B/A, truncated toward zero, operands in 12.12."""
    q = (abs(b) << CORDIC_FRAC) // abs(a)
    return -q if (b < 0) != (a < 0) else q


def make_notes():
    """12-TET table, MIDI 21 (A0, 27.5 Hz) .. 95 (B6, ~1975 Hz), raw values
    in the pitch/table format (IN_FRAC fractional bits)."""
    notes = []
    for midi in range(21, 96):
        hz = 440.0 * 2.0 ** ((midi - 69) / 12.0)
        notes.append(int(round(hz * (1 << IN_FRAC))))
    assert all((n << SHIFT_UP) < (1 << (BIT_WIDTH - 1)) for n in notes), \
        "table does not fit in signed 12.12 - lower the top note"
    assert notes == sorted(notes)
    return notes


def hz_to_raw(hz: float) -> int:
    return int(round(hz * (1 << IN_FRAC)))


def raw_to_hz(raw: int) -> float:
    return raw / (1 << IN_FRAC)


def expected_read_indices(notes, n, pitch_raw):
    """Index sequence a binary search over notes[:n] should read."""
    lo, hi = 0, n - 1
    reads = []
    while hi - lo > 1:
        mid = lo + ((hi - lo) >> 1)
        reads.append(mid)
        if notes[mid] <= pitch_raw:
            lo = mid
        else:
            hi = mid
    reads += [lo, hi]
    return reads


def closest_candidates(notes, n, pitch_raw):
    """Independent brute force: indices of the closest note(s) on a log scale.
    More than one index is returned only if the runner-up is within 1 cent."""
    dist = sorted((abs(math.log(pitch_raw / notes[i])), i) for i in range(n))
    cands = [dist[0][1]]
    if n > 1 and dist[1][0] - dist[0][0] < TIE_TOL_NEPER:
        cands.append(dist[1][1])
    return cands


# -----------------------------------------------------------------------------
# Memory + CORDIC models and scoreboard
# -----------------------------------------------------------------------------
class Harness:
    def __init__(self, dut, notes):
        self.dut = dut
        self.notes = notes
        self.base = self._probe_base()
        self.errors = []
        self.txn = 0

        # per-transaction expectations
        self.n = 0
        self.exp_reads = deque()
        self.valid_operands = set()
        self.mem_reads = 0              # reads seen in current transaction
        self.cordic_calls = 0           # CORDIC calls seen in current transaction

    # ---- setup -----------------------------------------------------------
    def _probe_base(self):
        try:
            base = int(self.dut.notes_addr_base.value)
            self.dut._log.info(f"notes_addr_base read from DUT: 0x{base:06x}")
            return base
        except Exception:
            self.dut._log.warning(
                f"could not probe notes_addr_base, assuming 0x{NOTES_ADDR_BASE_DEFAULT:06x}")
            return NOTES_ADDR_BASE_DEFAULT

    def err(self, msg):
        self.errors.append(msg)
        self.dut._log.error(msg)

    async def reset(self):
        d = self.dut
        d.rst_n.value = 0
        d.start.value = 0
        d.num_notes.value = 0
        d.pitch.value = 0
        d.val_in.value = 0
        d.res.value = 0
        d.ans.value = 0
        d.cordic_busy.value = 0
        for _ in range(4):
            await FallingEdge(d.clk)
        d.rst_n.value = 1
        for _ in range(2):
            await FallingEdge(d.clk)

    def start_models(self):
        self._mem_task = cocotb.start_soon(self._mem_model())
        self._cordic_task = cocotb.start_soon(self._cordic_model())
        self._mon_task = cocotb.start_soon(self._misc_monitor())

    def stop_models(self):
        for t in (self._mem_task, self._cordic_task, self._mon_task):
            t.cancel()

    # ---- memory model ------------------------------------------------------
    def _on_read(self, addr):
        """Check an observed read address; return the value to respond with."""
        junk = random.getrandbits(BIT_WIDTH)
        off = addr - self.base
        self.mem_reads += 1
        exp = self.exp_reads[0] if self.exp_reads else None
        idx = off // 4 if off >= 0 and off % 4 == 0 else None

        if LOG_TRAFFIC:
            exp_s = "none" if exp is None else f"{exp} (0x{self.base + 4 * exp:06x})"
            self.dut._log.info(
                f"txn {self.txn} MEM READ #{self.mem_reads}: address=0x{addr:06x} -> "
                f"index {idx if idx is not None else 'n/a'}, expected index {exp_s}")

        if off < 0 or off % 4 != 0:
            self.err(f"read #{self.mem_reads}: address 0x{addr:06x} is not "
                     f"base(0x{self.base:06x}) + 4*k")
            return junk
        if idx >= self.n:
            self.err(f"read #{self.mem_reads}: address 0x{addr:06x} -> index {idx}, "
                     f"out of bounds (num_notes={self.n})")
            return junk
        if not self.exp_reads:
            self.err(f"read #{self.mem_reads}: unexpected extra read of index {idx} "
                     f"(address 0x{addr:06x})")
        else:
            self.exp_reads.popleft()
            if idx != exp:
                self.err(f"read #{self.mem_reads}: address 0x{addr:06x} -> index {idx}, "
                         f"but the binary search should read index {exp} "
                         f"(expected address 0x{self.base + 4 * exp:06x})")
        return self.notes[idx]

    async def _mem_model(self):
        d = self.dut
        prev_sr = 0
        pending = None          # [cycles_left, value, res_hold] for an accepted read
        res_high = False
        res_hold = 0            # cycles of res left to hold
        low_edges = 1           # clock edges res has been low (need >= 1 between pulses)
        while True:
            await FallingEdge(d.clk)
            sr = int(d.start_read.value)

            # 1. end of a res pulse / low-time bookkeeping
            if res_high:
                res_hold -= 1
                if res_hold == 0:
                    d.res.value = 0
                    d.val_in.value = random.getrandbits(BIT_WIDTH)   # junk again
                    res_high = False
                    low_edges = 0
            else:
                low_edges += 1

            # 2. a new read request?
            if sr and prev_sr:
                self.err("start_read held high for more than one cycle")
            elif sr:
                value = self._on_read(int(d.address.value))
                if pending is not None:
                    self.err("start_read issued while a previous read is outstanding")
                pending = [random.randint(0, 4), value, random.randint(1, 2)]
                d.val_in.value = random.getrandbits(BIT_WIDTH)       # junk until response
            prev_sr = sr

            # 3. respond: res must have been low for a full cycle so the DUT
            #    sees a genuine rising edge even for back-to-back reads.
            if pending is not None:
                if pending[0] > 0:
                    pending[0] -= 1
                if pending[0] == 0 and not res_high and low_edges >= 1:
                    d.val_in.value = pending[1]
                    d.res.value = 1
                    res_high = True
                    res_hold = pending[2]
                    pending = None

    # ---- CORDIC model -------------------------------------------------------
    async def _cordic_model(self):
        d = self.dut
        prev_cs = 0
        busy_cnt = 0
        result = 0
        while True:
            await FallingEdge(d.clk)
            cs = int(d.cordic_start.value)

            if busy_cnt > 0:
                busy_cnt -= 1
                if busy_cnt == 0:
                    d.cordic_busy.value = 0
                    d.ans.value = result & MASK

            if cs and prev_cs:
                self.err("cordic_start held high for more than one cycle")
            elif cs:
                self.cordic_calls += 1
                opc = int(d.opcode.value)
                a = to_signed(int(d.A_out.value))
                b = to_signed(int(d.B_out.value))
                if busy_cnt > 0:
                    self.err("cordic_start issued while CORDIC is still busy")
                if opc != OP_DIV:
                    self.err(f"CORDIC call #{self.cordic_calls}: opcode={opc}, expected "
                             f"{OP_DIV} (divide)")
                if a not in self.valid_operands or b not in self.valid_operands:
                    self.err(f"CORDIC call #{self.cordic_calls}: operands A={a} B={b} "
                             f"are not pitch/table values in 12.12 "
                             f"(pitch/table << {SHIFT_UP})")
                if a == 0:
                    self.err(f"CORDIC call #{self.cordic_calls}: division by zero")
                    result = 0
                else:
                    result = cordic_div(b, a)
                if LOG_TRAFFIC:
                    self.dut._log.info(
                        f"txn {self.txn} CORDIC #{self.cordic_calls}: opcode={opc} "
                        f"B/A = {b}/{a} -> {result} ({result / (1 << CORDIC_FRAC):.5f})")
                busy_cnt = random.randint(2, 8)
                d.cordic_busy.value = 1
                d.ans.value = random.getrandbits(BIT_WIDTH)   # junk while busy
            prev_cs = cs

    # ---- misc protocol monitor -------------------------------------------
    async def _misc_monitor(self):
        d = self.dut
        while True:
            await FallingEdge(d.clk)
            if int(d.start_write.value):
                self.err("start_write asserted (should be unused)")

    # ---- one transaction ----------------------------------------------------
    async def run(self, n, pitch_raw):
        """Run one match.  Returns the observed `shift` (signed)."""
        d = self.dut
        self.errors.clear()
        self.txn += 1
        self.n = n
        self.mem_reads = 0
        self.cordic_calls = 0
        if n > 0 and pitch_raw > 0:
            self.exp_reads = deque(expected_read_indices(self.notes, n, pitch_raw))
        else:
            self.exp_reads = deque()
        self.valid_operands = {to_signed(pitch_raw << SHIFT_UP)} | \
                              {to_signed(x << SHIFT_UP) for x in self.notes[:max(n, 0)]}

        if LOG_TRAFFIC:
            d._log.info(f"txn {self.txn} START: num_notes={n} pitch={raw_to_hz(pitch_raw):.4f} Hz "
                        f"(raw {pitch_raw}), expected read indices {list(self.exp_reads)}")
        d.num_notes.value = n
        d.pitch.value = pitch_raw
        d.start.value = 1
        await FallingEdge(d.clk)
        d.start.value = 0
        if int(d.busy.value) != 1:
            self.err("busy did not assert in the cycle after start")

        for _ in range(TXN_TIMEOUT_CYCLES):
            await FallingEdge(d.clk)
            if int(d.busy.value) == 0:
                break
        else:
            raise AssertionError(f"timeout: busy never fell (n={n}, pitch_raw={pitch_raw})")

        if self.exp_reads:
            self.err(f"search finished but expected reads never happened: "
                     f"{list(self.exp_reads)}")
        # let any trailing stray pulse show up
        for _ in range(3):
            await FallingEdge(d.clk)
        shift = to_signed(int(d.shift.value))
        if LOG_TRAFFIC:
            d._log.info(f"txn {self.txn} DONE: {self.mem_reads} memory reads, "
                        f"{self.cordic_calls} CORDIC calls, shift={shift} "
                        f"({shift / (1 << CORDIC_FRAC):.5f})")
        return shift

    def check_clean(self, ctx):
        assert not self.errors, f"[{ctx}]\n  " + "\n  ".join(self.errors)


# -----------------------------------------------------------------------------
# Shared test plumbing
# -----------------------------------------------------------------------------
async def setup(dut):
    seed = int(os.environ.get("SEED") or random.randrange(1 << 30))
    random.seed(seed)
    dut._log.info(f"SEED={seed}  IN_FRAC_BITS={IN_FRAC}")
    try:
        cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    except TypeError:                       # cocotb 1.x
        cocotb.start_soon(Clock(dut.clk, 10, units="ns").start())
    h = Harness(dut, make_notes())
    await h.reset()
    h.start_models()
    return h


async def check_match(h, n, pitch_raw, ctx=""):
    """Run one transaction and compare shift against the brute-force closest."""
    shift = await h.run(n, pitch_raw)
    where = f"{ctx} n={n} pitch={raw_to_hz(pitch_raw):.4f} Hz (raw {pitch_raw})"
    h.check_clean(where)

    cands = closest_candidates(h.notes, n, pitch_raw)
    allowed = {i: cordic_div(h.notes[i] << SHIFT_UP, pitch_raw << SHIFT_UP) for i in cands}
    assert shift in allowed.values(), (
        f"[{where}] shift={shift} ({shift / (1 << CORDIC_FRAC):.5f}), expected one of "
        + ", ".join(f"{v} (note idx {i} = {raw_to_hz(h.notes[i]):.3f} Hz)"
                    for i, v in allowed.items()))
    assert h.cordic_calls in (1, 3), f"[{where}] unexpected CORDIC call count {h.cordic_calls}"
    return shift


# -----------------------------------------------------------------------------
# Tests
# -----------------------------------------------------------------------------
@cocotb.test()
async def test_random_pitches(dut):
    """Random pitches 50-800 Hz against the full table."""
    h = await setup(dut)
    n = len(h.notes)
    for i in range(500):
        hz = random.uniform(PITCH_MIN_HZ, PITCH_MAX_HZ)
        await check_match(h, n, hz_to_raw(hz), ctx=f"iter {i}")
    h.stop_models()


@cocotb.test()
async def test_exact_note_hits(dut):
    """Pitch equal to a table entry: closest is that note and shift == 1.0."""
    h = await setup(dut)
    n = len(h.notes)
    hit = 0
    for idx, raw in enumerate(h.notes):
        if not (PITCH_MIN_HZ <= raw_to_hz(raw) <= PITCH_MAX_HZ):
            continue
        shift = await check_match(h, n, raw, ctx=f"exact idx {idx}")
        assert shift == 1 << CORDIC_FRAC, f"exact hit idx {idx}: shift={shift}, expected 1.0"
        hit += 1
    dut._log.info(f"{hit} exact hits checked")
    h.stop_models()


@cocotb.test()
async def test_table_sizes(dut):
    """Different num_notes, including tiny tables and pitches outside the
    covered range (closest note is then an end of the table)."""
    h = await setup(dut)
    sizes = [1, 2, 3, 4, 5, 7, 8, 9, 13, 16, 17, 31, 32, 33, 50, len(h.notes)]
    for n in sizes:
        for i in range(25):
            hz = random.uniform(PITCH_MIN_HZ, PITCH_MAX_HZ)
            await check_match(h, n, hz_to_raw(hz), ctx=f"size sweep iter {i}")
    h.stop_models()


@cocotb.test()
async def test_degenerate_inputs(dut):
    """num_notes == 0 or pitch == 0: shift is 0 and no memory/CORDIC traffic."""
    h = await setup(dut)
    for n, pitch_raw in [(0, hz_to_raw(440.0)), (len(h.notes), 0), (0, 0)]:
        shift = await h.run(n, pitch_raw)
        h.check_clean(f"degenerate n={n} pitch_raw={pitch_raw}")
        assert shift == 0, f"degenerate n={n} pitch_raw={pitch_raw}: shift={shift}, expected 0"
        assert h.mem_reads == 0 and h.cordic_calls == 0, \
            f"degenerate n={n} pitch_raw={pitch_raw}: unexpected memory/CORDIC activity"
    # and the DUT must still work afterwards
    await check_match(h, len(h.notes), hz_to_raw(261.63), ctx="after degenerate")
    h.stop_models()
