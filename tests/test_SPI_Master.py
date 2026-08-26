import os
import sys
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, Timer
from cocotb_tools.runner import get_runner
from pathlib import Path

def test_runner():
    sim = os.getenv("SIM", "icarus")
    proj_root = Path(__file__).resolve().parent

    sources = ["src/SPI_Master.sv"]
    runner = get_runner(sim)
    runner.build(
        sources=sources,
        hdl_toplevel="SPI_Master",
        always=True,
        timescale=("1ns", "1ps")
    )
    runner.test(hdl_toplevel="SPI_Master", test_module="test_SPI_Master")

@cocotb.test()
async def test_write_sample(dut):
    """Testing writing a single 24-bit sample using the SPI master"""
    clock = Clock(dut.clk, 50, unit="ns")
    cocotb.start_soon(clock.start())
    
    # Put into reset state
    dut.rst_n.value = 1
    await Timer(200, unit="ns")
    dut.rst_n.value = 0
    for _ in range(10):
        await RisingEdge(dut.clk)
    print(f"DUT signals when in reset:\n{dut}")
    # Take out of reset
    dut.rst_n.value = 1

    # Populate input signals
    dut.cmnd_addr.value = 0x02123456
    dut.sample_in.value = 0xDEADBF
    dut.MISO.value = 0
    dut.start.value = 0

    # Send start
    await Timer(500, unit="ns")
    assert dut.current_state.value.binstr == "000"     # Check if in idle state
    dut.start.value = 1
    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk) # start_sreg = {0, 0, 1}
    print("start_sreg: " + str(dut.start_sreg.value))
    await RisingEdge(dut.clk) # start_sreg = {0, 1, 0}, next_state = START
    print("start_sreg: " + str(dut.start_sreg.value))
    await RisingEdge(dut.clk) # current_state = START, next_state = START_SCK
    print("current_state: " + str(dut.current_state.value))
    assert dut.current_state.value.binstr == "001"

    # Start SCK
    await RisingEdge(dut.clk) # current_state = START_SCK, next_state = SEND_CMND_ADDR
    print("current_state: " + str(dut.current_state.value))
    assert dut.current_state.value.binstr == "010"
    
if __name__ == "__main__":
    test_runner()
