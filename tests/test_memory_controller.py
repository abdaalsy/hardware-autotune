import os
import cocotb
from pathlib import Path
from cocotb.clock import Clock
from cocotb_tools.runner import get_runner
from cocotb.triggers import ClockCycles, RisingEdge, FallingEdge

def test_runner():
    sim = os.getenv("SIM", "icarus")
    proj_root = Path(__file__).resolve().parent

    sources = ["src/Memory_Controller.sv", "src/SPI_Master.sv"]
    runner = get_runner(sim)
    runner.build(
        sources=sources,
        hdl_toplevel="Memory_Controller",
        always=True,
        timescale=("1ns", "1ps"),
        waves=True
    )
    runner.test(hdl_toplevel="Memory_Controller", test_module="test_memory_controller", waves=True)

async def setup_dut(dut):
    """Initializes the clock, resets the DUT, and zeros all inputs."""
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start()) # 50 MHz

    # Initialize inputs
    dut._log.info("Entering reset state...")
    dut.rst_n.value = 0
    dut.start_read.value = 0
    dut.start_write.value = 0
    dut.address_in.value = 0
    dut.val_in.value = 0
    dut.MISO.value = 0
    
    for _ in range(5):
        await RisingEdge(dut.clk)

    dut.rst_n.value = 1
    await RisingEdge(dut.clk)
    dut._log.info("Exited reset state")

@cocotb.test()
async def test_read(dut):
    await setup_dut(dut)
    test_addr = 0xAAAAAA
    dut.address_in.value = test_addr
    dut.start_read.value = 1
    dut._log.info(f"Current state after pulsing rst_n: {dut.current_state.value}")
    await FallingEdge(dut.res)
    dut._log.info(f"Entered SERVICE state.")
     
    await RisingEdge(dut.res)
    # ASSERTIONS FOR READ A
    assert dut.command.value == 0x03, f"Expected Read Command (0x03), got {hex(dut.command.value)}"
    assert dut.address.value == test_addr, f"Expected Address {hex(test_addr)}, got {hex(dut.address.value)}"

@cocotb.test()
async def test_write(dut):
    await setup_dut(dut)
    test_addr = 0x111111
    test_val = 0x555555
    
    dut.address_in.value = test_addr
    dut.val_in.value = test_val
    dut.start_write.value = 1
    
    await RisingEdge(dut.res)
    # ASSERTIONS FOR WRITE A
    assert dut.command.value == 0x02, f"Expected Write Command (0x02), got {hex(dut.command.value)}"
    assert dut.address.value == test_addr, f"Expected Address {hex(test_addr)}, got {hex(dut.address.value)}"
    
    # Check that value_in was properly concatenated: {val_A, 8'h00}
    expected_value_in = (test_val << 8) | 0x00
    assert dut.value_in.value == expected_value_in, f"Expected Data {hex(expected_value_in)}, got {hex(dut.value_in.value)}"


