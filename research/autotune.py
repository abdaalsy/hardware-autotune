import numpy as np
import librosa
import math
import soundfile as sf

vocals = "vocals.m4a"

audio, sample_rate = librosa.load(vocals, sr=None)

print("\n\n\n")
print("File: " + vocals)
print("Audio data shape: " + str(audio.shape))
print("Sample rate: " + str(sample_rate))
print("Preview: " + str(audio[2000:]))

# Apply pitch shift, listen to output to see if proper 
shift = 1000.0 # Hz
indices = np.arange(audio.size, dtype=np.float32)

# Current time = sample index / sample rate
cos_values = np.cos(shift*indices/sample_rate)
sin_values = np.sin(shift*indices/sample_rate)
shifted_audio = audio*(cos_values + sin_values)

print("Shifted Preview: " + str(audio[2000:]))

sf.write('output.wav', shifted_audio, sample_rate)
print("Successfully saved to output.wav")

    
