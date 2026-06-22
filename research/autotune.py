import time
import wave
import numpy as np
import argparse
import sounddevice

def stream_wav_file(file_path, chunk_size=512):
    # Open the WAV file
    with wave.open(file_path, 'rb') as wav:
        # Extract audio properties
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        sample_rate = wav.getframerate()
        
        print(f"Streaming: {channels} channel(s), {sample_rate}Hz, {sample_width*8}-bit")
        
        # Determine the integer data type and max value for normalization
        if sample_width == 2:
            dtype = np.int16
            max_val = 32768.0
        elif sample_width == 4:
            dtype = np.int32
            max_val = 2147483648.0
        elif sample_width == 1:
            dtype = np.uint8
            max_val = 128.0  # Used for shifting and scaling 8-bit
        else:
            raise ValueError(f"Unsupported sample width: {sample_width} bytes")

        while True:
            # 1. Read exactly 'chunk_size' frames from the file
            raw_bytes = wav.readframes(chunk_size)
            
            # If raw_bytes is empty, we reached the end of the file
            if not raw_bytes:
                break
                
            # 2. Convert the raw bytes into an integer NumPy array
            audio_data = np.frombuffer(raw_bytes, dtype=dtype)
            
            # 3. Reshape the array if it's stereo (2 channels)
            if channels > 1:
                audio_data = audio_data.reshape(-1, channels)
            
            # 4. Convert to float32 and normalize between -1.0 and 1.0
            if dtype == np.uint8:
                # 8-bit audio centers around 128 (unsigned), so shift and scale
                float_data = (audio_data.astype(np.float32) - 128.0) / 128.0
            else:
                float_data = audio_data.astype(np.float32) / max_val
            
            # 'yield' makes this a generator stream returning fp32 arrays
            yield float_data

"""
We're gonna stream the data from some input file to input_buffer which is a circular buffer. This will happen in real-time in a separate thread. Real-time might be difficult due to multithreading restrictions so we might write like 512 samples at a time, sleeping for 512 * sample_period between writes.

As that's happening, in another thread the read head is gonna walk through this buffer at some speed decided by pitch detection, writing samples to output_buffer at the same sample rate (that means we need to sleep 1 sample period between each movement). However, because it might not be possible swap threads at this frequency, we can read like 512 samples at a time and sleep for 512*sample_period between reads.

Elaborating on pitch detection, it needs to occur when there are at least two full periods of the vocals waveform present. We'll take the previous 4096 samples from the write head's position and send that to pitch detection. Because our write head steps 512 at a time, we can reuse the 4096-512 most recent samples that were sent to pitch detection (that drastically decreases the math). 

At each read, a callback will occur causing the samples to be played on the speaker.
"""

parser = argparse.ArgumentParser(description="Process data with chunks and shifts.")
parser.add_argument("-i", "--input", default="vocals.wav", type=str, help="Input file path")
parser.add_argument("-b", "--buffer-size", type=int, help="Size of the circular buffer")
parser.add_argument("-s", "--shift", type=float, help="Shift amount value")
args = parser.parse_args()

block_size = 512
sample_rate = 44100

input_buffer = np.zeros(shape=(args.buffer_size,), dtype=np.float32)
output_buffer = np.zeros(shape=(block_size,), dtype=np.float32)
write_pos = 0
read_pos = int(write_pos - args.buffer_size/2) + args.buffer_size   # Initialize read head a little behind the write head
real_read_pos = float(read_pos)

input_stream = stream_wav_file(args.input, chunk_size=block_size)

def audio_callback(outdata, frames, time_info, status):
    global read_pos, write_pos, real_read_pos
    """
    This function is called by sounddevice in a separate background thread every time the audio buffer needs new data.
    """
    pitch_indexes = np.arange(write_pos, write_pos + 4096, dtype=np.int32) % len(input_buffer) 
    pitch = detect_pitch(input_buffer[pitch_indexes]) or 1
    # Determine nearest target frequency and get ratio (read head speed)
    speed = args.shift
    period_samples = 1.0 / float(pitch) * sample_rate

    # Updating write_pos and checking for underrun
    block = next(input_stream)
    indexes = np.arange(write_pos, write_pos + block_size, dtype=np.int32) % len(input_buffer)
    input_buffer[indexes] = block
    write_pos = (write_pos + block_size) % len(input_buffer)
    if read_pos - write_pos < block_size:
        write_pos -= period_samples
    if write_pos < 0:
        write_pos += len(input_buffer)
    elif write_pos >= len(input_buffer):
        write_pos -= len(input_buffer)

    # Updating read_pos and checking for overrun 
    for i in range(block_size):
        output_buffer[i] = input_buffer[int(read_pos)]
        read_pos += 1
        real_read_pos += speed
        if read_pos - real_read_pos >= 1:   # This happens when we are pitching down
            read_pos -= 1
        elif real_read_pos - read_pos >= -1: # This happens when we're pitching up
            read_pos += 1
    
    if write_pos - read_pos < block_size:
        read_pos -= period_samples
    if read_pos < 0:
        read_pos += len(input_buffer)
    elif read_pos >= len(input_buffer):
        read_pos -= len(input_buffer)
        
    # Set outdata (which is actually an output passed by reference) to the output buffer
    outdata[:] = output_buffer.reshape(block_size, 1)

def detect_pitch(signal):
    # Auto correlation will always start high, then decrease, then increase, and then decrease again
    # We want the 2nd peak so we're gonna wait till we decrease a 2nd time
    lag = 5
    auto_correl = np.dot(signal[-lag:], signal[-2*lag:-lag]) / lag
    prev_delta = -1     # Auto correlation starts off decreasing
    while lag+5 < len(signal)/2:
        lag += 5
        new_auto_correl = np.dot(signal[-lag:], signal[-2*lag:-lag]) / lag
        delta = new_auto_correl - auto_correl
        if prev_delta > 0 and delta < 0: # If we've reached a peak
            break
        prev_delta = delta
        auto_correl = new_auto_correl
    # Once we've reached this point, lag should be the number of samples for one period
    # That means freq = sample_rate/lag
    return sample_rate/lag

output_stream = sounddevice.OutputStream(samplerate=sample_rate, blocksize=block_size, channels=1, callback=audio_callback)

def circular_slice(arr, start_idx, length):
    indices = np.arange(start_idx, start_idx + length)
    circular_indices = indices % len(arr)
    if length == 1:
        return arr[circular_indices[0]]
    return arr[circular_indices]


print("Playing real-time audio... Press Ctrl+C to stop.")
try:
    with output_stream:
        while True:
            time.sleep(0.1)  # Keep the main Python thread alive
except:
    print(output_buffer)
