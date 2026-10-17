"""One audio stream for the narration preview, mixed by FreeSub itself and written to the sound card.

Why not a few more media players: following a video with extra QMediaPlayers means pausing and moving them
on every jump of the video, and Qt's media backend can hang for good when a player is moved while paused or
moved again before the last move is done (the window then stops responding). Here nothing is "moved": the
sound is computed from the files (numpy) for the time the video is at, and pushed to one QAudioSink. A jump
of the video just changes the place the next samples are read from.

source(k, n) -> float32 array (n, 2): the sound from sample k (44.1 kHz) on.
"""
import logging
import struct

import numpy as np
from PySide6.QtCore import QObject, QTimer
from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices, QMediaPlayer

log = logging.getLogger(__name__)
RATE = 44100
BUFFER = 0.30          # seconds of sound waiting in the sound card's queue
DRIFT = 0.25           # further off than this from the video (a jump): read from the right place again


def wav_frames(path):
    """A wav file as a read-only array (frames, channels) of int16 without loading it (memory map)."""
    with open(path, "rb") as f:
        head = f.read(12)
        if head[:4] != b"RIFF" or head[8:12] != b"WAVE":
            raise ValueError("not a wav file")
        ch, rate, bits, offset, size = 1, RATE, 16, None, 0
        while True:
            hdr = f.read(8)
            if len(hdr) < 8:
                break
            cid, ln = hdr[:4], struct.unpack("<I", hdr[4:])[0]
            if cid == b"fmt ":
                fmt = f.read(ln)
                ch, rate = struct.unpack("<HI", fmt[2:8])
                bits = struct.unpack("<H", fmt[14:16])[0]
            elif cid == b"data":
                offset, size = f.tell(), ln
                break
            else:
                f.seek(ln + (ln & 1), 1)
    if offset is None or bits != 16:
        raise ValueError("unsupported wav")
    import os
    size = min(size, os.path.getsize(path) - offset)
    frames = size // (2 * ch)
    data = np.memmap(path, dtype=np.int16, mode="r", offset=offset, shape=(frames * ch,))
    return data.reshape(frames, ch), rate


def stereo_slice(arr, k, n):
    """float32 (n, 2) of arr from frame k (zeros past the end)."""
    out = np.zeros((n, 2), dtype=np.float32)
    if arr is None or k >= len(arr) or k + n <= 0:
        return out
    a, b = max(0, k), min(len(arr), k + n)
    x = np.asarray(arr[a:b], dtype=np.float32) / 32768.0
    if x.shape[1] == 1:
        x = np.repeat(x, 2, axis=1)
    out[a - k:b - k] = x[:, :2]
    return out


class AudioEngine(QObject):
    def __init__(self, main: QMediaPlayer, parent=None):
        super().__init__(parent)
        self.main = main
        self.source = None
        self.volume = 1.0
        self.muted = False
        self.quiet = False            # silent for a moment (the timeline is being dragged)
        fmt = QAudioFormat()
        fmt.setSampleRate(RATE)
        fmt.setChannelCount(2)
        fmt.setSampleFormat(QAudioFormat.Int16)
        self.fmt = fmt
        self.sink = None
        self.io = None
        self.pos = 0                  # the next sample to write
        self.timer = QTimer(self)
        self.timer.setInterval(15)
        self.timer.timeout.connect(self._pump)
        self.failed = False
        self._playing = False

    def _open(self):
        if self.sink is None:
            dev = QMediaDevices.defaultAudioOutput()
            self.sink = QAudioSink(dev, self.fmt, self)
            self.sink.setBufferSize(int(RATE * 4 * BUFFER))
        if self.io is None:
            self.io = self.sink.start()
            if self.io is None:
                raise RuntimeError("no sound output")
            self.pos = self._want(0)

    def play(self, source):
        """Start pushing sound from source (it follows the video from now on)."""
        self.source = source
        try:
            self._open()
        except Exception as e:  # noqa: BLE001        (tried again next time: a headset may be plugged in)
            self.sink, self.io = None, None
            if not self.failed:
                log.warning("Narration preview: no sound output (%s)", e)
            self.failed = True
            return
        self.failed = False
        self.timer.start()

    def stop(self):
        self.timer.stop()
        self.source = None
        if self.sink is not None and self.io is not None:
            try:
                self.sink.stop()
            except Exception:  # noqa: BLE001
                pass
        self.io = None

    def active(self) -> bool:
        return self.source is not None and self.timer.isActive()

    def _buffered(self) -> float:
        try:
            return max(0, self.sink.bufferSize() - self.sink.bytesFree()) / 4 / RATE
        except Exception:  # noqa: BLE001
            return 0.0

    def _want(self, buffered):
        """The sample the next write must start with: where the video will be when it is heard."""
        return int((self.main.position() / 1000 + buffered) * RATE)

    def _flush(self):
        """Drop what is still queued (after a pause or a jump it belongs to the wrong moment)."""
        try:
            self.sink.stop()
            self.io = self.sink.start()
        except Exception:  # noqa: BLE001
            self.io = None
        self.pos = self._want(0)

    def _pump(self):
        if self.source is None or self.io is None:
            return
        if self.main.playbackState() != QMediaPlayer.PlayingState:
            if self._playing:                           # just paused: stop at once, not after the queue
                self._playing = False
                self._flush()
            return
        self._playing = True
        buffered = self._buffered()
        want = self._want(buffered)
        if abs(self.pos - want) > DRIFT * RATE:          # the video jumped: start over from there
            self._flush()
            if self.io is None:
                return
        n = min(self.sink.bytesFree() // 4, int(RATE * 0.1))
        if n <= 0:
            return
        try:
            x = self.source(self.pos, n)
        except Exception as e:  # noqa: BLE001
            log.warning("Narration preview: %s", e)
            x = np.zeros((n, 2), dtype=np.float32)
        gain = 0.0 if (self.muted or self.quiet) else self.volume
        data = (np.clip(x * gain, -1, 1) * 32767).astype(np.int16).tobytes()
        self.io.write(data)
        self.pos += n
