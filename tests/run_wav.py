#!/usr/bin/env python3
"""CLI for simulating the uploaded HardwareAutotune_top on an input WAV file.

Usage:
  python run_wav.py --input examples/test_440hz.wav --output output.wav \
      --scale chromatic --sim icarus --max-seconds 0.15

Requires: cocotb 2.x, numpy, a simulator (Icarus/Verilator).
"""
import argparse
import math
import os
import sys
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SRC = Path("src")
TESTS = Path("tests")
NOTE_NAMES = ["C", "CS", "D", "DS", "E", "F", "FS", "G", "GS", "A", "AS", "B"]
SOURCES = [
    "Bandpass_Filter.sv", "Cordic.sv", "Cordic_Request_Arbiter.sv",
    "Detect_Pitch.sv", "HardwareAutotune_top.sv", "I2S_Rx_Slave.sv",
    "I2S_Tx_Slave.sv", "Input_Handler.sv", "Match_Note.sv",
    "Memory_Controller.sv", "Note_Array.sv", "Output_Handler.sv",
    "RAM_Request_Arbiter.sv", "Resampler.sv"
]


def convert_input(src, dst, max_seconds, resample):
    """Normalize source to 48kHz mono 24-bit PCM for an exact-rate I2S test."""
    from scipy.signal import resample_poly
    # Reuse the same bit-accurate parsing functions as the Cocotb test, but
    # permit other rates here by writing a temporary WAV with corrected header.
    # Avoid importing a Cocotb test on the host; implement parsing here.
    with wave.open(str(src), "rb") as f:
        rate, nch, wid, nframes = f.getframerate(), f.getnchannels(), f.getsampwidth(), f.getnframes()
        if f.getcomptype() != "NONE":
            raise ValueError("Input must be uncompressed PCM WAV (8/16/24/32-bit)")
        raw = f.readframes(nframes)
    if wid == 1:
        x = (np.frombuffer(raw, np.uint8).astype(np.int64) - 128) << 16
    elif wid == 2:
        x = np.frombuffer(raw, '<i2').astype(np.int64) << 8
    elif wid == 3:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int64)
        x = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        x[x >= 0x800000] -= 0x1000000
    elif wid == 4:
        x = np.frombuffer(raw, '<i4').astype(np.int64) >> 8
    else:
        raise ValueError("Only 8/16/24/32-bit integer PCM WAV is supported")
    x = np.rint(x.reshape(-1, nch).astype(np.float64).mean(axis=1))
    if max_seconds is not None:
        x = x[:int(round(max_seconds * rate))]
    if rate != 48000:
        if not resample:
            raise ValueError(f"Input WAV is {rate} Hz; add --resample to convert to 48 kHz")
        factor = math.gcd(rate, 48000)
        x = resample_poly(x, 48000 // factor, rate // factor)
    x = np.clip(np.rint(x), -(1 << 23), (1 << 23) - 1).astype(np.int64)
    u = (x & 0xFFFFFF).astype(np.uint32)
    payload = np.stack((u & 0xFF, (u >> 8) & 0xFF, (u >> 16) & 0xFF), axis=-1).astype(np.uint8)
    dst.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(dst), 'wb') as f:
        f.setnchannels(1)
        f.setsampwidth(3)
        f.setframerate(48000)
        f.writeframes(payload.tobytes())
    return len(x)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--input', required=True, type=Path, help='Input PCM WAV (mono or stereo)')
    p.add_argument('--output', required=True, type=Path, help='Generated 48 kHz mono 24-bit PCM WAV')
    p.add_argument('--sim', choices=('icarus', 'verilator'), default='icarus')
    p.add_argument('--max-seconds', type=float, default=None,
                   help='Only simulate the first N seconds (recommended for first runs: 0.10-0.15)')
    p.add_argument('--resample', action='store_true', help='Resample WAV to 48 kHz if necessary')
    p.add_argument('--tail-ms', type=float, default=0.0, help='Add silent ADC samples to capture pipeline tail')
    p.add_argument('--root', choices=NOTE_NAMES, default='C', help='Scale root, e.g. C, CS, D')
    p.add_argument('--scale', choices=('major','minor','chromatic'), default='chromatic')
    p.add_argument('--strict-rx', action='store_true', help='Fail if captured ADC words do not match WAV input')
    p.add_argument('--strict-output', action='store_true', help='Fail if non-silent input produces only silence at DAC after startup')
    p.add_argument('--fixed-rx', action='store_true', help='Use optional 24-bit-counting I2S receiver fix (original used by default)')
    p.add_argument('--no-trace', action='store_true', help='Skip generating debug CSV')
    p.add_argument('--build-dir', type=Path, default=HERE / 'sim_build')
    a = p.parse_args()
    if a.max_seconds is not None and a.max_seconds <= 0:
        p.error('--max-seconds must be positive')
    if a.tail_ms < 0:
        p.error('--tail-ms must be nonnegative')
    src = a.input.resolve()
    dst = a.output.resolve()
    if not src.is_file():
        p.error(f'Input WAV does not exist: {src}')
    build = a.build_dir.resolve()
    build.mkdir(parents=True, exist_ok=True)
    normalized = build / 'input_48k_mono_24bit.wav'
    num_samples = convert_input(src, normalized, a.max_seconds, a.resample)
    if num_samples < 4800:
        print('NOTE: Under 100 ms input: initial 80 ms pitch frame and 42.7 ms resampler'
              ' priming may leave limited processed audio.', file=sys.stderr)
    output_trace = '' if a.no_trace else str(dst.with_suffix('.csv'))
    os.environ.update({
        'AUTOTUNE_WAV_IN': str(normalized),
        'AUTOTUNE_WAV_OUT': str(dst),
        'AUTOTUNE_TRACE_CSV': output_trace,
        'AUTOTUNE_ROOT_KEY': str(NOTE_NAMES.index(a.root)),
        'AUTOTUNE_MAJOR': '0' if a.scale == 'minor' else '1',
        'AUTOTUNE_CHROMATIC': '1' if a.scale == 'chromatic' else '0',
        'AUTOTUNE_TAIL_SAMPLES': str(round(a.tail_ms * 48)),
        'AUTOTUNE_STRICT_RX': '1' if a.strict_rx else '0',
        'AUTOTUNE_STRICT_OUTPUT': '1' if a.strict_output else '0',
    })
    sys.path.insert(0, str(TESTS))
    from cocotb_tools.runner import get_runner
    runner = get_runner(a.sim)
    build_args = ['-g2012'] if a.sim == 'icarus' else ['--timing']
    source_files = [(HERE / 'optional_fixes' / x) if (a.fixed_rx and x == 'I2S_Rx_Slave.sv')
                    else SRC / x for x in SOURCES]
    runner.build(
        sources=source_files,
        hdl_toplevel='HardwareAutotune_top',
        build_dir=build,
        build_args=build_args,
        always=True,
    )
    runner.test(
        hdl_toplevel='HardwareAutotune_top',
        test_module='test_autotune_wav',
        test_dir=TESTS,
        extra_env={k: os.environ[k] for k in os.environ if k.startswith('AUTOTUNE_')},
    )
    print(f'\nOutput: {dst}')
    if output_trace:
        print(f'Diagnostics: {output_trace}')


if __name__ == '__main__':
    main()
