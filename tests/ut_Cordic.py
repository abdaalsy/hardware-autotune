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

ARCTAN_VALUES = [
    0.785398163397, 0.463647609000, 0.244978663126, 0.124354994546,
    0.062418809995, 0.031239833430, 0.015623728620, 0.007812341060,
    0.003906230131, 0.001953122516, 0.000976562189, 0.000488281211,
    0.000244140620, 0.000122070312, 0.000061035156, 0.000030517578
]

ARCTANH_VALUES = [
    0.549306144334, 0.255412811882, 0.125657214140, 0.062581571477,
    0.031260178492, 0.015626271752, 0.007812658953, 0.003906269871,
    0.001953127388, 0.000976562732, 0.000488281268, 0.000244140626,
    0.000122070312, 0.000061035156, 0.000030517578, 0.000015258789
]

async def memory_responder(dut):
    """
    Intercepts memory read/write requests from the CORDIC engine, 
    provides LUT values based on the requested address, and pulses res.
    """
    dut.res.value = 0
    dut.val_in.value = 0
    
    while True:
        await RisingEdge(dut.clk)
        
        is_read = dut.start_read.value == 1
        is_write = dut.start_write.value == 1
        
        if (is_read or is_write) and dut.res.value == 0:
            latency = random.randint(1, 2)
            await ClockCycles(dut.clk, latency)
            
            if is_read:
                addr = int(dut.address.value)
                if addr >= 32:
                    idx = addr - 32
                    val_float = ARCTANH_VALUES[idx] if idx < len(ARCTANH_VALUES) else 0.0
                else:
                    idx = addr
                    val_float = ARCTAN_VALUES[idx] if idx < len(ARCTAN_VALUES) else 0.0
                
                fixed_val = int(round(val_float * SCALE)) & 0xFFFFFF
                dut.val_in.value = fixed_val
                
            dut.res.value = 1
            await ClockCycles(dut.clk, 2)
            dut.res.value = 0
            
            while dut.start_read.value == 1 or dut.start_write.value == 1:
                await RisingEdge(dut.clk)

async def execute_cordic_test(dut, opcode, x_val, y_val, expected_val, tolerance, test_name):
    """
    Common execution routine for testing the CORDIC engine with value assertions.
    """
    clock = Clock(dut.clk, 20, unit="ns")
    cocotb.start_soon(clock.start())
    
    dut.rst_n.value = 1
    dut.start.value = 0
    dut.opcode.value = opcode
    dut.x_in.value = x_val
    dut.y_in.value = y_val
    dut.res.value = 0
    dut.val_in.value = 0

    # Hardware Reset
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 5)

    responder_task = cocotb.start_soon(memory_responder(dut))

    while dut.busy.value == 1:
        await RisingEdge(dut.clk)
        
    dut._log.info(f"[{test_name}] Initialized. Pulsing start (opcode={opcode})...")
    
    dut.start.value = 1
    await ClockCycles(dut.clk, 2)
    dut.start.value = 0

    while dut.busy.value == 0:
        await RisingEdge(dut.clk)
        
    dut._log.info(f"[{test_name}] DUT is BUSY, processing CORDIC iterations...")

    timeout_cycles = 10000
    cycles_waited = 0
    
    while dut.busy.value == 1:
        await RisingEdge(dut.clk)
        cycles_waited += 1
        if cycles_waited > timeout_cycles:
            responder_task.kill()
            dut._log.error(f"[{test_name}] Timeout! DUT stuck in busy state.")
            assert False, "DUT FSM deadlock detected!"
            
    ans_val = int(dut.ans.value)
    # Handle signed conversion for comparison
    if ans_val & (1 << 24):
        ans_val -= (1 << 25)

    dut._log.info(f"[{test_name}] Finished in {cycles_waited} cycles. Got: {ans_val}, Expected: {expected_val} (Tol: ±{tolerance})")

    # Assert expected value within fixed-point quantization tolerance
    assert abs(ans_val - expected_val) <= tolerance, \
        f"[{test_name}] Assertion Failed! Expected {expected_val}, got {ans_val} (diff exceeds ±{tolerance})"

    responder_task.kill()

# --- Division Tests (Opcode = 0) ---

@cocotb.test()
async def test_div_no_normalization(dut):
    """Division: No operands need normalization"""
    # x = 1.0 (4096), y = 1.0 (4096) -> Expected angle arctan(1.0) = pi/4 ≈ 0.785398 * 4096 ≈ 3217
    await execute_cordic_test(dut, opcode=0, x_val=0x1000, y_val=0x1000, expected_val=3217, tolerance=20, test_name="Div_No_Norm")

@cocotb.test()
async def test_div_one_normalization(dut):
    """Division: One operand needs normalization"""
    # x = 0x0100 (256 needs shift), y = 0x1000 (4096)
    await execute_cordic_test(dut, opcode=0, x_val=0x0100, y_val=0x1000, expected_val=6150, tolerance=30, test_name="Div_One_Norm")

@cocotb.test()
async def test_div_two_normalization(dut):
    """Division: Two operands need normalization"""
    # x = 0x0080 (128), y = 0x0100 (256)
    await execute_cordic_test(dut, opcode=0, x_val=0x0080, y_val=0x0100, expected_val=3217, tolerance=30, test_name="Div_Two_Norm")

# --- Square Root Tests (Opcode = 1) ---

@cocotb.test()
async def test_sqrt_no_normalization(dut):
    """Square Root: No operands need normalization"""
    await execute_cordic_test(dut, opcode=1, x_val=0x1800, y_val=0x0800, expected_val=2105, tolerance=30, test_name="Sqrt_No_Norm")

@cocotb.test()
async def test_sqrt_one_normalization(dut):
    """Square Root: One operand needs normalization"""
    await execute_cordic_test(dut, opcode=1, x_val=0x0200, y_val=0x0100, expected_val=2250, tolerance=30, test_name="Sqrt_One_Norm")

@cocotb.test()
async def test_sqrt_two_normalization(dut):
    """Square Root: Two operands need normalization"""
    await execute_cordic_test(dut, opcode=1, x_val=0x0040, y_val=0x0020, expected_val=2250, tolerance=30, test_name="Sqrt_Two_Norm")


def test_runner():
    sim = os.getenv("SIM", "icarus")
    sources = ["src/Cordic.sv"]
    
    runner = get_runner(sim)
    runner.build(
        sources=sources,
        hdl_toplevel="Cordic",
        always=True,
        timescale=("1ns", "1ps"),
        waves=True
    )
    runner.test(hdl_toplevel="Cordic", test_module="ut_Cordic", waves=True)

if __name__ == "__main__":
    test_runner()
