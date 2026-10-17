"""Hear the narration while it is still being made (the mic button on the preview).

The narration step saves every finished line on its own (dub_live/<line>_<start>_<slot>.wav). This watches
that folder; while the video plays, the lines that belong to the current time are mixed into one sound
stream (audio_engine.py: no extra media players to start, stop or move), and the video's own sound is turned
down under them (as in the exported mix). The parts of the timeline whose lines are ready are reported with
`ready_changed`, so the slider can light them up.
When the whole narration is done, the window switches to the full mix (mix_audio.MixAudio).
"""
import bisect
import logging
import os
import wave

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from .audio_engine import RATE

log = logging.getLogger(__name__)
RAMP = 0.15


class LiveNarration(QObject):
    ready_changed = Signal(list)        # [(from, to)] seconds whose narration is ready

    def __init__(self, main: QMediaPlayer, main_out: QAudioOutput, master, engine, parent=None):
        super().__init__(parent)
        self.main, self.main_out, self.master, self.engine = main, main_out, master, engine
        self.on = False
        self.folder = ""
        self.lines = {}                 # line -> (start, end, slot0, slot1, path)
        self.clips = {}                 # line -> float32 mono samples (read once)
        self.starts, self.order = [], []
        self.poll = QTimer(self)
        self.poll.setInterval(400)
        self.poll.timeout.connect(self.scan)
        self.tick = QTimer(self)        # the video's own sound goes down under a line
        self.tick.setInterval(40)
        self.tick.timeout.connect(self._duck)
        main.playbackStateChanged.connect(self._state)

    # ---- public
    def start(self, folder: str):
        self.stop()
        self.folder = folder
        self.lines, self.clips, self.starts, self.order = {}, {}, [], []
        self.on = True
        self.ready_changed.emit([])
        self.poll.start()
        self.scan()
        self.engine.play(self._source)
        self._state(self.main.playbackState())

    def stop(self, release=False):      # noqa: ARG002  (the files are read whole: nothing is held open)
        """Stop listening (the narration may still be being made)."""
        was = self.on
        self.on = False
        self.poll.stop()
        self.tick.stop()
        if self.engine.source == self._source:
            self.engine.stop()
        if was:
            self.main_out.setVolume(self.master())

    def hold(self, on: bool):
        """The timeline is being dragged: silent until it is let go."""
        self.engine.quiet = on

    def clear(self):
        self.stop()
        self.lines, self.clips, self.starts, self.order = {}, {}, [], []
        self.ready_changed.emit([])

    # ---- the folder
    def scan(self):
        from ..steps.dub import parse_live_name
        try:
            names = [n for n in os.listdir(self.folder) if n.endswith(".wav")]
        except OSError:
            return
        new = False
        for name in names:
            info = parse_live_name(name)
            if not info or info[0] in self.lines:
                continue
            path = os.path.join(self.folder, name)
            try:
                with wave.open(path, "rb") as w:
                    if w.getframerate() != RATE:
                        continue
                    raw = w.readframes(w.getnframes())
                    ch = w.getnchannels()
            except (OSError, EOFError, wave.Error):
                continue
            a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            if ch > 1:
                a = a.reshape(-1, ch).mean(axis=1)
            i, start, s0, s1 = info
            self.clips[i] = a
            self.lines[i] = (start, start + len(a) / RATE, s0, s1, path)
            new = True
        if new:
            self.order = sorted(self.lines, key=lambda k: self.lines[k][0])
            self.starts = [self.lines[k][0] for k in self.order]
            self.ready_changed.emit(self.spans())

    def spans(self):
        """The ready parts of the timeline, neighbouring lines joined."""
        out = []
        for k in sorted(self.lines, key=lambda k: self.lines[k][2]):
            a, b = self.lines[k][2], self.lines[k][3]
            if out and a <= out[-1][1] + 0.05:
                out[-1][1] = max(out[-1][1], b)
            else:
                out.append([a, b])
        return [tuple(x) for x in out]

    # ---- sound
    def _source(self, k, n):
        """The narration lines heard from sample k (n samples), at the Narration volume."""
        from ..steps.dub import mix_levels
        out = np.zeros(n, dtype=np.float32)
        t0, t1 = k / RATE, (k + n) / RATE
        j = bisect.bisect_right(self.starts, t1)
        for idx in range(max(0, j - 4), j):            # lines start in order; a few back may still sound
            line = self.order[idx]
            start, end = self.lines[line][0], self.lines[line][1]
            if end <= t0:
                continue
            clip = self.clips[line]
            c0 = int(round(start * RATE))
            a, b = max(k, c0), min(k + n, c0 + len(clip))
            if b > a:
                out[a - k:b - k] += clip[a - c0:b - c0]
        out *= mix_levels()["narr"]
        return np.repeat(out[:, None], 2, axis=1)

    def _state(self, st):
        if not self.on:
            return
        if st == QMediaPlayer.PlayingState:
            self.tick.start()
            self._duck()
        else:
            self.tick.stop()

    def _duck(self):
        """The video's own sound: the Background volume, lower while a line is read (as in the export)."""
        if not self.on:
            return
        from ..steps.dub import mix_levels
        lv = mix_levels()
        t = self.main.position() / 1000
        g = 1.0
        if lv["duck"] < 1.0 and self.starts:
            k = bisect.bisect_right(self.starts, t + RAMP) - 1
            for idx in (k - 1, k, k + 1):
                if 0 <= idx < len(self.order):
                    a, b = self.lines[self.order[idx]][0], self.lines[self.order[idx]][1]
                    if a <= t <= b:
                        g = min(g, lv["duck"])
                    elif a - RAMP < t < a:
                        g = min(g, 1 - (1 - lv["duck"]) * (t - (a - RAMP)) / RAMP)
                    elif b < t < b + RAMP:
                        g = min(g, lv["duck"] + (1 - lv["duck"]) * (t - b) / RAMP)
        self.main_out.setVolume(max(0.0, min(1.0, lv["bg"] * g * self.master())))
