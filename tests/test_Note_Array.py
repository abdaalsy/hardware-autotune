"""Cocotb tests for Note_Array.sv (C chromatic, C major, A natural minor).

The DUT is tested on its own. Two independent concurrent coroutines
emulate the memory controller and the fixed-point CORDIC divider, so
neither interface request can be missed while the scoreboard is checking
another interface.

Run from the project root:
    SIM=icarus python -m pytest tests/test_Note_Array.py -s
"""

import os
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.queue import Queue
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge, Timer, with_timeout
from cocotb_tools.runner import get_runner


BIT_WIDTH = 24
FRACTIONAL_BITS = 12
SCALE = 1 << FRACTIONAL_BITS
MAX_FREQ_HZ = 800.0
MEMORY_BASE = 0x10
ADDRESS_STRIDE = 4

# Semitone intervals, starting at the root.
MAJOR_STEPS = (2, 2, 1, 2, 2, 2, 1)
MINOR_STEPS = (2, 1, 2, 2, 1, 2, 2)

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
EXPECTED_COUNTS = {
    "C chromatic": 56,
    "C major": 33,
    "A natural minor": 28,
}


def test_runner():
    sim = os.getenv("SIM", "icarus")
    tests_dir = Path(__file__).resolve().parent
    proj_root = tests_dir.parent

    runner = get_runner(sim)
    runner.build(
        sources=[proj_root / "src" / "Note_Array.sv"],
        hdl_toplevel="Note_Array",
        parameters={"BIT_WIDTH": BIT_WIDTH, "FRACTIONAL_BITS": FRACTIONAL_BITS},
        always=True,
        timescale=("1ns", "1ps"),
        waves=True,
    )
    runner.test(
        hdl_toplevel="Note_Array",
        test_module="test_Note_Array",  # Must match this file's capitalization.
        test_dir=tests_dir,
        waves=True,
    )


def midi_frequency(midi_note):
    """Independent reference: twelve-tone equal temperament, A4 = 440 Hz."""
    return 440.0 * 2.0 ** ((midi_note - 69) / 12.0)


def midi_name(midi_note):
    return f"{NOTE_NAMES[midi_note % 12]}{midi_note // 12 - 1}"


def expected_midi_notes(root, steps):
    """Starting in octave 1, list notes whose exact pitch is <= 800 Hz."""
    midi_note = 24 + root   # C1 is MIDI note 24
    notes = []
    i = 0

    while midi_frequency(midi_note) <= MAX_FREQ_HZ:
        notes.append(midi_note)
        midi_note += 1 if steps is None else steps[i % len(steps)]
        i += 1

    return notes


def to_fixed(hz):
    """Positive real Hz to the DUT's unsigned fixed-point representation."""
    return int(hz * SCALE + 0.5)


def reciprocal_fixed(semitones):
    """Rounded Q12 representation of 2**(-semitones/12)."""
    return int(2.0 ** (-semitones / 12.0) * SCALE + 0.5)


def divide_fixed(b, a):
    """CORDIC model: rounded (B/A), with both inputs and output in Q12."""
    assert a > 0, "CORDIC attempted division by zero"
    return (b * SCALE + a // 2) // a


async def setup_dut(dut):
    """Drive known inputs, start a 50 MHz clock, and reset the DUT."""
    dut.rst_n.value = 0
    dut.start.value = 0
    dut.key.value = 0
    dut.scale.value = 0
    dut.val_in.value = 0
    dut.res.value = 0
    dut.ans.value = 0
    dut.cordic_busy.value = 0

    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())
    await ClockCycles(dut.clk, 5)
    await FallingEdge(dut.clk)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)
    await FallingEdge(dut.clk)

    assert int(dut.busy.value) == 0, "busy must be low after reset"
    assert int(dut.num_notes.value) == 0, "num_notes must be 0 after reset"


async def memory_model(dut, writes):
    """Accept every one-cycle start_write pulse; acknowledge each write."""
    while True:
        await RisingEdge(dut.start_write)
        await Timer(1, unit="ns")  # Registered address/data have now settled.

        await writes.put((
            int(dut.address.value),
            int(dut.val_out.value),
            int(dut.num_notes.value),
        ))

        # Drive acknowledgement ahead of a rising clock edge, then release.
        await FallingEdge(dut.clk)
        dut.res.value = 1
        await FallingEdge(dut.clk)
        dut.res.value = 0


async def cordic_model(dut, operations):
    """Respond to each start with busy high, then a Q12 division result."""
    while True:
        await RisingEdge(dut.cordic_start)
        await Timer(1, unit="ns")

        a = int(dut.A_out.value)
        b = int(dut.B_out.value)
        opcode = int(dut.opcode.value)
        await operations.put((opcode, a, b))

        assert opcode == 0, f"Expected division opcode 0, received {opcode}"
        result = divide_fixed(b, a)
        assert result < (1 << (BIT_WIDTH - 1)), (
            f"CORDIC result {result} overflows signed {BIT_WIDTH}-bit output"
        )

        # DIV_WAIT_BUSY sees busy=1; DIV_WAIT_DONE sees it return to 0.
        await FallingEdge(dut.clk)
        dut.cordic_busy.value = 1
        await ClockCycles(dut.clk, 2)
        await FallingEdge(dut.clk)
        dut.ans.value = result
        dut.cordic_busy.value = 0


async def run_scale_case(dut, *, name, key, scale, root, steps):
    await setup_dut(dut)

    expected_notes = expected_midi_notes(root, steps)
    assert len(expected_notes) == EXPECTED_COUNTS[name], (
        f"Test expectation error for {name}: got {len(expected_notes)} notes"
    )
    dut._log.info("Testing %s: %d expected notes", name, len(expected_notes))

    # Run both peripheral models concurrently with the test scoreboard.
    # This prevents us from missing a short CORDIC or memory request pulse.
    writes = Queue()
    operations = Queue()
    memory_task = cocotb.start_soon(memory_model(dut, writes))
    cordic_task = cocotb.start_soon(cordic_model(dut, operations))

    memory_image = {}
    next_expected_fixed = to_fixed(midi_frequency(expected_notes[0]))

    try:
        # One-clock start pulse. Models are already monitoring the interfaces.
        await FallingEdge(dut.clk)
        dut.key.value = key
        dut.scale.value = scale
        dut.start.value = 1
        await FallingEdge(dut.clk)
        dut.start.value = 0

        for index, midi_note in enumerate(expected_notes):
            # 1. Wait for the memory model to observe a write request.
            addr, value, count_before_ack = await with_timeout(
                writes.get(), 5000, timeout_unit="ns"
            )

            correct_address = MEMORY_BASE + ADDRESS_STRIDE * index
            actual_hz = value / SCALE
            ideal_hz = midi_frequency(midi_note)
            note = midi_name(midi_note)

            assert int(dut.start_read.value) == 0, "Unexpected read request"
            assert addr == correct_address, (
                f"{name} [{index}] {note}: address 0x{addr:X}, "
                f"expected 0x{correct_address:X}"
            )
            assert addr not in memory_image, (
                f"{name} [{index}] {note}: duplicate address 0x{addr:X}"
            )
            assert count_before_ack == index, (
                f"{name} [{index}] {note}: num_notes advanced before write ack"
            )
            assert value == next_expected_fixed, (
                f"{name} [{index}] {note}: stored Q12={value}, "
                f"expected {next_expected_fixed}"
            )
            assert value <= to_fixed(MAX_FREQ_HZ), (
                f"{name} [{index}] {note}: wrote {actual_hz:.5f} Hz above 800 Hz"
            )

            # Also compare against independent theoretical 12-TET frequencies.
            # Q12 reciprocal quantization accumulates some error across octaves.
            relative_error = abs(actual_hz - ideal_hz) / ideal_hz
            assert relative_error < 0.002, (
                f"{name} [{index}] {note}: {actual_hz:.6f} Hz vs "
                f"ideal {ideal_hz:.6f} Hz "
                f"({relative_error * 100:.4f}% relative error)"
            )
            memory_image[addr] = value
            dut._log.info(
                "%s [%02d] %-3s: %10.5f Hz (ideal %10.5f Hz) @ 0x%X",
                name, index, note, actual_hz, ideal_hz, addr,
            )

            # 2. Check the corresponding CORDIC request. The background
            #    model handles timing while this loop checks correctness.
            opcode, a, b = await with_timeout(
                operations.get(), 5000, timeout_unit="ns"
            )
            semitones = 1 if steps is None else steps[index % len(steps)]
            expected_a = reciprocal_fixed(semitones)

            assert opcode == 0, f"{name} [{index}] {note}: not a divide operation"
            assert b == value, (
                f"{name} [{index}] {note}: CORDIC B_out={b}, expected {value}"
            )
            assert a == expected_a, (
                f"{name} [{index}] {note}: CORDIC A_out={a}, "
                f"expected {expected_a} for {semitones}-semitone step"
            )
            next_expected_fixed = divide_fixed(value, expected_a)

        # Every final note gets one more division to determine that the NEXT
        # pitch is above the limit; that next pitch must not be written.
        assert next_expected_fixed > to_fixed(MAX_FREQ_HZ), (
            f"{name}: the next predicted pitch is still <= 800 Hz"
        )

        await with_timeout(FallingEdge(dut.busy), 5000, timeout_unit="ns")
        await ClockCycles(dut.clk, 4)
        await Timer(1, unit="ns")

        assert int(dut.busy.value) == 0, f"{name}: busy did not deassert"
        assert int(dut.start_write.value) == 0, f"{name}: extra write request"
        assert writes.empty(), f"{name}: wrote more than {len(expected_notes)} notes"
        assert operations.empty(), f"{name}: unexpected extra CORDIC operation"
        assert int(dut.num_notes.value) == len(expected_notes), (
            f"{name}: num_notes={int(dut.num_notes.value)}, "
            f"expected {len(expected_notes)}"
        )
        assert len(memory_image) == len(expected_notes)

        for index in range(len(expected_notes)):
            assert MEMORY_BASE + ADDRESS_STRIDE * index in memory_image

        dut._log.info("PASS: %s — all %d notes validated", name, len(expected_notes))

    finally:
        memory_task.cancel()
        cordic_task.cancel()


@cocotb.test()
async def test_chromatic(dut):
    await run_scale_case(
        dut, name="C chromatic", key=12, scale=1, root=0, steps=None
    )


@cocotb.test()
async def test_major(dut):
    await run_scale_case(
        dut, name="C major", key=0, scale=1, root=0, steps=MAJOR_STEPS
    )


@cocotb.test()
async def test_minor(dut):
    await run_scale_case(
        dut, name="A natural minor", key=9, scale=0, root=9, steps=MINOR_STEPS
    )

