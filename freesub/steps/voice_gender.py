"""Guess a speaker's gender from voice pitch (from Japo): used for forms of address and dubbing voices."""
import numpy as np

MALE_MAX_HZ = 160      # median pitch below this -> male
FEMALE_MIN_HZ = 175    # above this -> female (in between = unknown)


def estimate_pitch(audio, sr=16000):
    """Median voice pitch (Hz) of a segment via frame autocorrelation, or None."""
    frame, hop = int(0.04 * sr), int(0.02 * sr)
    if audio is None or len(audio) < frame * 3:
        return None
    n = 1 + (len(audio) - frame) // hop
    idx = np.arange(frame)[None, :] + hop * np.arange(n)[:, None]
    fr = audio[idx] * np.hanning(frame)[None, :]
    energy = (fr ** 2).mean(axis=1)
    fr = fr[energy > max(np.percentile(energy, 40), 1e-7)]
    if len(fr) < 5:
        return None
    fr = fr - fr.mean(axis=1, keepdims=True)
    spec = np.fft.rfft(fr, 2 * frame, axis=1)
    ac = np.fft.irfft(np.abs(spec) ** 2, axis=1)[:, :frame]
    ac = ac / np.maximum(ac[:, :1], 1e-12)
    lo, hi = int(sr / 400), int(sr / 70)          # 70-400 Hz
    seg = ac[:, lo:hi]
    lag = seg.argmax(axis=1) + lo
    voiced = seg.max(axis=1) > 0.45
    if voiced.sum() < 5:
        return None
    return float(np.median(sr / lag[voiced]))


def classify(audio, sr=16000):
    """'f', 'm' or None."""
    try:
        f0 = estimate_pitch(audio, sr)
    except Exception:  # noqa: BLE001
        return None
    if f0 is None:
        return None
    if f0 >= FEMALE_MIN_HZ:
        return "f"
    if f0 <= MALE_MAX_HZ:
        return "m"
    return None
