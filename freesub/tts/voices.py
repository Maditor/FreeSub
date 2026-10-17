"""Text to speech into a file: Piper (offline, on this PC) or Microsoft Edge voices (online, free).
Used by the Voice step's test box now and by dubbing later (milestone 4)."""
import asyncio
import logging
import os
import threading
import wave

import numpy as np

from ..config import models_dir
from ..core.models import download

log = logging.getLogger(__name__)

HF = "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
# language -> (path in rhasspy/piper-voices, size, label)
PIPER = {
    "vi": ("vi/vi_VN/vais1000/medium/vi_VN-vais1000-medium", "63 MB", "Vietnamese · vais1000 (female)"),
    "en": ("en/en_US/lessac/medium/en_US-lessac-medium", "63 MB", "English · lessac (female)"),
}
EDGE = {
    "vi": [("vi-VN-HoaiMyNeural", "Hoài My (female)"), ("vi-VN-NamMinhNeural", "Nam Minh (male)")],
    "en": [("en-US-AvaNeural", "Ava (female)"), ("en-US-AndrewNeural", "Andrew (male)")],
}
_LOCK = threading.Lock()
_VOICES = {}


# ------------------------------------------------------------------ Piper
def piper_installed() -> bool:
    try:
        import piper  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def piper_path(lang: str) -> str:
    return os.path.join(models_dir(), "piper", os.path.basename(PIPER[lang][0]) + ".onnx")


def piper_ready(lang: str) -> bool:
    p = piper_path(lang)
    return os.path.isfile(p) and os.path.isfile(p + ".json")


def piper_download(lang: str, control=None):
    rel, size, _label = PIPER[lang]
    dest = piper_path(lang)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    for suffix in (".onnx.json", ".onnx"):
        target = dest + (".json" if suffix == ".onnx.json" else "")
        if os.path.isfile(target):
            continue

        def prog(got, total, name=os.path.basename(target)):
            if control is not None:
                control.progress(got / total if total else None, f"Voice {name} · {got / 1e6:.0f} MB")
        download(HF + rel + suffix, target, progress=prog, control=control)


def _piper_voice(lang):
    p = piper_path(lang)
    if p not in _VOICES:
        from piper import PiperVoice
        _VOICES[p] = PiperVoice.load(p, config_path=p + ".json")
    return _VOICES[p]


def piper_synth(text: str, lang: str, path: str, speed: int = 100):
    from piper import SynthesisConfig
    with _LOCK:
        voice = _piper_voice(lang)
        base = voice.config.length_scale or 1.0
        cfg = SynthesisConfig(length_scale=base * 100.0 / max(50, min(200, speed)))
        chunks, rate = [], 22050
        for ch in voice.synthesize(text, syn_config=cfg):
            chunks.append(ch.audio_int16_array)
            rate = ch.sample_rate
    audio = np.concatenate(chunks) if chunks else np.zeros(1, dtype=np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(audio.astype(np.int16).tobytes())
    return path


# ------------------------------------------------------------------ Edge (online)
def edge_installed() -> bool:
    try:
        import edge_tts  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def edge_synth(text: str, voice: str, path: str, speed: int = 100):
    import edge_tts
    rate = f"{speed - 100:+d}%"

    async def go():
        await edge_tts.Communicate(text, voice, rate=rate).save(path)
    asyncio.run(go())
    if not os.path.isfile(path) or os.path.getsize(path) < 100:
        raise RuntimeError("The online voice returned no audio")
    return path
