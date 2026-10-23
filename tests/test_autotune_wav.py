"""Full-top-level WAV -> I2S ADC -> RTL -> I2S DAC -> WAV Cocotb test.

The ADC/DAC are emulated as synchronous 48kHz I2S masters sharing their
bit/word timing. Only WS=1 carries mono audio. Standard I2S has a one-bit
MSB delay; each stereo channel occupies 32 serial bit-clock periods.

Environment supplied by run_wav.py:
  AUTOTUNE_WAV_IN, AUTOTUNE_WAV_OUT, AUTOTUNE_ROOT_KEY,
  AUTOTUNE_MAJOR, AUTOTUNE_CHROMATIC, AUTOTUNE_TRACE_CSV,
  AUTOTUNE_TAIL_SAMPLES, AUTOTUNE_STRICT_RX.
"""

import asyncio
import csv
import os
import wave
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, Timer, FallingEdge
import numpy as np

FS = 48_000
BIT_WIDTH = 24
SLOTS_PER_CHANNEL = 32
BCK_HALF_PS = round(1_000_000_000_000 / (2 * 64 * FS))
SYSTEM_CLOCK_NS = 10
MIN_PCM = -(1 << 23)
MAX_PCM = (1 << 23) - 1




def read_pcm_wav(path):
    """Return signed 24-bit mono int32 PCM; file must be 48 kHz PCM WAV."""
    with wave.open(str(path), "rb") as f:
        if f.getcomptype() != "NONE":
            raise ValueError("Only uncompressed PCM WAV is supported")
        fs, channels, width, count = f.getframerate(), f.getnchannels(), f.getsampwidth(), f.getnframes()
        raw = f.readframes(count)
    if fs != FS:
        raise ValueError(f"Expected {FS} Hz WAV; got {fs} Hz. Use run_wav.py --resample to convert")
    if channels <= 0 or width not in (1, 2, 3, 4):
        raise ValueError(f"Unsupported WAV format: channels={channels}, sample bytes={width}")
    if width == 1:  # WAV 8-bit PCM is unsigned
        pcm = (np.frombuffer(raw, np.uint8).astype(np.int32) - 128) << 16
    elif width == 2:
        pcm = np.frombuffer(raw, "<i2").astype(np.int32) << 8
    elif width == 3:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.uint32)
        u = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        pcm = u.astype(np.int32)
        pcm[pcm >= (1 << 23)] -= 1 << 24
    else:
        pcm = (np.frombuffer(raw, "<i4") >> 8).astype(np.int32)
    pcm = pcm.reshape(-1, channels)
    if channels != 1:
        # Widen before summing stereo channels to avoid signed overflow.
        pcm = np.rint(pcm.astype(np.float64).mean(axis=1)).astype(np.int32)
    else:
        pcm = pcm[:, 0]
    return np.clip(pcm, MIN_PCM, MAX_PCM).astype(np.int32)


def write_pcm24_wav(path, pcm):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    s = np.clip(np.asarray(pcm, dtype=np.int64), MIN_PCM, MAX_PCM)
    u = (s & 0xFFFFFF).astype(np.uint32)
    bytes3 = np.empty((len(u), 3), dtype=np.uint8)
    bytes3[:, 0] = u & 0xFF
    bytes3[:, 1] = (u >> 8) & 0xFF
    bytes3[:, 2] = (u >> 16) & 0xFF
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(3)
        f.setframerate(FS)
        f.writeframes(bytes3.tobytes())


def to_signed24(raw):
    value = int(raw) & 0xFFFFFF
    return value - (1 << 24) if value & (1 << 23) else value


def safe_value(signal):
    try:
        return int(signal.value)
    except (ValueError, TypeError):
        return None


YIN_STATE_NAMES = [
    "IDLE", "INIT", "NREAD", "NREC", "TREAD", "TPROD", "RRD", "RWR",
    "TNEXT", "NNEXT", "REF", "ERD", "EREC", "ESQ", "R2RD", "MEAN",
    "MUPD", "DPR", "DEC", "P2NEXT", "FIN0", "FIN_D", "FIN_P",
    "DONE", "CALL", "CWAIT",
]


def yin_snapshot(dut):
    """Read internal YIN progress without requiring a waveform dump."""
    try:
        y = dut.u_yin
        raw_state = safe_value(y.state)
        state_name = (YIN_STATE_NAMES[raw_state]
                      if raw_state is not None and 0 <= raw_state < len(YIN_STATE_NAMES)
                      else str(raw_state))
        return "state=%s n=%s tau=%s pend=%s warm=%s seq=%s" % (
            state_name, safe_value(y.n), safe_value(y.tau), safe_value(y.pend),
            safe_value(y.warm), safe_value(y.frame_seq),
        )
    except (AttributeError, ValueError, TypeError) as exc:
        return "internal YIN registers unavailable: %s" % exc


@cocotb.test()
async def process_wav_through_full_autotune(dut):
    input_path = Path(os.environ["AUTOTUNE_WAV_IN"]).resolve()
    output_path = Path(os.environ["AUTOTUNE_WAV_OUT"]).resolve()
    trace_path = os.environ.get("AUTOTUNE_TRACE_CSV", "")
    pcm_in = read_pcm_wav(input_path)
    tail = int(os.environ.get("AUTOTUNE_TAIL_SAMPLES", "0"))
    strict_rx = os.environ.get("AUTOTUNE_STRICT_RX", "0") == "1"
    strict_output = os.environ.get("AUTOTUNE_STRICT_OUTPUT", "0") == "1"

    dut.rst_n.value = 0
    dut.clk.value = 0
    dut.adc_bclk.value = 0
    dut.adc_ws.value = 0
    dut.adc_sd.value = 0
    dut.dac_bclk.value = 0
    dut.dac_ws.value = 0
    dut.root_key.value = int(os.environ.get("AUTOTUNE_ROOT_KEY", "0"))
    dut.major_scale.value = int(os.environ.get("AUTOTUNE_MAJOR", "1"))
    dut.chromatic.value = int(os.environ.get("AUTOTUNE_CHROMATIC", "1"))

    cocotb.start_soon(Clock(dut.clk, SYSTEM_CLOCK_NS, unit="ns").start())
    await Timer(500, unit="ns")
    dut.rst_n.value = 1
    await Timer(500, unit="ns")

    stats = {
        "rx_samples": [],
        "input_transactions": 0,
        "resample_starts": 0,
        "resampler_reads": 0,
        "resampler_writes": 0,
        "ram0_output_writes": 0,
        "ram0_output_nonzero": 0,
        "ram0_output_unknown": 0,
        "ram0_circular_writes": 0,
        "ram0_circular_nonzero": 0,
        "handler_nonzero_checks": 0,
        "handler_checked": 0,
        "output_starts": 0,
        "output_reads": 0,
        "yin_starts": 0,
        "yin_reads": 0,
        "yin_writes": 0,
        "yin_cordic_starts": 0,
        "match_starts": 0,
        "trace": [],
    }

    async def monitor_input():
        while True:
            await RisingEdge(dut.in_write)
            await Timer(1, unit="ns")
            val = safe_value(dut.in_val)
            if val is not None:
                stats["rx_samples"].append(to_signed24(val))
            stats["input_transactions"] += 1

    async def monitor_starts(handle, key):
        while True:
            await RisingEdge(handle)
            stats[key] += 1

    async def monitor_physical_ram_writes():
        # Observe accepted physical RAM0 commands, not just a client's intent.
        while True:
            await RisingEdge(dut.m0_wr)
            await Timer(1, unit="ns")
            addr = safe_value(dut.m0_addr)
            value = safe_value(dut.m0_val)
            if addr is None:
                continue
            if (0x410 <= addr < 0x450) or (0x510 <= addr < 0x550):
                stats["ram0_output_writes"] += 1
                if value is None:
                    stats["ram0_output_unknown"] += 1
                elif value != 0:
                    stats["ram0_output_nonzero"] += 1
            elif 0x810 <= addr < 0x4810:
                stats["ram0_circular_writes"] += 1
                if value is not None and value != 0:
                    stats["ram0_circular_nonzero"] += 1

    async def monitor_match_completion():
        while True:
            await FallingEdge(dut.match_busy)
            await Timer(1, unit="ns")

            pitch_hz = int(dut.yin_pitch.value) / 4096.0
            note_hz = int(dut.u_match.note_sel.value) / 4096.0
            shift_raw = int(dut.matched_shift.value)

            dut._log.info(
                "MATCH VERIFIED: pitch=%.6f Hz, "
                "selected_note=%.6f Hz, "
                "ideal_ratio=%.6f, hardware_ratio=%.6f",
                pitch_hz,
                note_hz,
                note_hz / pitch_hz,
                shift_raw / 4096.0,
            )

    async def monitor_match_result():
        while True:
            await RisingEdge(dut.match_start)
            dut._log.info(
                "MATCH START: pitch=%.3f Hz, notes=%d",
                int(dut.yin_pitch.value) / 4096,
                int(dut.num_notes.value)
            )

            await RisingEdge(dut.match_busy)
            await FallingEdge(dut.match_busy)

            # Allow the top-level scheduler to capture the result.
            await RisingEdge(dut.clk)
            await Timer(1, unit="ns")

            dut._log.info(
                "MATCH DONE: matched_shift=%.6f, "
                "ratio_current=%.6f",
                int(dut.matched_shift.value) / 4096,
                int(dut.ratio_current.value) / 4096
            )

    watchers = [
        cocotb.start_soon(monitor_input()),
        cocotb.start_soon(monitor_physical_ram_writes()),
        cocotb.start_soon(monitor_starts(dut.resample_start, "resample_starts")),
        cocotb.start_soon(monitor_starts(dut.resample_rd, "resampler_reads")),
        cocotb.start_soon(monitor_starts(dut.resample_wr, "resampler_writes")),
        cocotb.start_soon(monitor_starts(dut.out_start, "output_starts")),
        cocotb.start_soon(monitor_starts(dut.out_read, "output_reads")),
        cocotb.start_soon(monitor_starts(dut.yin_start, "yin_starts")),
        cocotb.start_soon(monitor_starts(dut.yin_rd, "yin_reads")),
        cocotb.start_soon(monitor_starts(dut.yin_wr, "yin_writes")),
        cocotb.start_soon(monitor_starts(dut.yin_cd_start, "yin_cordic_starts")),
        cocotb.start_soon(monitor_starts(dut.match_start, "match_starts")),
        cocotb.start_soon(monitor_match_result()),
        cocotb.start_soon(monitor_match_completion()),
    ]

    pcm_out = []
    failed = None
    try:
        # First channel WS=0, then the active mono channel WS=1.
        # For each channel, bit slots are:
        # slot 0: I2S 1-bit delay; slots 1-24: signed 24-bit MSB first;
        # slots 25-31: padding.  Data is made stable on falling BCK and
        # sampled by the ADC on rising BCK. The DAC changes on falling BCK.
        for sample_idx in range(len(pcm_in) + tail):
            sample = int(pcm_in[sample_idx]) if sample_idx < len(pcm_in) else 0
            raw = sample & 0xFFFFFF
            captured = 0
            for ws in (0, 1):
                dut.adc_ws.value = ws
                dut.dac_ws.value = ws
                for slot in range(SLOTS_PER_CHANNEL):
                    dut.adc_bclk.value = 0
                    dut.dac_bclk.value = 0
                    if ws == 1 and 1 <= slot <= BIT_WIDTH:
                        dut.adc_sd.value = (raw >> (BIT_WIDTH - slot)) & 1
                    else:
                        dut.adc_sd.value = 0
                    await Timer(BCK_HALF_PS, unit="ps")
                    # TX updates SD on the preceding falling edge. Read it
                    # after settling and before the rising BCK edge.
                    if ws == 1 and 1 <= slot <= BIT_WIDTH:
                        bit = safe_value(dut.dac_sd)
                        captured = (captured << 1) | (bit if bit is not None else 0)
                    dut.adc_bclk.value = 1
                    dut.dac_bclk.value = 1
                    await Timer(BCK_HALF_PS, unit="ps")
            pcm_out.append(to_signed24(captured))
            if sample_idx % 16 == 0:
                handler_sample = safe_value(dut.u_output.sample_out)
                if handler_sample is not None:
                    stats["handler_checked"] += 1
                    if handler_sample != 0:
                        stats["handler_nonzero_checks"] += 1
                p = safe_value(dut.debug_pitch)
                r = safe_value(dut.debug_shift)
                stats["trace"].append((sample_idx, round(sample_idx * 1000 / FS, 4),
                                       sample, pcm_out[-1], None if p is None else p / 4096,
                                       None if r is None else r / 4096,
                                       safe_value(dut.overrun_flag), safe_value(dut.missed_frame)))
            # Sample internal YIN progress every 5 ms after the first 80 ms.
            # This adds almost no simulator overhead compared with watching each clock.
            if sample_idx >= (FS * 80 // 1000) and sample_idx % (FS // 200) == 0:
                dut._log.info(
                    "YIN @ %.1f ms: %s | busy=%s, "
                    "starts=%d, rd=%d, wr=%d, cordic=%d, missed=%s",
                    sample_idx * 1000 / FS, yin_snapshot(dut),
                    safe_value(dut.yin_busy), stats["yin_starts"],
                    stats["yin_reads"], stats["yin_writes"],
                    stats["yin_cordic_starts"], safe_value(dut.missed_frame),
                )
            if sample_idx and sample_idx % (FS // 10) == 0:
                dut._log.info("Simulated %.1f ms of audio, YIN starts=%d, resampler starts=%d",
                              sample_idx * 1000 / FS, stats["yin_starts"],
                              stats["resample_starts"])
    except Exception as e:
        failed = e
    finally:
        for watcher in watchers:
            watcher.cancel()
        # Always write the captured samples, even if a later assertion fails.
        write_pcm24_wav(output_path, pcm_out)
        dut._log.info("Wrote %d output samples to %s", len(pcm_out), output_path)
        if trace_path:
            trace_file = Path(trace_path)
            trace_file.parent.mkdir(parents=True, exist_ok=True)
            with trace_file.open("w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(("sample_index", "time_ms", "adc_sample", "dac_sample",
                                 "pitch_hz", "shift_ratio", "overrun", "missed_frame"))
                writer.writerows(stats["trace"])
            dut._log.info("Wrote debug trace to %s", trace_file)

    if failed is not None:
        raise failed

    rx = stats["rx_samples"]
    compared = min(len(rx), len(pcm_in))
    mismatches = [(i, int(pcm_in[i]), rx[i]) for i in range(compared)
                  if int(pcm_in[i]) != rx[i]]
    dut._log.info("INPUT RX: %d transactions / %d audio samples; mismatches=%d/%d",
                  stats["input_transactions"], len(pcm_in), len(mismatches), compared)
    for i, expected, actual in mismatches[:5]:
        dut._log.warning("RX framing mismatch sample %d: expected=%d (0x%06X), got=%d (0x%06X)",
                         i, expected, expected & 0xFFFFFF, actual, actual & 0xFFFFFF)
    dut._log.info("RESAMPLER starts=%d, YIN starts=%d, MATCH starts=%d, overrun=%s, missed_frame=%s",
                  stats["resample_starts"], stats["yin_starts"], stats["match_starts"],
                  safe_value(dut.overrun_flag), safe_value(dut.missed_frame))
    dut._log.info(
        "OUTPUT DEBUG: starts=%d, reads=%d, resampler_reads=%d, resampler_writes=%d",
        stats["output_starts"], stats["output_reads"],
        stats["resampler_reads"], stats["resampler_writes"],
    )
    dut._log.info(
        "RAM0 DATA DEBUG: circular_writes=%d (nonzero=%d), "
        "output_bank_writes=%d (nonzero=%d, unknown=%d), "
        "output_handler_nonzero=%d/%d checks",
        stats["ram0_circular_writes"], stats["ram0_circular_nonzero"],
        stats["ram0_output_writes"], stats["ram0_output_nonzero"],
        stats["ram0_output_unknown"], stats["handler_nonzero_checks"],
        stats["handler_checked"],
    )
    dut._log.info(
        "YIN FINAL DEBUG: %s | read_requests=%d, write_requests=%d, "
        "cordic_starts=%d, starts=%d, busy=%s",
        yin_snapshot(dut), stats["yin_reads"], stats["yin_writes"],
        stats["yin_cordic_starts"], stats["yin_starts"], safe_value(dut.yin_busy),
    )
    dut._log.info(
        "PIPELINE STATUS: resample_busy=%s, output_ready=%s, yin_busy=%s, "
        "note_busy=%s, match_busy=%s, notes=%s",
        safe_value(dut.resample_busy), safe_value(dut.out_ready),
        safe_value(dut.yin_busy), safe_value(dut.note_busy),
        safe_value(dut.match_busy), safe_value(dut.debug_num_notes),
    )
    if not rx:
        dut._log.warning("No RX write transactions observed. Check I2S framing, startup/reset and the top scheduler")
    if stats["resample_starts"] == 0:
        dut._log.warning("No resampling observed. The top has a 2048-input-sample priming threshold")
    if stats["resampler_writes"] == 0 and stats["resample_starts"] > 0:
        dut._log.warning("Resampler was started but issued no output-buffer write requests")
    if stats["output_starts"] == 0 and stats["resample_starts"] > 0:
        dut._log.warning("Resampler was started but Output_Handler was never triggered")
    if stats["output_starts"] > 0 and stats["output_reads"] == 0:
        dut._log.warning("Output_Handler was triggered but never issued a RAM0 read request")
    if stats["yin_starts"] == 0:
        dut._log.warning("No YIN detection observed. The top requires 480 decimated samples (~80 ms) first")
    # An output file can be valid WAV yet contain complete silence. Report it.
    check_start = 2048  # top holds off resampling for this many input samples
    output_region = np.asarray(pcm_out[check_start:], dtype=np.float64)
    input_region = np.asarray(pcm_in[check_start:min(len(pcm_in), len(pcm_out))], dtype=np.float64)
    rms_in = float(np.sqrt(np.mean(input_region ** 2))) if len(input_region) else 0.0
    rms_out = float(np.sqrt(np.mean(output_region ** 2))) if len(output_region) else 0.0
    dut._log.info("After 2048-sample priming: input RMS=%.1f, output RMS=%.1f (24-bit LSB)", rms_in, rms_out)
    if rms_in > 0 and rms_out == 0:
        dut._log.warning("DAC PCM is completely silent after startup: the WAV was written, but audio processing did not produce data")
    if strict_output and rms_in > 0:
        assert rms_out > 0, "DAC output is silent after resampler priming despite nonzero audio input"
    if strict_rx:
        assert not mismatches and len(rx) >= len(pcm_in), (
            f"RX captured {len(rx)}/{len(pcm_in)} samples and {len(mismatches)} mismatches. "
            "Inspect I2S_Rx_Slave.sv: it does not stop at 24 serial data bits."
        )

