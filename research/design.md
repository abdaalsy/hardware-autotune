# Autotune Pipeline Design

This document showcases the design of our autotune pipeline.

## Pipeline Overview

To go from input audio to our autotuned output audio, we traverse through the following stages:

![Autotune Pipeline](pipeline_flowchart.png)

- **Input Audio:** A number of audio samples are collected and stored in a buffer.
- **Pitch Detection:** The pitch is determined, taking into account these samples as well as older ones.
- **Note Matching:** The frequency of the closest musical note to the pitch (ie. A4 - 440 Hz) is determined.
- **Pitch Shifting:** The input audio gets pitched up/down without changing the duration.
- **Output Audio:** The same number of samples collected are sent to be played.

## Key Components

This section will go into more detail on how each pipeline component is designed. Before we begin that, here are the descriptions for a few important parameters:

| **Parameter** | **Description** | **Value** |
| :--- | :--- | :--- |
| $f_s$ | The frequency at which the user's vocals are sampled. | 48 or 44.1 kHz |
| Block size | The number of samples collected/outputted at a time. | 64 |
| $f_{max}$ | The highest pitch we can detect. | 800 Hz |
| $f_{min}$ | The lowest pitch we can detect. | 50 Hz |
| $\tau_{max}$ | The number of samples in one period at frequency $f_{min}$. | $f_s / f_{min}$ |
| $\tau_{min}$ | The number of samples in one period at frequency $f_{max}$. | $f_s / f_{max}$ |
| $W$ | The size (# of samples) of the fixed window used for pitch detection calculations. | $2 * f_s / f_{min}$ |


### Input Audio

Inputs: Block size, sample rate, audio source, write position
Outputs: Input circular buffer

Every $Block size / f_s$ seconds, new audio samples are written to the input buffer at the current write position, with the write position beind updated afterwards. If the write pointer is about to reach the end of the buffer it wraps around to the start, hence the *circular* buffer. This stage gets called at the same time as Output Audio, and will run in parallel to the others. However, because it is required by all subsequent stages it's been placed as the first stage in the pipeline.

### Pitch Detection

Inputs: signal, $W$, $f_s$, $f_{max}$, $f_{min}$, $\tau_{max}$, $\tau_{min}$, YIN threshold
Outputs: pitch (in Hz)

This is the most critical and difficult stage of the pipeline. An autotune pipeline is only as good as the pitch detection, which will be made apparent later. We use the YIN pitch detection method which performs the following steps:

1. Passes the signal through a butterworth bandpass filter to filter out frequencies outside of the vocal spectrum.
2. Computes the difference function $d(\tau)$ of the signal for each lag $\tau$.
3. Computes the cumulative mean normalized difference function (cmndf) $d'(\tau)$ of the signal.
4. Searches the output of $d'(\tau)$ for a local minimum *below* the YIN threshold, falling back to the global minimum if none are found.
5. Uses parabolic interpolation between \tau_{chosen} as well as one under and one above to find \tau_{final}.
6. Outputs a frequency by taking $f_s / \tau_{final}$

Enhancement:
This algorithm 

### Note Matching

Inputs: pitch
Outputs: nearest note frequency

Using a precalculated table of frequencies corresponding to musical notes, this algorithm performs a binary search for the pitch, returning the final value landed on. If the binary search lands on two values, we select the one nearest to the pitch. 

The table of frequencies is generated at startup given a key and a scale (ie. C, "major"). It accesses a LUT to determine the key in an octave near $f_{min}$, and uses the scale to generate the rest of the frequencies until the $f_{max}$ is reached. If the scale has been set to "chromatic", then the frequency table will simply be loaded with frequencies for every semitone from $f_{min}$ to $f_{max}$.

### Pitch Shifting

Inputs: pitch, note frequency, input circular buffer, read position, write position
Outputs: output buffer

The read head reads the input circular buffer at a speed determined by taking the note frequency over the pitch. When the ratio is not 1, there is the chance that the read head can overrun the write head, or be overran by the write head causing discontinuities in the output. We address this by simply advancing/retreating the read head by the number of samples corresponding to their vocal period (1/pitch). Since the human voice is periodic at a small enough scale, this prevents discontinuities in the output and prevents the audio from sounding sped up or slowed down.

### Output Audio

Inputs: output buffer
Outputs: playback device

This stage is simple, the samples of the output buffer are simply sent to the playback device.

## Pipeline Order 

Despite what the diagram shows, this pipeline is **not** sequential. Different components are called at different rates.

| **Component** | **Calling Frequency** |
| :--- | :--- |
| Input Audio | $f_s / Block size$ |
| Pitch Detection | 100 Hz |
| Note Matching | Variable |
| Pitch Shifting | Variable |
| Output Audio | $f_s / Block size$ |

- Input Audio & Output Audio are both called at the same rate because if N samples go in, N samples must come out.
- Pitch Detection is called at 100 Hz as a balance of latency and processing time.
- The remaining two stages should be called at the same frequency. They have been made variable because some users may want a faster retune speed to create the rap autotune effect, or a slower retune speed to sound more natural.

## Operation Breakdown

## Space Estimation
