import numpy as np
import sys

# The notes in Major keys follow the pattern:   W - W - H - W - W - W - H
# Minor keys follow:                            W - H - W - W - H - W - W
# 
# B<->C is an H, as is E<->F, these are the only exceptions

# Human voice ranges from 50-800 Hz. That's from G1 to A5, 
# If we're supplied with a key, we should assemble a cache with all the notes for that key for as many octaves that fit in the range

def generate_freq_table(key, scale, max_hz, chromatic):
    is_major = True
    if scale.lower() == "minor":
        is_major = False

    FREQ_MINS = {
        "C": 32.70,
        "CS": 34.65,
        "D": 36.71,
        "DS": 38.89,
        "E": 41.20,
        "F": 43.65,
        "FS": 46.25,
        "G": 49.00,
        "GS": 51.91,
        "A": 55.00,
        "AS": 58.27,
        "B": 61.74
    }

    W_RATIO = float(2**(2.0/12.0))
    H_RATIO = float(2**(1.0/12.0))

    MAJOR_JUMPS = [W_RATIO, W_RATIO, H_RATIO, W_RATIO, W_RATIO, W_RATIO, H_RATIO]
    MINOR_JUMPS = [W_RATIO, H_RATIO, W_RATIO, W_RATIO, H_RATIO, W_RATIO, W_RATIO]
    
    table = []
    if chromatic:
        current_freq = FREQ_MINS["C"]
    else:
        current_freq = FREQ_MINS[key]
    i = 0
    while current_freq < max_hz:
        if chromatic:
            table.append(current_freq)
            current_freq *= H_RATIO
        else:
            if i > 6:
                i = 0   # We could also % by 7 but I think thats more work, however it looks simpler
            table.append(current_freq)
            if is_major:
                current_freq *= MAJOR_JUMPS[i]
            else:
                current_freq *= MINOR_JUMPS[i]
            i += 1

    return table

def find_nearest_note(freq, table):
    # binary search since table will be sorted, return last value
    start = 0
    end = len(table)-1
    while abs(start - end) > 1:
        mid = int((start + end)/2)
        if freq >= table[mid]:
            start = mid
        else:
            end = mid
    
    if start != end:
        diff = abs(table[start] - freq)
        if abs(table[end] - freq) < diff:
            return table[end]
        else:
            return table[start]
    else:
        return table[start]

if __name__ == "__main__":
    table = generate_freq_table(sys.argv[1], sys.argv[2], 800)
    print(find_nearest_note(100, table))

