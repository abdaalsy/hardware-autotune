import numpy as np
from scipy.signal import butter

# Specifications
fs = 48000  # Sampling frequency (Hz)
f_lower = 50.0  # Lower cutoff (Hz)
f_upper = 800.0  # Upper cutoff (Hz)
order = 4  # Must be even (prototype order M = order // 2)

# Normalize frequencies relative to Nyquist (fs / 2)
nyquist = 0.5 * fs
low = f_lower / nyquist
high = f_upper / nyquist

# Compute filter coefficients (b: numerator/feedforward, a: denominator/feedback)
b, a = butter(N=order // 2, Wn=[low, high], btype="band")

print("Numerator coefficients (b):", b)
print("Denominator coefficients (a):", a)
