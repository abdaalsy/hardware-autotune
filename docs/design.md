# Design Plans

How am I going to organize this?

Inputs (total 8 bits): key (4 bits), scale (major/minor) (1 bit), ADC (2 bits)
Outputs (total 8 bits): ADC (4 bits, FMT MD0 MD1 all driven at 0), DAC (6 bits)
Inouts (total 8 bits): RAM module (4 bits)

- Need to allocate 2 of the DAC's outputs to the inout pins

## Parameters

Bit width: 32
Format: Fixed-point
BLOCK_SIZE: 128 samples
F_MAX: 800 Hz
F_MIN: 50 Hz
SAMPLE_RATE: 48000 Hz
TAU_MAX: SAMPLE_RATE / F_MIN
TAU_MIN: SAMPLE_RATE / F_MAX
FRAME_WIDTH: 2*SAMPLE_RATE/F_MIN
WINDOW_SIZE: FRAME_WIDTH / 2

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
