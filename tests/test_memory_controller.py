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
    dut.start_read_A.value = 0
    dut.start_write_A.value = 0
    dut.start_read_B.value = 0
    dut.start_write_B.value = 0
    dut.address_A.value = 0
    dut.val_A.value = 0
    dut.address_B.value = 0
    dut.val_B.value = 0
    dut.MISO.value = 0
    
    for _ in range(5):
        await RisingEdge(dut.clk)

    dut.rst_n.value = 1
    await RisingEdge(dut.clk)
    dut._log.info("Exited reset state")

@cocotb.test()
async def test_read_A(dut):
    await setup_dut(dut)
    test_addr = 0xAAAAAA
    dut.address_A.value = test_addr
    dut.start_read_A.value = 1
    dut._log.info(f"Current state after pulsing rst_n: {dut.current_state.value}")
    await FallingEdge(dut.res_A)
    dut._log.info(f"Entered SERVICE_A state.")
     
    await RisingEdge(dut.res_A)
    # ASSERTIONS FOR READ A
    assert dut.command.value == 0x03, f"Expected Read Command (0x03), got {hex(dut.command.value)}"
    assert dut.address.value == test_addr, f"Expected Address {hex(test_addr)}, got {hex(dut.address.value)}"

@cocotb.test()
async def test_write_A(dut):
    await setup_dut(dut)
    test_addr = 0x111111
    test_val = 0x555555
    
    dut.address_A.value = test_addr
    dut.val_A.value = test_val
    dut.start_write_A.value = 1
    
    await RisingEdge(dut.res_A)
    # ASSERTIONS FOR WRITE A
    assert dut.command.value == 0x02, f"Expected Write Command (0x02), got {hex(dut.command.value)}"
    assert dut.address.value == test_addr, f"Expected Address {hex(test_addr)}, got {hex(dut.address.value)}"
    
    # Check that value_in was properly concatenated: {val_A, 8'h00}
    expected_value_in = (test_val << 8) | 0x00
    assert dut.value_in.value == expected_value_in, f"Expected Data {hex(expected_value_in)}, got {hex(dut.value_in.value)}"


@cocotb.test()
async def test_read_B(dut):
    await setup_dut(dut)
    test_addr = 0xBBBBBB
    dut.address_B.value = test_addr
    dut.start_read_B.value = 1
    
    await RisingEdge(dut.res_B)
    # ASSERTIONS FOR READ B
    assert dut.command.value == 0x03, "Read command incorrect for Port B"
    assert dut.address.value == test_addr, "Address incorrect for Port B"
    


@cocotb.test()
async def test_write_B(dut):
    await setup_dut(dut)
    test_addr = 0x222222
    test_val = 0x666666
    
    dut.address_B.value = test_addr
    dut.val_B.value = test_val
    dut.start_write_B.value = 1
    
    await RisingEdge(dut.res_B)
    # ASSERTIONS FOR WRITE B
    assert dut.command.value == 0x02, "Write command incorrect for Port B"
    expected_value_in = (test_val << 8) | 0x00
    assert dut.value_in.value == expected_value_in, "Data concatenation incorrect for Port B"

@cocotb.test()
async def test_simultaneous_read_A_and_B(dut):
    await setup_dut(dut)
    
    addr_A = 0x123123
    addr_B = 0x456456
    dut.address_A.value = addr_A
    dut.address_B.value = addr_B
    
    dut.start_read_A.value = 1
    dut.start_read_B.value = 1
    # WAIT FOR FIRST PORT TO BE SERVICED
    await RisingEdge(dut.res_A)
    
    # ASSERT PRIORITY A
    assert dut.res_B.value == 1, "Error: B started! A should have strict priority."
    assert dut.address.value == addr_A, "Controller did not load A's address!"
    assert dut.command.value == 0x03, "Controller did not load Read command!"
    
    # WAIT FOR SECOND PORT TO BE SERVICED FROM QUEUE
    await RisingEdge(dut.res_B)
    
    # ASSERT QUEUE B
    assert dut.address.value == addr_B, "Controller dropped B's request or loaded wrong address!"
    assert dut.command.value == 0x03, "Controller dropped B's read command!"


@cocotb.test()
async def test_simultaneous_write_A_and_B(dut):
    await setup_dut(dut)
    
    addr_A = 0xAAAAAA
    val_A = 0x111111
    addr_B = 0xBBBBBB
    val_B = 0x222222
    
    dut.address_A.value = addr_A
    dut.val_A.value = val_A
    dut.address_B.value = addr_B
    dut.val_B.value = val_B
    
    dut.start_write_A.value = 1
    dut.start_write_B.value = 1

    # WAIT FOR FIRST PORT TO BE SERVICED
    await RisingEdge(dut.res_A)
    
    # ASSERT PRIORITY A
    assert dut.res_B.value == 1, "Error: B started! A should have strict priority."
    assert dut.address.value == addr_A, "Controller did not load A's address!"
    assert dut.value_in.value == ((val_A << 8) | 0x00), "Controller did not load A's data!"
    
    # WAIT FOR SECOND PORT TO BE SERVICED FROM QUEUE
    await RisingEdge(dut.res_B)
    
    # ASSERT QUEUE B
    assert dut.address.value == addr_B, "Controller dropped B's write request!"
    assert dut.value_in.value == ((val_B << 8) | 0x00), "Controller did not load B's data!"
