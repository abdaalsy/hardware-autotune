import os
import sys
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, FallingEdge, Timer
from cocotb_tools.runner import get_runner
from pathlib import Path

states = ["IDLE", "START", "START_SCK", "SEND_CMND_ADDR", "READ", "WRITE", "END", "END_SCK"]

def test_runner():
    sim = os.getenv("SIM", "icarus")
    proj_root = Path(__file__).resolve().parent

    sources = ["src/SPI_Master.sv"]
    runner = get_runner(sim)
    runner.build(
        sources=sources,
        hdl_toplevel="SPI_Master",
        always=True,
        timescale=("1ns", "1ps"),
        waves=True
    )
    runner.test(hdl_toplevel="SPI_Master", test_module="test_SPI_Master", waves=True)

async def reset_dut(dut):
    """Helper coroutine to reset the DUT."""
    dut._log.info("Resetting DUT...")
    dut.rst_n.value = 0
    dut.start.value = 0
    dut.cmnd_addr.value = 0
    dut.sample_in.value = 0
    dut.MISO.value = 0
    
    # Wait a few clock cycles
    for _ in range(5):
        await RisingEdge(dut.clk)
        
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)
    dut._log.info("DUT reset complete.")

@cocotb.test()
async def test_spi_write(dut):
    """Test writing a sample to the SPI slave (Command 0x02)"""
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())
    
    await reset_dut(dut)
    
    cmd_addr_val = 0x02ABCDEF
    sample_in_val = 0xDEADBEEF
    
    dut._log.info(f"[WRITE TEST] Setup Command/Addr: {hex(cmd_addr_val)}")
    dut._log.info(f"[WRITE TEST] Setup Sample In Data: {hex(sample_in_val)}")
    
    dut.cmnd_addr.value = cmd_addr_val
    dut.sample_in.value = sample_in_val
    
    # Pulse start for 1 clock cycle
    await FallingEdge(dut.clk)
    dut._log.info("[WRITE TEST] Pulsing start signal...")
    dut.start.value = 1
    await FallingEdge(dut.clk)
    dut.start.value = 0         # start_sreg = [0, 0, 1]
    await RisingEdge(dut.clk)   
    # start_sreg = [0, 1, 0], next_state = START, current_state scheduled to be IDLE
    await RisingEdge(dut.clk)
    # next_state = IDLE, current_state scheduled to be START
    await RisingEdge(dut.clk) # Wait a clock cycle for the state to change to START
    # current_state = START, next_state = IDLE
    dut._log.info(f"[WRITE TEST] Current state: {states[int(dut.current_state.value)]}")
    await RisingEdge(dut.clk)
    # current_state = START_SCK
    dut._log.info(f"[WRITE TEST] Current state: {states[int(dut.current_state.value)]}")
    await RisingEdge(dut.clk)
    # current_state = SEND_CMND_ADDR
    dut._log.info(f"[WRITE TEST] Current state: {states[int(dut.current_state.value)]}")
    
    # We act as a SPI Monitor, sampling MOSI on the rising edge of SCK
    dut._log.info("[WRITE TEST] Monitoring MOSI on SCK rising edges...")
    captured_mosi = 0
    for i in range(64):
        await RisingEdge(dut.SCK)

        val = dut.MOSI.value
        if val.is_resolvable:
            captured_mosi = (captured_mosi << 1) | int(val)
        else:
            dut._log.error(f"MOSI went to 'X' at bit index {i}!")
            raise ValueError(f"MOSI is {val} at bit {i}")
        
    # Wait for the transaction to complete (CS goes high)
    await RisingEdge(dut.CS)
    
    # Verify the full 64-bit transaction
    expected_data = (cmd_addr_val << 32) | sample_in_val
    dut._log.info(f"[WRITE TEST] Captured 64-bit MOSI stream: {hex(captured_mosi)}")
    
    assert captured_mosi == expected_data, f"SPI Write Mismatch! Expected: {hex(expected_data)}, Got: {hex(captured_mosi)}"
    dut._log.info("[WRITE TEST] SUCCESS: Master correctly drove Command/Address and Data.")

@cocotb.test()
async def test_spi_read(dut):
    """Test reading a sample from the SPI slave (Command 0x03)"""
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())
    
    await reset_dut(dut)
    
    cmd_addr_val = 0x03123456
    read_data_val = 0xCAFEBABE
    
    dut._log.info(f"[READ TEST] Setup Command/Addr: {hex(cmd_addr_val)}")
    dut._log.info(f"[READ TEST] Expected Data to read (via MISO): {hex(read_data_val)}")
    
    dut.cmnd_addr.value = cmd_addr_val
    
    # Pulse start for 1 clock cycle
    await FallingEdge(dut.clk)
    dut._log.info("[WRITE TEST] Pulsing start signal...")
    dut.start.value = 1
    await FallingEdge(dut.clk)
    dut.start.value = 0         # start_sreg = [0, 0, 1]
    await RisingEdge(dut.clk)   
    # start_sreg = [0, 1, 0], next_state = START, current_state scheduled to be IDLE
    await RisingEdge(dut.clk)
    # next_state = IDLE, current_state scheduled to be START
    await RisingEdge(dut.clk) # Wait a clock cycle for the state to change to START
    # current_state = START, next_state = IDLE
    dut._log.info(f"[WRITE TEST] Current state: {states[int(dut.current_state.value)]}")
    await RisingEdge(dut.clk)
    # current_state = START_SCK
    dut._log.info(f"[WRITE TEST] Current state: {states[int(dut.current_state.value)]}")
    await RisingEdge(dut.clk)
    # current_state = SEND_CMND_ADDR
    dut._log.info(f"[WRITE TEST] Current state: {states[int(dut.current_state.value)]}")
    
    # Phase 1: Monitor the 32-bit Command/Address phase sent by the Master
    captured_cmd = 0
    dut._log.info("[READ TEST] Phase 1: Monitoring Cmd/Addr on MOSI...")
    for i in range(32):
        await RisingEdge(dut.SCK)
        captured_cmd = (captured_cmd << 1) | int(dut.MOSI.value)
        await FallingEdge(dut.SCK)
        
    dut._log.info(f"[READ TEST] Phase 1 Complete. Master sent Cmd/Addr: {hex(captured_cmd)}")
    assert captured_cmd == cmd_addr_val, f"SPI Command Mismatch! Expected: {hex(cmd_addr_val)}, Got: {hex(captured_cmd)}"
    
    # Phase 2: Act as the SPI Slave and drive MISO for the 32-bit Data phase
    dut._log.info("[READ TEST] Phase 2: Driving MISO as SPI Slave...")
    for i in range(32):
        bit_val = (read_data_val >> (31 - i)) & 1
        dut.MISO.value = bit_val
        
        await RisingEdge(dut.SCK) 
        await FallingEdge(dut.SCK)
        
    dut._log.info("[READ TEST] Phase 2 Complete. Finished driving MISO.")
    
    # Wait for the transaction to complete (CS goes high)
    await RisingEdge(dut.CS)
    
    # Allow the DUT a few clock cycles to shift the final bit and settle the state machine
    for _ in range(10):
        await RisingEdge(dut.clk)
        
    # Verify the DUT successfully captured the sample_out
    captured_sample = dut.sample_out.value
    dut._log.info(f"[READ TEST] DUT sample_out port reads: {hex(captured_sample)}")
    
    assert captured_sample == read_data_val, f"SPI Read Mismatch! Expected: {hex(read_data_val)}, Got: {hex(captured_sample)}"
    dut._log.info("[READ TEST] SUCCESS: Master correctly captured MISO data.")
