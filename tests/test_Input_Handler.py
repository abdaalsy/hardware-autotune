import os
import random
import cocotb
from pathlib import Path
from cocotb.clock import Clock
from cocotb_tools.runner import get_runner
from cocotb.triggers import RisingEdge, FallingEdge, ClockCycles

def test_runner():
    sim = os.getenv("SIM", "icarus")
    proj_root = Path(__file__).resolve().parent

    # Make sure this points to your Input_Handler and I2S_Rx_Slave files
    sources = ["src/Input_Handler.sv", "src/I2S_Rx_Slave.sv"]
    
    runner = get_runner(sim)
    runner.build(
        sources=sources,
        hdl_toplevel="Input_Handler",
        always=True,
        timescale=("1ns", "1ps"),
        waves=True
    )
    runner.test(hdl_toplevel="Input_Handler", test_module="test_Input_Handler", waves=True)

async def real_i2s_master(dut, samples):
    """
    Strictly follows the Philips I2S standard:
    WS toggles 1 clock BEFORE the MSB is transmitted.
    """
    dut.bclk.value = 0
    dut.ws.value = 0
    dut.sd.value = 0
    await ClockCycles(dut.clk, 10)
    
    FRAME_BITS = 24 
    prev_lsb = 0 
    for val in samples:
        # --- LEFT CHANNEL (WS = 0) ---
        for bit_idx in range(FRAME_BITS):
            dut.bclk.value = 0
            if bit_idx == 0: 
                dut.ws.value = 0
                dut.sd.value = prev_lsb 
            else:
                dut.sd.value = 0 
            
            await ClockCycles(dut.clk, 4)
            dut.bclk.value = 1
            await ClockCycles(dut.clk, 4)
            
        prev_lsb = 0 
        # --- RIGHT CHANNEL (WS = 1) ---
        for bit_idx in range(FRAME_BITS):
            dut.bclk.value = 0
            if bit_idx == 0:
                dut.ws.value = 1
                dut.sd.value = prev_lsb
            else:
                shift = FRAME_BITS - bit_idx
                dut.sd.value = (val >> shift) & 1
                
            await ClockCycles(dut.clk, 4)
            dut.bclk.value = 1
            await ClockCycles(dut.clk, 4)
            
        prev_lsb = val & 1
 
    dut.bclk.value = 0
    dut.ws.value = 0
    dut.sd.value = prev_lsb
    await ClockCycles(dut.clk, 4)
    dut.bclk.value = 1
    await ClockCycles(dut.clk, 4)


@cocotb.test()
async def test_input_handler_256_samples(dut):
    """Tests the Input Handler pipeline with 256 back-to-back samples"""
    
    # 1. Setup clock (50 MHz)
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())
    
    # 2. Initialize inputs to 0
    dut.start.value = 0
    dut.address.value = 0
    dut.res_A.value = 0
    dut.bclk.value = 0
    dut.ws.value = 0
    dut.sd.value = 0

    # 3. Apply Reset Sequence
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 5)
    dut._log.info("Exited reset state")
    
    # 4. Generate 256 Random 24-bit test samples
    expected_samples = [random.randint(0, (1 << 24) - 1) for _ in range(256)]
    
    # 5. Start driving the I2S Bus in the background
    cocotb.start_soon(real_i2s_master(dut, expected_samples))
    
    # 6. Act as the Controlling Module / Memory to receive all 256 samples
    for i, expected_val in enumerate(expected_samples):
        expected_addr = i * 4  # Example: word-aligned memory addressing
        
        # A. Wait for 'ready' signal from Input_Handler
        # (Check if it's already 1, otherwise wait for the rising edge)
        await RisingEdge(dut.ready)
            
        # B. Output the desired memory address and pulse the 'start' signal
        dut.address.value = expected_addr
        
        # Pulse start for 2 clock cycles to ensure shift register catches it
        dut.start.value = 1
        await ClockCycles(dut.clk, 2)
        dut.start.value = 0
        
        # C. Wait for Input_Handler to command a memory write
        await RisingEdge(dut.start_write_A)
        
        # D. Validate data and address latched onto the output buses
        captured_val = int(dut.val_A.value)
        captured_addr = int(dut.address_A.value)
        
        dut._log.info(f"Sample {i:03d} | Expected Data: 0x{expected_val:06X}, Got: 0x{captured_val:06X} | Addr: {captured_addr}")
        
        assert captured_val == expected_val, f"Data mismatch on sample {i}!"
        assert captured_addr == expected_addr, f"Address mismatch on sample {i}!"
        
        # E. Simulate memory acknowledging the write by pulsing 'res_A'
        dut.res_A.value = 1
        await ClockCycles(dut.clk, 2) # Ensure resA_sreg shift register catches it
        dut.res_A.value = 0
        
        # F. Wait for Input_Handler to cleanly drop the write request and return to IDLE
        await FallingEdge(dut.start_write_A)
        
    dut._log.info("Successfully received and wrote all 256 samples to memory!")
