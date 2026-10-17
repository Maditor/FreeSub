"""Hear the narration mix in the preview before exporting, with the volumes changing live.

The narration track (dub.wav), the background (music.wav, or the whole original sound without voice
separation) and the original voice (voice.wav = original sound - music) are mixed here, sample by sample,
for the time the video is at, and played through one sound stream (audio_engine.py). The video's own sound
is muted meanwhile. The volumes are the Narration card's, and the original voice goes down while a narration
line plays, exactly like the exported mix (steps/dub.py)."""
import logging

import numpy as np
from PySide6.QtCore import QObject
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from .audio_engine import RATE, stereo_slice, wav_frames

log = logging.getLogger(__name__)
RAMP = 0.15


class MixAudio(QObject):
    def __init__(self, main: QMediaPlayer, main_out: QAudioOutput, engine, parent=None):
        super().__init__(parent)
        self.main, self.main_out, self.engine = main, main_out, engine
        self.on = False
        self.tracks = {}            # name -> int16 array (frames, channels), memory-mapped
        self._maps = []
        self.spans = []             # narration lines, in samples
        self.levels = {"narr": 1.0, "bg": 1.0, "voice": 1.0, "duck": 0.3}
        self.has_voice = False

    def ready(self) -> bool:
        return bool(self.tracks)

    def load(self, sources: dict | None, spans=None):
        """sources: {"narr": path, "bg": path, "voice": path or None}; spans: narration lines [(start, end)]."""
        was_on = self.on
        self.clear()
        if not sources:
            return
        for name in ("narr", "bg", "voice"):
            path = sources.get(name)
            if not path:
                continue
            try:
                arr, rate = wav_frames(path)
            except (OSError, ValueError) as e:
                log.warning("Narration preview: cannot read %s (%s)", path, e)
                continue
            if rate != RATE:
                log.warning("Narration preview: %s is %d Hz, not %d", path, rate, RATE)
                continue
            self.tracks[name] = arr
            self._maps.append(arr)
        self.has_voice = "voice" in self.tracks
        self.spans = sorted((int(a * RATE), int(b * RATE)) for a, b in (spans or []))
        self.refresh_levels()
        if was_on and self.ready():
            self.set_enabled(True)

    def clear(self):
        if self.engine.source == self._source:
            self.engine.stop()
        self.on = False
        self.tracks = {}
        for m in self._maps:          # let go of the files now (Windows keeps a mapped file locked)
            base = m
            while getattr(base, "base", None) is not None and not hasattr(base, "_mmap"):
                base = base.base
            mm = getattr(base, "_mmap", None)
            if mm is not None:
                try:
                    mm.close()
                except Exception:  # noqa: BLE001
                    pass
        self._maps = []

    def set_enabled(self, on: bool):
        self.on = on and self.ready()
        self.main_out.setMuted(self.on)
        if self.on:
            self.engine.play(self._source)
        elif self.engine.source == self._source:
            self.engine.stop()

    def hold(self, on: bool):
        """The timeline is being dragged: silent until it is let go."""
        self.engine.quiet = on

    def refresh_levels(self):
        from ..steps.dub import mix_levels
        self.levels = mix_levels()

    def _source(self, k, n):
        from ..steps.dub import duck_curve
        lv = self.levels
        narr = stereo_slice(self.tracks.get("narr"), k, n)
        bg = stereo_slice(self.tracks.get("bg"), k, n)
        g = duck_curve(self.spans, k, n, lv["duck"], int(RAMP * RATE))[:, None]
        if self.has_voice:
            x = bg * lv["bg"] + stereo_slice(self.tracks.get("voice"), k, n) * (lv["voice"] * g)
        else:                                       # no separation: the whole original sound ducks
            x = bg * (lv["bg"] * g)
        return (x + narr * lv["narr"]).astype(np.float32)
