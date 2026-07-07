import sounddevice as sd
import numpy as np
import time
import numpy as np

SAMPLE_RATE = 44100
TAU_MIN = int(44100/800)
TAU_MAX = 512+256+128   # close enough to int(44100/50)
WINDOW_SIZE = 2048      # close enough to 2* int(44100/50). Larger is better
BLOCK_SIZE = WINDOW_SIZE + TAU_MAX     # Number of samples per chunk passed to the callback

def signal_energy(signal, length, W, new_lag, old_lag, reference_old):
    lagged_energy = reference_old
    lagged_energy -= np.sum(signal[length-new_lag:length-old_lag]**2)
    lagged_energy += np.sum(signal[length-W-new_lag:length-W-old_lag]**2)
    return lagged_energy

def autocorrelation(signal, length, W, tau):
    return np.sum(signal[length-W:] * signal[length-W-tau:length-tau])

def squared_difference(signal, length, W, tau_max):
    d = np.zeros(tau_max)
    reference_energy = np.sum(signal[length-W:]**2)
    lagged_old = reference_energy
    for tau in range(1, tau_max):
        lagged_new = signal_energy(signal, length, W, tau, tau - 1, lagged_old)
        d[tau] = reference_energy + lagged_new - 2*autocorrelation(signal, length, W, tau)
        lagged_old = lagged_new

    return d

def cmndf(d, tau_max):
    d_prime = np.ones(tau_max)  # Stores output of CMNDF for all lags (including 1 b/c the math collapses if we start from tau_min)
    running_sum = 0.0
    for tau in range(1, tau_max):
        running_sum += d[tau]
        if running_sum != 0:
            d_prime[tau] = d[tau] / ((1.0 / tau) * running_sum) # equation for cmndf
    
    return d_prime

def optimized_yin(frame, fs, threshold=0.1):
    W = WINDOW_SIZE
    tau_max = TAU_MAX
    tau_min = TAU_MIN
    length = len(frame)

    d = squared_difference(frame, length, W, tau_max)
    d_prime = cmndf(d, tau_max)

    # Run through till we find the first LOCAL MIN with a vlaue under the threshold
    min_tau = 1
    chosen_tau = None
    for tau in range(tau_min, tau_max - 1):
        if d_prime[tau] < threshold:
            if d_prime[tau] < d_prime[min_tau]:
                min_tau = tau
            # Check if it's a local minimum
            if d_prime[tau] < d_prime[tau-1] and d_prime[tau] < d_prime[tau+1]:
                chosen_tau = tau
                break
                
    # If nothing below threshold, take global minimum (must be higher than tau_min tho so we don't mistake for super high pitch)
    if chosen_tau is None:
        chosen_tau = min_tau
    
    if d_prime[chosen_tau] > 0.25: 
        return 0.0 # Unvoiced/Silence
   
    # Some parabolic interpolation for extra precision, this creates a parabola through d_prime[chosen_tau], and its neighbours, and then picks the x-value (tau) of the vertex
    if chosen_tau > tau_min and chosen_tau < tau_max - 1:
        alpha = d_prime[chosen_tau - 1]
        beta = d_prime[chosen_tau]
        gamma = d_prime[chosen_tau + 1]
        denom = 2.0 * (2.0 * beta - alpha - gamma)
        if denom != 0:
            delta = (gamma - alpha) / denom
            chosen_tau = chosen_tau + delta

    return fs / chosen_tau

def yin_pitch_estimate(frame, fs, threshold=0.1):
    W = WINDOW_SIZE
    tau_max = TAU_MAX
    tau_min = TAU_MIN
    length = len(frame)
   
    # For all different lags, calculate the difference between signal and time lagged samples, square each difference, then sum
    d = np.zeros(tau_max)
    for tau in range(1, tau_max):
        diff = frame[length-W:] - frame[length-W-tau:length-tau]
        d[tau] = np.sum(diff ** 2)

    d_prime = np.ones(tau_max)  # Stores output of CMNDF for all lags (including 1 b/c the math collapses if we start from tau_min)
    running_sum = 0.0
    for tau in range(1, tau_max):
        running_sum += d[tau]
        if running_sum != 0:
            d_prime[tau] = d[tau] / ((1.0 / tau) * running_sum) # equation for cmndf
            
    # Run through till we find the first LOCAL MIN with a vlaue under the threshold
    chosen_tau = None
    for tau in range(tau_min, tau_max - 1):
        if d_prime[tau] < threshold:
            # Check if it's a local minimum
            if d_prime[tau] < d_prime[tau-1] and d_prime[tau] < d_prime[tau+1]:
                chosen_tau = tau
                break
                
    # If nothing below threshold, take global minimum (must be higher than tau_min tho so we don't mistake for super high pitch)
    if chosen_tau is None:
        chosen_tau = np.argmin(d_prime[tau_min:tau_max]) + tau_min

    # The local min picking section can be made faster in worst case (no local min) by simply tracking current lowest
        
    # Voicing decision check
    if d_prime[chosen_tau] > 0.25: 
        return 0.0 # Unvoiced/Silence
        
    # 4. Parabolic Interpolation (extra precision, see how to optimize this)
    if chosen_tau > tau_min and chosen_tau < tau_max - 1:
        alpha = d_prime[chosen_tau - 1]
        beta = d_prime[chosen_tau]
        gamma = d_prime[chosen_tau + 1]
        denom = 2.0 * (2.0 * beta - alpha - gamma)
        if denom != 0:
            delta = (gamma - alpha) / denom
            chosen_tau = chosen_tau + delta

    return fs / chosen_tau

def detect_pitch(signal, sample_rate):
    return optimized_yin(signal, SAMPLE_RATE)

def audio_callback(indata, frames, time_info, status):
    """
    This function is called for every block of audio samples.
    'indata' is a numpy array of type float32 containing the mic samples.
    """
    # indata is your float32 array
    
    print(f"Pitch: {detect_pitch(indata.reshape(-1), SAMPLE_RATE)}", end="\r")

# Open the microphone input stream
stream = sd.InputStream(
    samplerate=SAMPLE_RATE,
    channels=1,
    dtype='float32',
    blocksize=BLOCK_SIZE,
    callback=audio_callback
)

print("Starting continuous stream. Press Ctrl+C to stop.")
with stream:
    try:
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nStream stopped.")
