import sounddevice as sd
import numpy as np
import threading
import argparse
import wave
import time
import sys
from pitch_detection import detect_pitch
from note_matching import generate_freq_table, find_nearest_note

parser = argparse.ArgumentParser(description="Process data with chunks and shifts.")
parser.add_argument("-i", "--input", default="vocals.wav", type=str, help="Input file path")
parser.add_argument("-k", "--key", type=str, help="The musical key (ex. \"C major\")")
parser.add_argument("-c", "--chromatic", type=bool, help="Whether or not to use the chromatic scale.")
args = parser.parse_args()

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

SAMPLE_RATE = 48000
F_MIN = 50
F_MAX = 800
TAU_MIN = int(SAMPLE_RATE/F_MAX)
TAU_MAX = int(SAMPLE_RATE/F_MIN)
BLOCK_SIZE = 128
FRAME_WIDTH = int(2*SAMPLE_RATE/F_MIN)   # in number of samples
WINDOW_SIZE = int(SAMPLE_RATE/F_MIN)
FREQ_TABLE = generate_freq_table("" if args.chromatic else args.key.split()[0], "" if args.chromatic else args.key.split()[1], F_MAX, args.chromatic)

past_pitches = [0 for i in range(3)]
input_buffer = np.zeros(shape=(BLOCK_SIZE*16,), dtype=np.float32)
input_stream = stream_wav_file(args.input, BLOCK_SIZE)
read_pos = 0
write_pos = read_pos + 4*BLOCK_SIZE

RATE_PITCH_DETECT = 100.0
pitch_lock = threading.Lock()

def check_overtake(pointer1, pointer2, jump_size, buffer_length):
    # Returns true if pointer1 is about to overtake pointer2 after stepping forward jump_size elements in a circular buffer of length buffer_length
    
    # Case 1: pointer2 > pointer1, but within jump_size of pointer1
    if (pointer2 > pointer1) and ((pointer1+jump_size) >= pointer2):
        return True
    # Case 2: pointer2 has already wrapped around, but is still within jump_size of pointer1 (which is boutta wrap)
    if (pointer1 > pointer2) and ((pointer1 + jump_size) >= buffer_length) and (((pointer1 + jump_size)%buffer_length) >= pointer2):
        return True
    return False

def circular_slice(arr, start_idx, length):
    indices = np.arange(start_idx, start_idx + length)
    circular_indices = indices % len(arr)
    if length == 1:
        return arr[circular_indices[0]]
    return arr[circular_indices]

def call_pitch_detect():
    global past_pitches
    with pitch_lock:
        pitch_indexes = np.arange(write_pos - FRAME_WIDTH, write_pos, dtype=np.int32)
        pitch = detect_pitch(input_buffer[pitch_indexes], SAMPLE_RATE, WINDOW_SIZE, TAU_MAX, TAU_MIN)
        del past_pitches[0]
        past_pitches.append(pitch)

def audio_callback(outdata, frames, time_info, status):
    global read_pos, write_pos, input_buffer
    # Move write head
    write_indexes = np.arange(write_pos, write_pos + BLOCK_SIZE, dtype=np.int32) % len(input_buffer)
    input_buffer[write_indexes] = next(input_stream)
    write_pos += BLOCK_SIZE
    write_pos %= len(input_buffer)
    
    # Move read head
    sorted_pitches = sorted(past_pitches)
    pitch = sorted_pitches[int(len(sorted_pitches)/2)]
    if not pitch:
        pitch = 140
    shift = find_nearest_note(pitch, FREQ_TABLE) / pitch
    print(pitch)
    period_samples = SAMPLE_RATE / pitch
    # Handle underrun
    if check_overtake(write_pos, read_pos, BLOCK_SIZE, len(input_buffer)):
        read_pos += period_samples
        read_pos %= len(input_buffer)

    read_indexes = np.zeros(BLOCK_SIZE, dtype=np.int32)
    real_read_pos = read_pos
    pitch_read_pos = read_pos
    for k in range(BLOCK_SIZE):
        read_indexes[k] = read_pos 
        read_pos += 1
        real_read_pos += shift
        if shift > 1 and real_read_pos-read_pos >= 1:
            read_pos += 1
        elif shift < 1 and read_pos-real_read_pos >= 1:
            read_pos -= 1

        read_pos %= len(input_buffer)
        real_read_pos %= len(input_buffer)
        
        # Handle overrun/underruns
        if check_overtake(read_pos, write_pos, 2, len(input_buffer)):
            read_pos -= period_samples
            real_read_pos -= period_samples
            read_pos %= len(input_buffer)
            real_read_pos %= len(input_buffer)
    
    outdata[:] = input_buffer[read_indexes].reshape(BLOCK_SIZE, 1)
    
# ==========================================
# THREAD LOOP WORKERS
# ==========================================
def worker_loop_pitch_detect(stop_event):
    """
    Executes call_pitch_detect at a rate of RATE_F.
    Uses time.perf_counter() for high-precision timing.
    """
    # Calculate the period (time between executions in seconds)
    period = 1.0 / RATE_PITCH_DETECT
    next_time = time.perf_counter()

    while not stop_event.is_set():
        call_pitch_detect()

        # Incremental timing prevents drift over long periods
        next_time += period
        sleep_time = next_time - time.perf_counter()

        if sleep_time > 0:
            time.sleep(sleep_time)
        else:
            # If a execution runs over its time budget, reset anchor to current time
            next_time = time.perf_counter()


# ==========================================
# MAIN EXECUTION CONTEXT
# ==========================================
if __name__ == "__main__":
    # Event used to signal threads to shut down gracefully
    shutdown_signal = threading.Event()

    # Create the two worker threads
    thread_1 = threading.Thread(
        target=worker_loop_pitch_detect, args=(shutdown_signal,), daemon=True
    )

    print(f"Starting Thread 1 at {RATE_PITCH_DETECT} Hz...")
    thread_1.start()
    # Open a duplex Stream (simultaneous input and output via callback)
    print("Starting audio stream...")
    try:
        with sd.OutputStream(
            samplerate=SAMPLE_RATE,
            blocksize=BLOCK_SIZE,
            channels=1,
            callback=audio_callback
        ):
            print("Audio stream running. Press Ctrl+C to exit.")
            while not shutdown_signal.is_set():
                time.sleep(0.5)
    except KeyboardInterrupt:
        # Catch Ctrl+C to shut down cleanly
        print("\nShutting down threads...")
        shutdown_signal.set()
        thread_1.join(timeout=1.0)
        print("Done.")
