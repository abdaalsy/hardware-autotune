import os
import cocotb
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path
from cocotb.clock import Clock
from cocotb_tools.runner import get_runner
from cocotb.triggers import ClockCycles, RisingEdge, FallingEdge

BIT_WIDTH = 24

async def setup_dut(dut):
    """Initializes the clock, resets the DUT, and zeros all inputs."""
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start()) # 50 MHz

    # Initialize inputs
    dut._log.info("Entering reset state...")
    dut.rst_n.value = 0
    dut.start.value = 0
    dut.sample_in.value = 0
    dut.frame_addr_base.value = 0
    dut.res.value = 0
    dut.val_in.value = 0
    
    for _ in range(5):
        await RisingEdge(dut.clk)

    dut.rst_n.value = 1
    await RisingEdge(dut.clk)
    dut._log.info("Exited reset state")

async def memory_responder(dut, captured_outputs):
    """
    Memory responder task that uses Cocotb's native RisingEdge trigger 
    on start_write to handle memory writes and the res handshake.
    """
    while True:
        # Wait directly for start_write to assert (0 -> 1)
        await RisingEdge(dut.start_write)
        await ClockCycles(dut.clk, 1)
        
        # 1. Drive res LOW to start the handshake sequence
        dut.res.value = 0
        
        # 2. Capture the filtered sample
        val = dut.val_out.value.signed_integer / 4096.0
        captured_outputs.append(val)
        dut._log.info(f"Memory Responder: Captured filtered sample -> {val}")
        
        # 3. Simulate memory controller latency (e.g., 5 clock cycles)
        await ClockCycles(dut.clk, 5)
        
        # 4. Drive res HIGH to generate the positive edge (res_posedge)
        dut.res.value = 1

@cocotb.test()
async def test_bandpass_filter(dut):
    # Initialize clock and reset DUT using existing helper
    await setup_dut(dut)

    captured_outputs = []
    
    # Start the memory responder background coroutine
    cocotb.start_soon(memory_responder(dut, captured_outputs))

    await RisingEdge(dut.clk)

    num_samples = 1920
    frame_base_addr = 0x1000

    dut._log.info("Starting bandpass filter sample processing loop...")

    freq = 1000
    fs = 48000
    t = np.arange(1920) / fs
    input_samples = np.sin(2 * np.pi * freq * t) * 4096

    for i in range(num_samples):
        sample_val = int(input_samples[i])
        dut._log.info(f"Filtering sample {i}: {sample_val / 4096}")

        # Convert negative numbers to 24-bit two's-complement bit pattern
        if sample_val < 0:
            sample_val = sample_val & ((1 << BIT_WIDTH) - 1)
        
        dut.sample_in.value = sample_val
        dut.frame_addr_base.value = frame_base_addr

        # Pulse start HIGH for 1 clock cycle
        dut.start.value = 1
        await RisingEdge(dut.clk)
        dut.start.value = 0
        await RisingEdge(dut.clk)

        prev_busy = dut.busy.value
        # Wait for busy negative edge indicating completion
        while True:
            await RisingEdge(dut.clk)
            curr_busy = dut.busy.value
            if prev_busy == 1 and curr_busy == 0:
                dut._log.info("Completed filtering of sample " + str(i))
                break
            prev_busy = curr_busy

        await ClockCycles(dut.clk, 2)

    dut._log.info(f"All {num_samples} samples processed.")
    dut._log.info(f"Captured Outputs: {captured_outputs}")
    # ==========================================
    # Plot and Save Waveform using Matplotlib
    # ==========================================
    plt.figure(figsize=(12, 6))
    
    # Plot the original input sine wave (scaled back to float [-1.0, 1.0])
    # Matching the length of captured_outputs in case of any framing offsets
    plt.plot(
        input_samples[:len(captured_outputs)] / 4096.0, 
        label=f'Input Signal ({freq} Hz)', 
        color='red', 
        alpha=0.7, 
        linewidth=1.5
    )
    
    # Plot the captured filtered output from memory
    plt.plot(
        captured_outputs, 
        label='Filtered Output', 
        color='blue', 
        linewidth=1.5
    )
    
    plt.title(f'Butterworth Bandpass Filter Test Verification ({freq} Hz)')
    plt.xlabel('Sample Index')
    plt.ylabel('Amplitude')
    plt.grid(True)
    plt.legend()
    
    # Save the plot to disk
    output_image_path = 'bandpass_filter_waveform.png'
    plt.savefig(output_image_path, dpi=300, bbox_inches='tight')
    dut._log.info(f"Waveform plot saved successfully as '{output_image_path}'")
    plt.close()

def test_runner():
    sim = os.getenv("SIM", "icarus")
    proj_root = Path(__file__).resolve().parent

    sources = ["src/Bandpass_Filter.sv"]
    runner = get_runner(sim)
    runner.build(
        sources=sources,
        hdl_toplevel="Bandpass_Filter",
        always=True,
        timescale=("1ns", "1ps"),
        waves=True
    )
    runner.test(hdl_toplevel="Bandpass_Filter", test_module="ut_Bandpass_Filter", waves=True)

if __name__ == "__main__":
    test_runner()
