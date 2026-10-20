"""Cocotb 2.x regression for the byte-addressed Memory_Controller.
Run: SIM=icarus python -m pytest tests/test_Memory_Controller.py -s
"""
import os
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, ClockCycles, Timer, with_timeout
from cocotb_tools.runner import get_runner


ROOT = Path(__file__).resolve().parent.parent


def test_runner():
    runner = get_runner(os.getenv("SIM", "icarus"))
    runner.build(
        sources=[ROOT / "src" / "Memory_Controller.sv"],
        hdl_toplevel="Memory_Controller",
        parameters={"BIT_WIDTH": 24, "DEPTH_WORDS": 8192},
        timescale=("1ns", "1ps"),
        always=True,
        waves=True,
    )
    runner.test(
        hdl_toplevel="Memory_Controller",
        test_module="test_Memory_Controller",
        test_dir=Path(__file__).parent,
        waves=True,
    )


async def setup(dut):
    dut.clk.value = 0
    dut.rst_n.value = 0
    dut.start_read.value = 0
    dut.start_write.value = 0
    dut.address_in.value = 0
    dut.val_in.value = 0
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await ClockCycles(dut.clk, 5)
    await FallingEdge(dut.clk)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)


async def transaction(dut, addr, value=None):
    # Drive request away from the active clock edge.
    await FallingEdge(dut.clk)
    dut.address_in.value = addr
    dut.start_read.value = 1 if value is None else 0
    dut.start_write.value = 0 if value is None else 1
    if value is not None:
        dut.val_in.value = value
    await with_timeout(RisingEdge(dut.res), 500, timeout_unit="ns")
    await Timer(1, unit="ns")
    result = int(dut.val_out.value) if value is None else None
    await FallingEdge(dut.clk)
    dut.start_read.value = 0
    dut.start_write.value = 0
    await ClockCycles(dut.clk, 3)  # includes WAIT_DROP return to IDLE
    return result


@cocotb.test()
async def test_read_write_and_sign(dut):
    await setup(dut)
    cases = {
        0x10: 0x7FFFFF,
        0x14: 0x800000,
        0x18: 0x000000,
        0x1C: 0xC00000,
        0x810: 0x345678,
        0x480C: 0xABCDEF,
    }
    for address, value in cases.items():
        await transaction(dut, address, value=value)
    for address, expected in cases.items():
        received = await transaction(dut, address)
        assert received == expected, (
            f"RAM[{address:#08x}] expected {expected:#08x}, got {received:#08x}"
        )
    dut._log.info("BRAM read/write and 24-bit PCM bit patterns all pass")


@cocotb.test()
async def test_reset_preserves_ram_contents(dut):
    await setup(dut)
    await transaction(dut, 0x1010, value=0xFEDCBA)
    await FallingEdge(dut.clk)
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 3)
    await FallingEdge(dut.clk)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)
    assert await transaction(dut, 0x1010) == 0xFEDCBA

