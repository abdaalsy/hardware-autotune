import os
import random
import cocotb
from pathlib import Path
from cocotb.clock import Clock
from cocotb_tools.runner import get_runner
from cocotb.triggers import RisingEdge, ClockCycles

def test_runner():
    sim = os.getenv("SIM", "icarus")
    
    # Update this path to point to your Resampler.sv file
    sources = ["src/Resampler.sv"] 
    
    runner = get_runner(sim)
    runner.build(
        sources=sources,
        hdl_toplevel="Resampler",
        always=True,
        timescale=("1ns", "1ps"),
        waves=True
    )
    # Ensure test_module matches the name of this python file (without .py)
    runner.test(hdl_toplevel="Resampler", test_module="ut_Resampler", waves=True)


async def memory_responder(dut):
    """
    Simulates a memory controller. Watches for start_read_A or start_write_A, 
    waits a random amount of cycles to simulate memory latency,
    provides dummy data on reads, and pulses res_A for 2 clock cycles.
    """
    dut.res_A.value = 0
    dut.value_in.value = 0
    
    while True:
        await RisingEdge(dut.clk)
        
        # Check if the DUT is requesting a read or write
        is_read = dut.start_read_A.value == 1
        is_write = dut.start_write_A.value == 1
        
        if (is_read or is_write) and dut.res_A.value == 0:
            
            # 1. Add random memory latency (1 to 3 clock cycles)
            latency = random.randint(1, 3)
            await ClockCycles(dut.clk, latency)
            
            # 2. If it's a read, feed dummy data back (e.g., lower 24 bits of address)
            if is_read:
                dut.value_in.value = int(dut.address_A.value) & 0xFFFFFF
                
            # 3. Pulse res_A for 2 clock cycles so the DUT's 2-stage shift register catches it
            dut.res_A.value = 1
            await ClockCycles(dut.clk, 2)
            dut.res_A.value = 0
            
            # 4. Wait for the DUT to cleanly drop the request before looking for the next one
            while dut.start_read_A.value == 1 or dut.start_write_A.value == 1:
                await RisingEdge(dut.clk)


async def execute_resampler_test(dut, shift_val, test_name):
    """
    Common execution routine for testing the Resampler block.
    """
    # 1. Setup 50 MHz Clock
    clock = Clock(dut.clk, 20, unit="ns")
    cocotb.start_soon(clock.start())
    
    # 2. Initialize inputs
    dut.rst_n.value = 1
    dut.start.value = 0
    dut.shift.value = shift_val
    dut.period_samples.value = 300
    dut.res_A.value = 0
    dut.value_in.value = 0
    
    # Offset the heads so they don't immediately trigger overrun/underrun logic.
    # The Fixed-point format has 12 fractional bits, so we shift integers by 12.
    dut.write_pos_in.value = 1000 << 12
    dut.read_pos_in.value = 500 << 12
    dut.real_read_pos_in.value = 500 << 12

    # 3. Hardware Reset
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 5)

    # 4. Start the memory responder background task
    responder_task = cocotb.start_soon(memory_responder(dut))

    # 5. Wait for the DUT to be out of reset and in the IDLE state (busy == 0)
    while dut.busy.value == 1:
        await RisingEdge(dut.clk)
        
    dut._log.info(f"[{test_name}] DUT initialized and IDLE. Pulsing start...")
    
    # 6. Pulse Start for 2 cycles (caught by 2-stage shift reg)
    dut.start.value = 1
    await ClockCycles(dut.clk, 2)
    dut.start.value = 0

    # 7. Wait for DUT to assert busy (meaning it entered READ_INPUT)
    while dut.busy.value == 0:
        await RisingEdge(dut.clk)
        
    dut._log.info(f"[{test_name}] DUT is BUSY, iterating through 256 samples...")

    # 8. Wait for DUT to finish processing the block (busy falls back to 0)
    # We add a timeout to fail safely if the state machine gets deadlocked
    timeout_cycles = 15000 
    cycles_waited = 0
    
    while dut.busy.value == 1:
        await RisingEdge(dut.clk)
        cycles_waited += 1
        if cycles_waited > timeout_cycles:
            responder_task.kill()
            dut._log.error(f"[{test_name}] Timeout! DUT stuck in busy state.")
            assert False, "DUT FSM deadlock detected!"
            
    # 9. Verify results
    w_pos = int(dut.write_pos_out.value) / 4096.0
    r_pos = int(dut.read_pos_out.value) / 4096.0
    rr_pos = int(dut.real_read_pos_out.value) / 4096.0
    
    dut._log.info(f"[{test_name}] Success! Finished in {cycles_waited} cycles.")
    dut._log.info(f"[{test_name}] Final Positions -> Write: {w_pos:.2f}, Read: {r_pos:.2f}, Real: {rr_pos:.2f}")

    # Clean up the background task before the next test starts
    responder_task.kill()


@cocotb.test()
async def test_pitch_normal(dut):
    """Test Resampler with shift == 1.0 (Normal Speed)"""
    # 1.0 in fixed point (12 frac bits) is 0x1000
    await execute_resampler_test(dut, shift_val=0x1000, test_name="Pitch_Normal")

@cocotb.test()
async def test_pitch_up(dut):
    """Test Resampler with shift > 1.0 (Pitch Up)"""
    # 1.5 in fixed point (12 frac bits) is 0x1800
    await execute_resampler_test(dut, shift_val=0x1800, test_name="Pitch_Up")

@cocotb.test()
async def test_pitch_down(dut):
    """Test Resampler with shift < 1.0 (Pitch Down)"""
    # 0.5 in fixed point (12 frac bits) is 0x0800
    await execute_resampler_test(dut, shift_val=0x0800, test_name="Pitch_Down")
