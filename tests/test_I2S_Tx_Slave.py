import os
import cocotb
from pathlib import Path
from cocotb.clock import Clock
from cocotb_tools.runner import get_runner
from cocotb.triggers import RisingEdge, ClockCycles

def test_runner():
    sim = os.getenv("SIM", "icarus")
    proj_root = Path(__file__).resolve().parent
    sources = ["src/I2S_Tx_Slave.sv"]
    runner = get_runner(sim)
    runner.build(
        sources=sources,
        hdl_toplevel="I2S_Tx_Slave",
        always=True,
        timescale=("1ns", "1ps"),
        waves=True,
    )
    runner.test(hdl_toplevel="I2S_Tx_Slave", test_module="test_I2S_Tx_Slave", waves=True)


async def monitor_state(dut):
    """Prints state transitions in real time to catch where it hangs."""
    last_state = None
    while True:
        await RisingEdge(dut.clk)
        current = dut.current_state.value
        if current != last_state:
            dut._log.info(f"State changed to: {current} | busy={dut.busy.value}")
            last_state = current


async def i2s_master_drive_and_capture(dut, samples, bit_width=24, quarter_period=4):
    """
    Drives bclk/ws as the I2S bus master. The DUT is a Slave-Tx: it only
    ever *receives* bclk/ws and *drives* SD, so this coroutine both
    generates the bus timing and decodes the serial data the DUT produces.

    Because SD is driven from an internal register (not a live combinational
    signal), there are *two* dead bit-slots at the start of every word
    instead of one:
      - the bclk_negedge that moves WAIT_BIT -> OUTPUT fires while the case
        statement still evaluates the WAIT_BIT branch, so it drives nothing;
      - the next bclk_negedge (the first one evaluated in OUTPUT) schedules
        the MSB into SD via a non-blocking assignment, so that MSB only
        becomes visible on the bit slot *after* that -- one slot later again.
    So bit_idx 0 and 1 of each right channel are dead, real data starts at
    bit_idx 2, and a word's last two bits (bit1, bit0) spill over into
    bit_idx 0 and 1 of the *following* left channel.

    Loads `sample` at the start of each left channel -- IDLE spans the
    entire left channel, and sample_reg only reloads from `sample` while
    the DUT is in IDLE/WAIT_BIT, so this is always safely ahead of when
    the word is latched for shifting.

    Returns the list of captured (right-channel) words, one per entry in
    `samples`.
    """
    dut.bclk.value = 0
    dut.ws.value = 0
    await ClockCycles(dut.clk, 10)

    captured_words = []
    pending_bits = []  # bits collected so far for the word being assembled

    # One extra trailing left-channel start is required so the DUT can shift
    # out the final word's last two bits and fall back to IDLE -- without it
    # there's no following ws_negedge/bclk_negedge sequence to flush them,
    # and busy would never drop (mirrors the equivalent fix needed for
    # I2S_Rx_Slave).
    frames = list(samples) + [None]

    for i, val in enumerate(frames):
        # --- LEFT CHANNEL (ws = 0) ---
        for bit_idx in range(bit_width):
            dut.bclk.value = 0
            if bit_idx == 0:
                dut.ws.value = 0
                if i < len(samples):
                    dut.sample.value = samples[i]
            await ClockCycles(dut.clk, quarter_period)

            dut.bclk.value = 1
            await ClockCycles(dut.clk, quarter_period)
            sd_bit = int(dut.sd.value)

            if bit_idx in (0, 1) and pending_bits:
                # These two slots carry the last two bits (bit1, then bit0)
                # of the *previous* word.
                pending_bits.append(sd_bit)
                if bit_idx == 1:
                    word = 0
                    for b in pending_bits:
                        word = (word << 1) | b
                    captured_words.append(word)
                    pending_bits = []

        if val is None:
            break  # trailing flush frame -- no right channel needed

        # --- RIGHT CHANNEL (ws = 1) ---
        for bit_idx in range(bit_width):
            dut.bclk.value = 0
            if bit_idx == 0:
                dut.ws.value = 1
            await ClockCycles(dut.clk, quarter_period)

            dut.bclk.value = 1
            await ClockCycles(dut.clk, quarter_period)
            sd_bit = int(dut.sd.value)

            if bit_idx >= 2:
                pending_bits.append(sd_bit)  # bits 23 downto 2

    return captured_words


@cocotb.test()
async def test_multiple_samples_tx(dut):
    # 1. Setup clock and reset
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())  # 50 MHz
    cocotb.start_soon(monitor_state(dut))

    dut.rst_n.value = 0
    dut.sample.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1

    # 2. Define the test data
    samples_to_send = [0xAAAAAA, 0x555555, 0x123456, 0x789ABC]

    # 3. Drive the bus as an I2S master and capture what the DUT transmits
    captured = await i2s_master_drive_and_capture(dut, samples_to_send)

    # 4. Verify
    assert len(captured) == len(samples_to_send), (
        f"Expected {len(samples_to_send)} words, captured {len(captured)}"
    )
    for i, (expected, got) in enumerate(zip(samples_to_send, captured)):
        dut._log.info(f"Frame {i}: Expected 0x{expected:06X}, Got 0x{got:06X}")
        assert got == expected, (
            f"Mismatch on sample {i}: expected 0x{expected:06X}, got 0x{got:06X}"
        )

    dut._log.info("All samples transmitted successfully!")
