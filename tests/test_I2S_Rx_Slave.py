import os
import cocotb
from pathlib import Path
from cocotb.clock import Clock
from cocotb_tools.runner import get_runner
from cocotb.triggers import RisingEdge, FallingEdge, ClockCycles

def test_runner():
    sim = os.getenv("SIM", "icarus")
    proj_root = Path(__file__).resolve().parent

    sources = ["src/I2S_Rx_Slave.sv"]
    runner = get_runner(sim)
    runner.build(
        sources=sources,
        hdl_toplevel="I2S_Rx_Slave",
        always=True,
        timescale=("1ns", "1ps"),
        waves=True
    )
    runner.test(hdl_toplevel="I2S_Rx_Slave", test_module="test_I2S_Rx_Slave", waves=True)

async def monitor_state(dut):
    """Prints state transitions in real time to catch where it hangs."""
    last_state = None
    while True:
        await RisingEdge(dut.clk)
        current = dut.current_state.value
        if current != last_state:
        # Map enum values if needed, or print raw int
            dut._log.info(f"State changed to: {current} | busy={dut.busy.value}")
            last_state = current

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
    prev_lsb = 0 # Holds the LSB to be transmitted when WS toggles
    for val in samples:
        # --- LEFT CHANNEL (WS = 0) ---
        for bit_idx in range(FRAME_BITS):
            dut.bclk.value = 0
            if bit_idx == 0: 
                dut.ws.value = 0
                # Output the LSB of the previous channel when WS toggles!
                dut.sd.value = prev_lsb 
            else:
                dut.sd.value = 0 # Dummy Left data
            
            await ClockCycles(dut.clk, 4)
            dut.bclk.value = 1
            await ClockCycles(dut.clk, 4)
            
        prev_lsb = 0 # Dummy LSB for the left channel
        # --- RIGHT CHANNEL (WS = 1) ---
        for bit_idx in range(FRAME_BITS):
            dut.bclk.value = 0
            if bit_idx == 0:
                dut.ws.value = 1
                # Output the dummy Left LSB
                dut.sd.value = prev_lsb
            else:
                # Transmit MSB down to Bit 1
                shift = FRAME_BITS - bit_idx
                dut.sd.value = (val >> shift) & 1
                
            await ClockCycles(dut.clk, 4)
            dut.bclk.value = 1
            await ClockCycles(dut.clk, 4)
            
        # Save the actual Right LSB to transmit on the NEXT WS toggle
        prev_lsb = val & 1
 
    # The DUT needs one more WS transition + bclk edge to latch the final
    # sample's LSB and fall back to IDLE. Without a "next" word, that edge
    # never comes on its own, so send a trailing flush pulse.
    dut.bclk.value = 0
    dut.ws.value = 0
    dut.sd.value = prev_lsb
    await ClockCycles(dut.clk, 4)
    dut.bclk.value = 1
    await ClockCycles(dut.clk, 4)

@cocotb.test()
async def _test_multiple_samples_busy_body(dut):
    # 1. Setup clock and reset
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start()) # 50 MHz
    cocotb.start_soon(monitor_state(dut))
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    
    # 2. Define the test data
    expected_samples = [0xAAAAAA, 0x555555, 0x123456, 0x789ABC]
    
    # 3. Launch the accurate I2S Master
    cocotb.start_soon(real_i2s_master(dut, expected_samples))
    
    # 4. Monitor the DUT for the 'busy' flag dropping to 0
    await RisingEdge(dut.busy)
    for i, expected in enumerate(expected_samples):
        # Poll until busy drops to 0 (data is ready)
        while dut.busy.value == 1:
            await RisingEdge(dut.clk)
            
        captured_val = int(dut.sample.value)
        dut._log.info(f"Frame {i}: Expected 0x{expected:06X}, Got 0x{captured_val:06X}")
        assert captured_val == expected, f"Mismatch on sample {i}!"
        
        # Poll until busy goes back to 1 (state machine started next frame)
        # -- but not after the last sample: there is no next frame coming.
        if i < len(expected_samples) - 1:
            while dut.busy.value == 0:
                await RisingEdge(dut.clk)
    dut._log.info("All samples received successfully!")

