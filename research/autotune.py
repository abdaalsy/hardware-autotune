import numpy as np
import librosa
import soundfile as sf
import matplotlib.pyplot as plt
import sys
import argparse

parser = argparse.ArgumentParser(description="Process data with chunks and shifts.")

parser.add_argument("-c", "--chunk-size", type=int, help="Size of each data chunk")
parser.add_argument("-s", "--shift", type=float, help="Shift amount value")
parser.add_argument("--cc-threshold", type=float, help="Normalized cross correlation threshold")
parser.add_argument("--ov-frac", type=int, help="Overlap fraction value")
parser.add_argument("-i", "--input", type=str, help="Path to the input file")
parser.add_argument("-o", "--output", type=str, help="Path to the output file")

args = parser.parse_args()

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

def input_audio(path):
    audio, sample_rate = librosa.load(path, sr=None)

    print("\n\n\n")
    print("File: " + path)
    print("Audio data shape: " + str(audio.shape))
    print("Sample rate: " + str(sample_rate))
    print("Preview: " + str(audio[2000:]))

    return audio, sample_rate

def autotune(signal, sample_rate):
    # Compress and copy the samples
    shift = get_shift(get_freq(signal), -1)
    samples_to_copy = args.chunk_size  # The number of samples we take before we compress then copy,

    split_indices = range(samples_to_copy, len(signal), samples_to_copy)
    chunks = np.array_split(audio, split_indices)

    shifted_chunks = pitch_shift(chunks, shift)
    
    # Resample at the original sample rate
    flattened = np.concatenate(shifted_chunks)
    resampled = resample(flattened, shift)  # You can try calculating the gap yourself, it ends up being equal to the shift ratio
    assert len(resampled) == len(signal), f"{len(resampled)} != {len(signal)}"

    return resampled

def get_freq(signal):
    return -1
    
def get_shift(freq, target):
    return args.shift

def create_copy(chunk, shift):
    # Determine the length of our copied chunk (including overlap)
    # Slide our window until we see that the points have been matching (make the target score configurable)
    # Copy that window and append it to the end of the chunk.
    cc_threshold = args.cc
    len_overlap = int(len(chunks)/args.ov_frac)    # We can vary this to see what gives best output
    len_copy = len_overlap + int(len(chunks)*(shift - 1))     # Total copied length = overlap + piece of chunk
    start = 0
    while start < (len(chunk) - len_copy):
        # Calculate normalized cross correlation
        start += 1

def pitch_shift(chunks, shift):
    """
    In a loop:
        Add a new chunk after the current one containing just a copy of the last N samples (N decided by shift + overlap)
        for the overlapping values, apply crossfade
        join into one chunk, move to next.
    """
    for i in range(len(chunks)):
        copy_chunk = chunks[i][-len_copy:].copy() # The last len_copy number of elements of the current chunk
        chunks[i] = cross_fade(chunks[i].copy(), copy_chunk, len_overlap)
    
    return chunks

def cross_fade(chnk_a, chnk_b, len_overlap):
    """
    To apply crossfade:
        Generate len_overlap values of a cos wave that starts at 1 and goes to 0
        Generate len_overlap values of a sin wave that starts at 0 and goes to 1
        Multiply the last len_overlap elements of chnk_a by the cos wave
        Multiply the first len_overlap elements of chnk_b by the sin wave
        Since sin^2 + cos^2 = 1, there should be no change in perceived volume and thus, no "jump cuts"
    """
    x = np.linspace(0, np.pi/2, len_overlap)
    cos_values = np.cos(x)
    sin_values = np.sin(x)
    joined = np.concatenate([ chnk_a[:-len_overlap], chnk_a[-len_overlap:]*cos_values + chnk_b[:len_overlap]*sin_values, chnk_b[len_overlap:] ])
    return joined

def output_audio(audio, sample_rate, path):
    sf.write(path, audio, sample_rate)
    print(f"Successfully saved to {path}") 

def output_waveform(audio, sample_rate, path):
    x = np.linspace(0, len(audio)/sample_rate, len(audio))

    plt.plot(x, audio, label="Vocals Waveform", color="green", alpha=1)

    plt.title("Vocals Waveform")
    plt.xlabel("time")
    plt.ylabel("displacement")
    plt.legend()

    plt.savefig(path, dpi=800, bbox_inches="tight")
    plt.close()

    print(f"Saved waveform to {path}")

if __name__ == "__main__":
    vocals_path = args.input
    output_audio_path = args.output
    # output_waveform_path = "output.png"
    
    audio, sample_rate = input_audio(vocals_path)
    autotuned = autotune(audio, sample_rate)

    output_audio(autotuned, sample_rate, output_audio_path)
    # output_waveform(autotuned, sample_rate, output_waveform_path)
