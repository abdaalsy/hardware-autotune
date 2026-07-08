import time
import wave
import sys
import numpy as np
import argparse
import sounddevice
from pitch_detection import detect_pitch

parser = argparse.ArgumentParser(description="Process data with chunks and shifts.")
parser.add_argument("-i", "--input", default="vocals.wav", type=str, help="Input file path")
parser.add_argument("-s", "--shift", type=float, help="(DEBUG) Shift amount value")
parser.add_argument("-k", "--key", type=str, help="Musical key. Options: [TODO]")
args = parser.parse_args()

SAMPLE_RATE = 48000
TAU_MIN = int(48000/800)
TAU_MAX = 1024   # close enough to int(48000/50)
WINDOW_SIZE = 2048      # close enough to 2* int(44100/50). Larger is better
BLOCK_SIZE = WINDOW_SIZE + TAU_MAX     # Number of samples per chunk passed to the callback

def check_overtake(pointer1, pointer2, jump_size, buffer_length):
    # Returns true if pointer1 is about to overtake pointer2 after stepping forward jump_size elements in a circular buffer of length buffer_length
    
    # Case 1: pointer2 > pointer1, but within jump_size of pointer1
    if (pointer2 > pointer1) and ((pointer1+jump_size) >= pointer2):
        return True
    # Case 2: pointer2 has already wrapped around, but is still within jump_size of pointer1 (which is boutta wrap)
    if (pointer1 > pointer2) and ((pointer1 + jump_size) >= buffer_length) and (((pointer1 + jump_size)%buffer_length) >= pointer2):
        return True
    return False

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


input_buffer = np.zeros(shape=(BLOCK_SIZE,), dtype=np.float32)
output_buffer = np.zeros(shape=(BLOCK_SIZE,), dtype=np.float32)
read_pos = 0
write_pos = read_pos + BLOCK_SIZE/2
input_stream = stream_wav_file(args.input, chunk_size=BLOCK_SIZE)

def move_read_head():
    global read_pos, write_pos, output_buffer
    # Collect indexes of input buffer to use (downsampled to 5 kHz to reduce operations - roughly translates to taking every 8th sample)
    pitch_indexes = np.arange(write_pos - BLOCK_SIZE, write_pos, dtype=np.int32) % len(input_buffer) 
    pitch = detect_pitch(input_buffer, SAMPLE_RATE, WINDOW_SIZE, TAU_MAX, TAU_MIN)
    period_samples = 0
    speed = args.shift
    if pitch:
        period_samples = int(1.0 / float(pitch) * SAMPLE_RATE)

    j = 0
    k = j
    read_indexes = np.zeros(shape=(BLOCK_SIZE,), dtype=np.int32)
    for i in range(BLOCK_SIZE):
        read_indexes[i] = read_pos + j
        j += 1
        k += speed
        if speed > 1 and k-j >= 1.0:
            j += 1
        elif speed < 1 and j-k >= 1.0:
            j -= 1
    
    read_indexes %= len(input_buffer)
    output_buffer = input_buffer[read_indexes]
    read_pos += j
    read_pos %= len(input_buffer)
    print(read_pos)
    
    if check_overtake(read_pos, write_pos, BLOCK_SIZE, len(input_buffer)):
        print("overrun")
        # print(pitch)
        read_pos -= period_samples*BLOCK_SIZE
        read_pos %= len(input_buffer)
    
    if check_overtake(write_pos, read_pos, BLOCK_SIZE, len(input_buffer)):
        print("underrun")
        # print(pitch)
        read_pos += period_samples*BLOCK_SIZE
        read_pos %= len(input_buffer)

def move_write_head():
    global write_pos
    # Will be called in a separate thread at sample_rate
    write_block = next(input_stream)
    write_indexes = np.arange(write_pos, write_pos + BLOCK_SIZE, dtype=np.int32) % len(input_buffer)
    input_buffer[write_indexes] = write_block
    write_pos += BLOCK_SIZE 
    write_pos %= len(input_buffer)  # Circles back to beginning of buffer

def audio_callback(outdata, frames, time_info, status):
    # This function is called by sounddevice in a separate background thread every time the audio buffer needs new data
    move_write_head()
    move_read_head()
    # Set outdata (which is actually an output passed by reference) to the output buffer
    outdata[:] = output_buffer.reshape(BLOCK_SIZE, 1)

def circular_slice(arr, start_idx, length):
    indices = np.arange(start_idx, start_idx + length)
    circular_indices = indices % len(arr)
    if length == 1:
        return arr[circular_indices[0]]
    return arr[circular_indices]

output_stream = sounddevice.OutputStream(samplerate=SAMPLE_RATE, blocksize=BLOCK_SIZE, channels=1, callback=audio_callback)

print("Playing real-time audio... Press Ctrl+C to stop.")
try:
    with output_stream:
        while True:
            time.sleep(0.1)  # Keep the main Python thread alive
except KeyboardInterrupt:
    sys.exit()
