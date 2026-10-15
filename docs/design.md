# Design Plans

How am I going to organize this?

Inputs (total 8 bits): key (4 bits), scale (major/minor) (1 bit), ADC (2 bits)
Outputs (total 8 bits): ADC (4 bits, FMT MD0 MD1 all driven at 0), DAC (6 bits)
Inouts (total 8 bits): RAM module (4 bits)

- Need to allocate 2 of the DAC's outputs to the inout pins

## Parameters

- Bit width: 24
- Format: 12.12 Fixed-point
- BLOCK_SIZE: 16 samples
- F_MAX: 800 Hz
- F_MIN: 50 Hz
- SAMPLE_RATE: 48000 Hz
- TAU_MAX: SAMPLE_RATE / F_MIN = 960
- TAU_MIN: SAMPLE_RATE / F_MAX = 60
- FRAME_WIDTH: 2*SAMPLE_RATE/F_MIN = 1920 samples
- WINDOW_SIZE: FRAME_WIDTH / 2 = 960 samples

## High Level Design

There's two major sections of this design:

1. Resampling 
2. Pitch Detection + Note matching

Resampling follows this flow:

1. The ADC is ready to send a sample, signalling the I2S Rx Slave module.
2. The input handler receives the sample and writes it to a circular buffer located in an external SPI memory module.
3. After BLOCK_SIZE samples have been written, we have time until the next tick of LRCK to read, resample, and write samples to the output buffer in the same SPI memory module.
4. An output handler reads the output buffer and send samples to the DAC using an I2S Tx Slave.

Pitch Detection follows this flow:

1. From step two of resampling, the sample is also directed to the butterworth bandpass filter.
2. After passing through the filter, the sample is written to another memory module.
3. When a start signal is pulsed, YIN pitch detection is performed on the frame.
4. Once the pitch is determined, the nearest note and the ratio is determined by reading the note frequencies LUT.
5. Both the current pitch, and ratio are outputted so that resampling knows what speed to sample at.

Because pitch detection uses its own ram module, its able to operate in parallel with resampling. This prevents us from having to squeeze both resampling and pitch detection in the narrow time between the BLOCK_SIZE'th sample, and the next sample.

## Operation Breakdown

### Startup/Configuring

For startup, the only operations we really perform is generating the frequency table. In a cycle we perform:

- 1 magnitude comparison (note_matching.py:50)
- 1 multiply (note_matching.py:54,56)
- 1 add (note_matching.py:57)

This is repeated a maximum of 56 times (in the case that the scale is set to chromatic).

### Resampling

First we step the write head:

- BLOCK_SIZE incrementations
- BLOCK_SIZE magnitude comparisons

Next, we binary search for the note.

- Max 2*log(56) magnitude comparisons
- Potentially a single multiply, square root, and magnitude comparison
- 1 division to determine the shift
- 1 division to determine the samples for one vocal period

Next, we check for an underrun:

- 1 addition
- 5 magnitude comparisons
- 1 modulo
- An addition and modulo if an underrun occurred

Finally, we resample repeating the following operations BLOCK_SIZE times:

- 3 additions
- 1 subtraction
- 4 magnitude comparisons
- 2 modulos
- 1 addition, 5 magnitude comparisons, 1 modulo (check overrun)
- Two additions and modulos if an overrun occurred.

### Pitch Detection

- (window_size * tau_max) + (4 * tau_max) - 1 additions/subtractions
- (window_size * tau_max) + (4 * tau_max) - 2 multiplications
- 2 * tau_max divisions
- 4 * (tau_max - tau_min) comparisons (worst-case scenario)

I can optimize this by storing prior autocorrelation for each lag.

