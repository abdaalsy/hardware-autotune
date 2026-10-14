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

def div_expected(a_val, b_val):
    """DUT computes B/A in Q12 (x_val drives A_in, y_val drives B_in)."""
    return round((b_val / a_val) * SCALE)


def sqrt_expected(a_val):
    """DUT computes sqrt(A) in Q12; B_in is ignored."""
    return round(math.sqrt(a_val / SCALE) * SCALE)


async def execute_cordic_test(dut, opcode, x_val, y_val, expected_val, tolerance, test_name):
    """
    Common execution routine for testing the CORDIC engine with value assertions.
    """
    clock = Clock(dut.clk, 20, unit="ns")
    cocotb.start_soon(clock.start())

    dut.rst_n.value = 1
    dut.start.value = 0
    dut.opcode.value = opcode
    dut.A_in.value = x_val
    dut.B_in.value = y_val
    dut.res.value = 0
    dut.val_in.value = 0

    # Hardware Reset
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 5)

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
            dut._log.error(f"[{test_name}] Timeout! DUT stuck in busy state.")
            assert False, "DUT FSM deadlock detected!"

    ans_val = int(dut.ans.value)
    # Handle signed conversion for comparison (ans is 25 bits)
    if ans_val & (1 << 24):
        ans_val -= (1 << 25)

    dut._log.info(f"[{test_name}] Finished in {cycles_waited} cycles. Got: {ans_val}, Expected: {expected_val} (Tol: ±{tolerance})")

    # Assert expected value within fixed-point quantization tolerance
    assert abs(ans_val - expected_val) <= tolerance, \
        f"[{test_name}] Assertion Failed! Expected {expected_val}, got {ans_val} (diff exceeds ±{tolerance})"


# --- Division Tests (Opcode = 0): ans = B / A, Q12 ---
# Tolerance note: when the DUT pre-shifts y right by k, the result is
# quantized to 2^k ulp after the post-shift, so tolerance grows with k.

@cocotb.test()
async def test_div_no_normalization(dut):
    """Division: |B| < |A| (k = 0): 0.5 / 1.0... A = 2.0, B = 1.0 -> 0.5"""
    a, b = 0x2000, 0x1000
    await execute_cordic_test(dut, opcode=0, x_val=a, y_val=b,
                              expected_val=div_expected(a, b), tolerance=4,
                              test_name="Div_No_Norm")


@cocotb.test()
async def test_div_equal_operands(dut):
    """Division: A == B (needs k = 1): 1.0 / 1.0 = 1.0"""
    a, b = 0x1000, 0x1000
    await execute_cordic_test(dut, opcode=0, x_val=a, y_val=b,
                              expected_val=div_expected(a, b), tolerance=4,
                              test_name="Div_Equal")


@cocotb.test()
async def test_div_one_normalization(dut):
    """Division: |B| >> |A| (k = 5): 1.0 / 0.0625 = 16.0"""
    a, b = 0x0100, 0x1000
    await execute_cordic_test(dut, opcode=0, x_val=a, y_val=b,
                              expected_val=div_expected(a, b), tolerance=8,
                              test_name="Div_One_Norm")


@cocotb.test()
async def test_div_two_normalization(dut):
    """Division: small operands, B > A (k = 2): (1/16) / (1/32) = 2.0"""
    a, b = 0x0080, 0x0100
    await execute_cordic_test(dut, opcode=0, x_val=a, y_val=b,
                              expected_val=div_expected(a, b), tolerance=4,
                              test_name="Div_Two_Norm")


@cocotb.test()
async def test_div_negative_divisor(dut):
    """Division: negative A: 1.0 / -2.0 = -0.5"""
    a, b = (-0x2000) & 0xFFFFFF, 0x1000
    await execute_cordic_test(dut, opcode=0, x_val=a, y_val=b,
                              expected_val=div_expected(-0x2000, b), tolerance=4,
                              test_name="Div_Neg_A")


# --- Square Root Tests (Opcode = 1): ans = sqrt(A), Q12 (B_in unused) ---

@cocotb.test()
async def test_sqrt_no_normalization(dut):
    """Square Root: A already in [0.5, 2): sqrt(1.5)"""
    a = 0x1800
    await execute_cordic_test(dut, opcode=1, x_val=a, y_val=0,
                              expected_val=sqrt_expected(a), tolerance=3,
                              test_name="Sqrt_No_Norm")


@cocotb.test()
async def test_sqrt_one_normalization(dut):
    """Square Root: small A needs left shift: sqrt(0.125)"""
    a = 0x0200
    await execute_cordic_test(dut, opcode=1, x_val=a, y_val=0,
                              expected_val=sqrt_expected(a), tolerance=3,
                              test_name="Sqrt_One_Norm")


@cocotb.test()
async def test_sqrt_two_normalization(dut):
    """Square Root: very small A needs larger left shift: sqrt(1/64)"""
    a = 0x0040
    await execute_cordic_test(dut, opcode=1, x_val=a, y_val=0,
                              expected_val=sqrt_expected(a), tolerance=3,
                              test_name="Sqrt_Two_Norm")


@cocotb.test()
async def test_sqrt_large_input(dut):
    """Square Root: large A needs right shift: sqrt(8.0)"""
    a = 0x8000
    await execute_cordic_test(dut, opcode=1, x_val=a, y_val=0,
                              expected_val=sqrt_expected(a), tolerance=8,
                              test_name="Sqrt_Large")


@cocotb.test()
async def test_sqrt_zero(dut):
    """Square Root: sqrt(0) -> 0 (special-case bypass)"""
    await execute_cordic_test(dut, opcode=1, x_val=0, y_val=0,
                              expected_val=0, tolerance=0,
                              test_name="Sqrt_Zero")


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
