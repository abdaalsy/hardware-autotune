import numpy as np
import librosa
import soundfile as sf
import sys

def resample(arr, real_gap):
    resampled = []
    real_pos = 0.0
    i = 0
    while (i < len(arr)):
        resampled.append(arr[i])
        if real_pos - float(i) >= 1.0:
            i += 1
        elif real_pos - float(i) <= -1.0:
            i -= 1
        i += 1
        real_pos += real_gap
    return resampled

vocals = sys.argv[1]
audio, sample_rate = librosa.load(vocals, sr=None)
duration = len(audio) / sample_rate

print("\n\n\n")
print("File: " + vocals)
print("Audio data shape: " + str(audio.shape))
print("Sample rate: " + str(sample_rate))
print("Preview: " + str(audio[2000:]))

# Compress and copy the samples
shift = 1.5     # TODO: Convert target frequency into shift value, belongs to interval [0, 2]
samples_to_copy = 5000  # The number of samples we take before we compress then copy,

split_indices = range(samples_to_copy, len(audio), samples_to_copy)
chunks = np.array_split(audio, split_indices)
copied_chunks = chunks

for i in range(len(chunks)):
    copied_chunks[i] = np.append(chunks[i], chunks[i][-1*int( len(chunks[i])*abs(shift - 1) ): ])  # Copy over the chunk to get it back to the original length

print(len(copied_chunks[0]))

# Flatten and resample at the original sample rate
copied_chunks = np.concatenate(copied_chunks)
num_values = len(copied_chunks)
num_resamples = len(audio)
resampled = resample(copied_chunks, shift)  # You can try calculating the gap yourself, it ends up being equal to the shift ratio
assert len(resampled) == len(audio), f"{len(resampled)} != {len(audio)}"

sf.write('output.wav', resampled, sample_rate)
print("Successfully saved to output.wav") 


    
