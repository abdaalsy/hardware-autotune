import numpy as np
import wave

fs = 48000
duration = 0.25
frequency = 450.0

t = np.arange(int(fs * duration)) / fs
samples = np.round(
    0.25 * (2**23 - 1) *
    np.sin(2 * np.pi * frequency * t)
).astype(np.int32)

u = samples.astype(np.uint32) & 0xFFFFFF
pcm = np.column_stack([
    u & 0xFF,
    (u >> 8) & 0xFF,
    (u >> 16) & 0xFF
]).astype(np.uint8)

with wave.open("test_450hz.wav", "wb") as wav:
    wav.setnchannels(1)
    wav.setsampwidth(3)
    wav.setframerate(fs)
    wav.writeframes(pcm.tobytes())

print("450 Hz test WAV created")

