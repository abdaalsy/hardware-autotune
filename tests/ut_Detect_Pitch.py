import os
import random
import math
import cocotb
from pathlib import Path
from cocotb.clock import Clock
from cocotb_tools.runner import get_runner
from cocotb.triggers import RisingEdge, ClockCycles

# Fixed-point scaling factor (ONES_BIT = 12 -> 2^12 = 4096)
SCALE = 4096

def float_to_q12_12(val):
    scaled = round(val * SCALE)
    if scaled >= (1 << 23):
        scaled = (1 << 23) - 1
    elif scaled < -(1 << 23):
        scaled = -(1 << 23)
    return scaled & 0xFFFFFF

def q12_12_to_float(val):
    if val & (1 << 23):  # Negative sign bit (24-bit width)
        val -= (1 << 24)
    return val / SCALE


# --- Background Responders ---

async def memory_responder(dut, memory_dict):
    """Responds to memory read and write requests from Detect_Pitch."""
    while True:
        await RisingEdge(dut.clk)
        dut.res.value = 0
        if dut.start_read.value == 1:
            addr = int(dut.address.value)
            # Retrieve sample from frame buffer or memory dict (default to 0)
            val = memory_dict.get(addr, 0)
            dut.val_in.value = val & 0xFFFFFF
            await RisingEdge(dut.clk)
            dut.res.value = 1
            await RisingEdge(dut.clk)
            dut.res.value = 0
        elif dut.start_write.value == 1:
            addr = int(dut.address.value)
            val = int(dut.val_out.value)
            memory_dict[addr] = val
            await RisingEdge(dut.clk)
            dut.res.value = 1
            await RisingEdge(dut.clk)
            dut.res.value = 0


async def cordic_responder(dut):
    """Emulates the CORDIC engine executing division (B / A) on cordic_start."""
    while True:
        await RisingEdge(dut.clk)
        if dut.cordic_start.value == 1:
            dut.cordic_busy.value = 1
            
            # Emulate pipeline/iteration latency
            for _ in range(8):
                await RisingEdge(dut.clk)
                
            a_val = int(dut.A_out.value)
            b_val = int(dut.B_out.value)
            
            # Handle signed conversion for 24-bit inputs
            if a_val & (1 << 23): a_val -= (1 << 24)
            if b_val & (1 << 23): b_val -= (1 << 24)
            
            # Compute division: B / A in Q12.12
            a_float = a_val / SCALE
            b_float = b_val / SCALE
            
            if abs(a_float) < 1e-6:
                ans_val = 0
            else:
                ans_val = round((b_float / a_float) * SCALE)
                
            if ans_val >= (1 << 23): ans_val = (1 << 23) - 1
            if ans_val < -(1 << 23): ans_val = -(1 << 23)
            
            dut.ans.value = ans_val & 0xFFFFFF

            # Wait some cycles to simulate the cordic run
            await ClockCycles(dut.clk, 24)
            dut.cordic_busy.value = 0


# --- Main Test Sequence ---

@cocotb.test()
async def test_detect_pitch_frequencies(dut):
    clock = Clock(dut.clk, 20, unit="ns")
    cocotb.start_soon(clock.start())

    # Shared memory dictionary model
    memory = {}

    # Start background responders
    cocotb.start_soon(memory_responder(dut, memory))
    cocotb.start_soon(cordic_responder(dut))

    # Initialize DUT signals
    dut.rst_n.value = 1
    dut.start.value = 0
    dut.frame_base.value = 0x000000
    dut.val_in.value = 0
    dut.res.value = 0
    dut.ans.value = 0
    dut.cordic_busy.value = 0

    # Hardware Reset
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 5)

    sample_rate = 16000  # 16 kHz sample rate
    frame_size = 1920
    frame_base = 0x000000
    test_frequencies = [220.0, 440.0, 880.0]  # A3, A4, A5

    for target_freq in test_frequencies:
        dut._log.info(f"Generating sine wave for target frequency: {target_freq} Hz")
        
        # Populate frame buffer in memory with Q12.12 sine wave samples (4-byte word offset)
        for i in range(frame_size):
            t = i / sample_rate
            sine_val = math.sin(2 * math.pi * target_freq * t)
            fixed_val = float_to_q12_12(sine_val)
            addr = frame_base + (i * 4)
            memory[addr] = fixed_val

        # Clear autocorrelation memory region at 0x004000 before starting
        for lag in range(100):
            memory[0x004000 + (lag * 4)] = 0

        # Trigger pitch detection
        dut.frame_base.value = frame_base
        await RisingEdge(dut.clk)
        dut.start.value = 1
        await ClockCycles(dut.clk, 2)
        dut.start.value = 0

        dut._log.info("Pitch detection started, waiting for busy to deassert...")

        # Wait for DUT to assert busy
        while dut.busy.value == 0:
            await RisingEdge(dut.clk)

        # Wait for DUT to finish processing (busy goes low)
        timeout_cycles = 200000
        cycles_waited = 0
        while dut.busy.value == 1:
            await RisingEdge(dut.clk)
            cycles_waited += 1
            if cycles_waited > timeout_cycles:
                dut._log.error("Timeout! Detect_Pitch stuck in busy state.")
                assert False, "DUT FSM deadlock detected!"

        # Verify autocorrelation values were written starting at 0x004000
        assert 0x004000 in memory, "Autocorrelation result at base 0x004000 was not written!"

        # Read detected pitch output
        detected_pitch_raw = int(dut.pitch.value)
        detected_pitch = q12_12_to_float(detected_pitch_raw)

        dut._log.info(f"Finished in {cycles_waited} cycles. Target: {target_freq} Hz | Detected Pitch: {detected_pitch:.2f} Hz")

        # Assert detected pitch is within a reasonable tolerance of the target frequency
        tolerance = target_freq * 0.10  # 10% tolerance
        assert abs(detected_pitch - target_freq) <= tolerance, \
            f"Pitch detection failed! Expected ~{target_freq} Hz, got {detected_pitch:.2f} Hz"

    dut._log.info("All frequency sweep test cases passed successfully!")


def test_runner():
    sim = os.getenv("SIM", "icarus")
    sources = ["src/Detect_Pitch.sv"]

    runner = get_runner(sim)
    runner.build(
        sources=sources,
        hdl_toplevel="Detect_Pitch",
        always=True,
        timescale=("1ns", "1ps"),
        waves=True
    )
    runner.test(hdl_toplevel="Detect_Pitch", test_module="ut_Detect_Pitch", waves=True)

if __name__ == "__main__":
    test_runner()
