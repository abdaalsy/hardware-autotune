import numpy as np
from scipy.signal import butter, sosfiltfilt

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

def bandpass(data, lowcut, highcut, fs, order=5):
    # The Nyquist frequency is half the sampling rate
    nyq = 0.5 * fs
    
    # Normalize the cutoff frequencies (0.0 to 1.0)
    low = lowcut / nyq
    high = highcut / nyq
    
    # Design the filter using second-order sections (SOS)
    sos = butter(order, [low, high], btype='bandpass', output='sos')
    
    # Apply the filter forward and backward to avoid phase shift
    filtered_data = sosfiltfilt(sos, data)
    
    return filtered_data

def optimized_yin(frame, fs, window_size, tau_max, tau_min, threshold=0.8):
    W = window_size
    length = len(frame)

    frame = bandpass(frame, 50, 1500, fs)

    d = squared_difference(frame, length, W, tau_max)
    d_prime = cmndf(d, tau_max)

    # Run through till we find the first LOCAL MIN with a vlaue under the threshold
    min_tau = tau_min
    chosen_tau = None
    for tau in range(tau_min, tau_max - 1):
        if d_prime[tau] < d_prime[min_tau]:
            min_tau = tau

        if d_prime[tau] < threshold:
            # Check if it's a local minimum
            if d_prime[tau] < d_prime[tau-1] and d_prime[tau] < d_prime[tau+1]:
                chosen_tau = tau
                break
                
    # If nothing below threshold, take global minimum (must be higher than tau_min tho so we don't mistake for super high pitch)
    if chosen_tau is None:
        chosen_tau = min_tau
    
    if d_prime[chosen_tau] > 0.15: 
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


def detect_pitch(signal, sample_rate, window_size, tau_max, tau_min, threshold=0.1):
    return optimized_yin(signal, sample_rate, window_size, tau_max, tau_min, threshold)
