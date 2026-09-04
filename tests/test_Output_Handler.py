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

    # Ensure this points to both Output_Handler and your I2S_Tx_Slave
    sources = ["src/Output_Handler.sv", "src/I2S_Tx_Slave.sv"]
    
    runner = get_runner(sim)
    runner.build(
        sources=sources,
        hdl_toplevel="Output_Handler",
        always=True,
        timescale=("1ns", "1ps"),
        waves=True
    )
    runner.test(hdl_toplevel="Output_Handler", test_module="test_Output_Handler", waves=True)


async def generate_i2s_clocks(dut):
    """
    Drives the BCLK and WS inputs. 
    Without these, the I2S_Tx_Slave won't cycle through its state machine 
    and will never toggle the 'busy' signal.
    """
    dut.bclk.value = 0
    dut.ws.value = 0
    await ClockCycles(dut.clk, 10)
    
    FRAME_BITS = 24
    
    while True:
        # --- LEFT CHANNEL (WS = 0) ---
        for bit_idx in range(FRAME_BITS):
            dut.bclk.value = 0
            if bit_idx == 0:
                dut.ws.value = 0
            await ClockCycles(dut.clk, 4)
            dut.bclk.value = 1
            await ClockCycles(dut.clk, 4)
            
        # --- RIGHT CHANNEL (WS = 1) ---
        for bit_idx in range(FRAME_BITS):
            dut.bclk.value = 0
            if bit_idx == 0:
                dut.ws.value = 1
            await ClockCycles(dut.clk, 4)
            dut.bclk.value = 1
            await ClockCycles(dut.clk, 4)


async def memory_responder(dut, memory_dict):
    """
    Simulates the RAM. It listens for start_read_A, looks up the address,
    provides val_in, and pulses res_A after a simulated latency.
    """
    dut.val_in.value = 0
    dut.res_A.value = 0
    
    while True:
        # 1. Wait for Output_Handler to request a memory read
        await RisingEdge(dut.start_read_A)
        
        # 2. Extract requested address
        addr = int(dut.address_A.value)
        
        # 3. Simulate memory latency (e.g., 2 clock cycles)
        await ClockCycles(dut.clk, 2)
        
        # 4. Provide data onto the bus
        dut.val_in.value = memory_dict.get(addr, 0)
        
        # 5. Pulse res_A to tell Output_Handler the data is valid
        dut.res_A.value = 1
        await ClockCycles(dut.clk, 2) # Hold long enough for resA_sreg to catch it
        dut.res_A.value = 0
        
        # 6. Wait for the Output_Handler to drop the read request before looping
        await FallingEdge(dut.start_read_A)


@cocotb.test()
async def test_output_handler_256_samples(dut):
    """Tests the Output Handler pipeline by feeding it 256 back-to-back samples"""
    
    # 1. Setup clock (50 MHz)
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())
    
    # 2. Initialize top-level inputs
    dut.start.value = 0
    dut.address.value = 0
    dut.res_A.value = 0
    dut.bclk.value = 0
    dut.ws.value = 0
    dut.val_in.value = 0

    # 3. Apply Reset Sequence
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 5)
    
    # 4. Generate 256 Random 24-bit test samples into a dictionary (address -> value)
    expected_samples = [random.randint(0, (1 << 24) - 1) for _ in range(256)]
    # Simulate a word-aligned memory space where addr increments by 4
    memory_dict = { (i * 4): val for i, val in enumerate(expected_samples) }
    
    # 5. Start background tasks
    cocotb.start_soon(generate_i2s_clocks(dut))
    cocotb.start_soon(memory_responder(dut, memory_dict))
    
    # 6. Act as the Controlling Module that commands the Output_Handler
    for i, expected_val in enumerate(expected_samples):
        expected_addr = i * 4
        
        # A. Wait for 'ready' signal (meaning Tx Slave's busy went low)
        if dut.ready.value == 0:
            await RisingEdge(dut.ready)
            
        # B. Output the desired memory address and pulse the 'start' signal
        dut.address.value = expected_addr
        
        # Pulse start for 2 clock cycles to ensure start_sreg catches it
        dut.start.value = 1
        await ClockCycles(dut.clk, 2) 
        dut.start.value = 0
        
        # C. The memory_responder will now automatically satisfy the memory handshake.
        # We need to wait for the exact moment the Tx Slave pulls 'busy' HIGH again 
        # (which makes 'ready' go LOW). At this exact moment, the sample must be valid.
        await FallingEdge(dut.ready)
        
        # D. Validate that Output_Handler placed the correct data into sample_out
        # (cocotb can inspect internal signals using dut.sample_out)
        captured_internal = int(dut.sample_out.value)
        
        dut._log.info(f"Sample {i:03d} | Addr: {expected_addr} | Expected: 0x{expected_val:06X}, Latched to Tx: 0x{captured_internal:06X}")
        
        assert captured_internal == expected_val, f"Mismatch on sample {i}! Output_Handler failed to latch data in time."
        
    dut._log.info("Successfully fetched and delivered all 256 samples to the Tx Slave!")
