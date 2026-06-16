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

shift = 1.5     # TODO: Convert target frequency into a number to multiply fundamental freq by
samples_to_copy = 5000  # The number of samples we take before we compress then copy,

split_indices = range(0, len(audio), samples_to_copy)
chunks = np.array_split(audio, split_indices)
copied_chunks = [np.append(cluster, cluster[:samples_to_copy/shift]) for cluster in chunks]

print("Shifted Preview: " + str(audio[2000:]))

sf.write('output.wav', shifted_audio, sample_rate)
print("Successfully saved to output.wav") 


    
