import numpy as np
import librosa

vocals = "vocals.m4a"

audio, sample_rate = librosa.load(vocals, sr=None)

print("\n\n\n")
print("File: " + vocals)
print("Audio data shape: " + str(audio.shape))
print("Sample rate: " + str(sample_rate))
print("Preview: " + str(audio[2000:]))

# Apply pitch shift, listen to output to see if proper 
shift = 500 # Hz

# Shifting in fourier domain corresponds to multiplying by complex exponential
# Since e^(iD) = cos(D) + i*sin(D), represent complex exponential using cos and sin waves
# Multiply our samples by our cos and sin waves then combine them together
