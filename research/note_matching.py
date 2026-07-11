import numpy as np

# The notes in Major keys follow the pattern:   W - W - H - W - W - W - H
# Minor keys follow:                            W - H - W - W - H - W - W
# 
# B<->C is an H, as is E<->F, these are the only exceptions

# Human voice ranges from 50-800 Hz. That's from G1 to A5, 
# If we're supplied with a key, we should assemble a cache with all the notes for that key for as many octaves that fit in the range
# The plan is, given a root note and an indication for major/minor:
#   - Use the major/minor pattern to determine the notes
#   - Use a LUT to convert each to frequencies.
#   - Extend until we fill 50-800 Hz range (next octave is at double frequency)
# After we're complete, we'll have a table of length which is a multiple of 8.

def generate_freq_table(root_note, major, min_hz, max_hz):

