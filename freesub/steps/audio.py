"""Step: video -> audio.wav (16 kHz mono, what Whisper uses)."""
import logging
import os
import wave

import numpy as np

from ..core import ffmpeg
from ..core.project import AUDIO, Project
from ..log import step_timer

log = logging.getLogger(__name__)
RATE = 16000


def extract(project: Project, control, stream: int | None = None, force: bool = False) -> str:
    out = project.audio
    if not force and project.is_done("audio", AUDIO) and \
            os.path.getmtime(out) >= os.path.getmtime(project.video):
        control.progress(1.0, "Audio already extracted")
        return out
    project.ensure()
    info = project.meta.get("info") or {}
    if not info.get("duration"):
        info = ffmpeg.probe(project.video)
        project.meta["info"] = info
    if not info.get("audio_streams"):
        raise RuntimeError("This file has no audio track")
    with step_timer("audio", project.video):
        project.mark("audio", "running")
        tmp = project.path("audio.part.wav")
        args = ["-i", project.video, "-vn", "-sn", "-dn"]
        if stream is None:
            stream = project.meta.get("audio_stream")       # chosen in the Source panel
        if stream is not None:
            args += ["-map", f"0:{stream}"]
        else:
            args += ["-map", "0:a:0"]
        args += ["-ac", "1", "-ar", str(RATE), "-c:a", "pcm_s16le", tmp]
        try:
            ffmpeg.run(args, duration=info.get("duration") or 0, control=control, label="Extracting audio")
        except BaseException:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            project.mark("audio", "stopped" if control.stopped else "failed")
            raise
        os.replace(tmp, out)
        project.mark("audio", "done", file=AUDIO)
    control.progress(1.0, "Audio extracted")
    return out


def load(path: str, start: float = 0.0, end: float | None = None) -> np.ndarray:
    """Read (part of) a 16-bit mono wav as float32 -1..1."""
    with wave.open(path, "rb") as w:
        rate, n = w.getframerate(), w.getnframes()
        a = int(max(0.0, start) * rate)
        b = n if end is None else min(n, int(end * rate))
        if b <= a:
            return np.zeros(0, dtype=np.float32)
        w.setpos(a)
        raw = w.readframes(b - a)
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
