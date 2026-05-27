# hardware-autotune

I'm re-creating the autotune effect using digital circuits. When complete, I plan to have a device that can connect to a microphone, and includes an onboard speaker with the option to connect a separate speaker instead. The user will speak into the microphone, the circuit I made with HDL is gonna apply autotune, and then the result will playback on the speaker.

## Design

This section will cover the design of the recording and playback analog circuits. I specifically left component values unknown as I they depend on certain specifications that I haven't decided yet.

For info on the digital implementations of autotune and other effects, see the following links:

- WIP

### Recording

![Recording Circuit Diagram](.docs/assets/recording_circuit.png)

Above is the circuit diagram detailing how microphone input will be processed before reaching the ADC, and then the DSP. 

1. We start at the voltage source labelled $V_{mic}$, this represents the microphone which converts your voice into an AC waveform with a very small amplitude. Microphones typically require a small amount of plug in power, so we connect the microphone to our $3.3 V$ source, with a resistor to limit current.
2. Because the DC plug in power is on the same line as the AC output, we place a capacitor $C_1$ to recenter our AC waveform at $0 V$. 
3. Now, we pass our microphone signal through an inverting amplifier circuit to get it to the ADC's desired amplitude.
4. Our waveform is still centered at $0 V$, we need to move it such that the minimum value of our waveform is at $0 V$, so we use a summing amplifier to accomplish that.
5. Now, our signal is ready to pass through the ADC and be processed by the DSP.

### Playback

![Playback Circuit Diagram](.docs/assets/playback_circuit.png)

Above is the circuit diagram detailing how our processed audio (digital) will be played on a speaker after being converted into analog by our DAC.

1. At the DAC output, our signal is going to be centered at some voltage above $0 V$, we send our signal through a difference amplifier to recenter it at $0 V$.
2. We place capacitor $C_2$ after the difference amplifier to ensure our signal is centered at zero, for the best audio quality.
3. While our signal has the amplitude required, it lacks the current needed to produce loud and rich audio. We use two source follower amplifiers, one for the positive half-cycle, and one for the negative. The diodes bias these transistors so that they are held at the edge of saturation, which is required to prevent crossover distortion. I specifically chose source follower amplifiers because the gain is unity, keeping our signal at the amplitude we like.
4. Now our audio signal is ready to be played on the speaker. Speakers can be modelled by an $8 \Omega$ impedance, showcasing their high current demands. This speaker will be onboard. The displacement of the speaker depends on the potential difference between its terminals.

## Parts

TBD

## Plans for future

**May 26, 2026:** I'm considering adding more effects than just autotune, and enabling profile creation too. My friend also gave me a pretty good idea of printing this using TinyTapeout, it'd be sick to have my very own ASIC that I designed all on my own. 

**May 27, 2026:** I feel like its useful for a project like this to have options for input/output. My plan is to use a 3.5mm audio jack for the mic input, and another 3.5mm audio jack directly after the DAC so that active speakers (like the ones in headphones) can hear the audio. The onboard 8-ohm passive speaker will still be there as it gives me a reason to design my own audio amp.
