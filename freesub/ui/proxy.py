"""Playable preview copies for videos Qt's player cannot show.

Qt's video player has no software AV1 decoder: on a graphics card without AV1 decoding (anything before RTX 30
/ RX 6000 / Intel Arc) an AV1 video stays black. FreeSub's own ffmpeg can read it, so it makes a small H.264
copy (preview.mp4 in the project's work folder) for the preview only. Export, OCR, voice separation... still
use the original file, and the timing is the same.

The copy is written as a fragmented MP4 (a 1-second piece at a time), so the preview can play the part made so
far while the rest is still being made. preview.done marks a finished copy.
"""
import logging
import os

from PySide6.QtCore import QObject, QProcess, Signal

from ..core import ffmpeg
from ..core.project import Project

log = logging.getLogger(__name__)
NEEDS_COPY = {"av1"}                # codecs the player cannot decode without the graphics card
PREVIEW = "preview.mp4"
DONE = "preview.done"


def needs_copy(info: dict) -> bool:
    return bool(info.get("has_video")) and (info.get("vcodec") or "").lower() in NEEDS_COPY


def ready_copy(video: str) -> str | None:
    p = Project(video)
    path = p.path(PREVIEW)
    try:
        if p.has(PREVIEW) and os.path.isfile(p.path(DONE)) and os.path.getmtime(path) >= os.path.getmtime(video):
            return path
    except OSError:
        pass
    return None


class ProxyMaker(QObject):
    progress = Signal(str, float, float)   # video, 0..1, seconds that can be played already
    ready = Signal(str, str)        # video, preview copy
    failed = Signal(str, str)       # video, message

    def __init__(self, parent=None):
        super().__init__(parent)
        self.jobs = {}              # video -> (QProcess, tmp path, duration)

    def busy(self, video) -> bool:
        return video in self.jobs

    def make(self, video: str, duration: float):
        if video in self.jobs:
            return
        exe = ffmpeg.find("ffmpeg")
        if not exe:
            self.failed.emit(video, "ffmpeg was not found")
            return
        p = Project(video)
        p.ensure()
        out = p.path(PREVIEW)
        for f in (p.path(DONE), out):
            try:
                os.remove(f)
            except OSError:
                pass
        tmp = out                                   # written in place: the preview reads it while it grows
        args = ["-hide_banner", "-nostats", "-loglevel", "error", "-y", "-i", video,
                "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn",
                "-vf", "scale=-2:'min(540,ih)':flags=bilinear",
                "-c:v", "libx264", "-preset", "ultrafast", "-tune", "fastdecode", "-crf", "26",
                "-g", "30", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k", "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
                "-f", "mp4", "-progress", "pipe:1", tmp]
        proc = QProcess(self)
        proc.setProgram(exe)
        proc.setArguments(args)
        proc.readyReadStandardOutput.connect(lambda v=video: self._read(v))
        proc.finished.connect(lambda code, _status, v=video: self._done(v, code))
        self.jobs[video] = (proc, tmp, out, max(0.1, duration or 0.1))
        log.info("Making a preview copy of %s (its %s video cannot be shown by the player)", video,
                 "AV1")
        proc.start()

    def _read(self, video):
        job = self.jobs.get(video)
        if not job:
            return
        proc, _tmp, _out, dur = job
        text = bytes(proc.readAllStandardOutput()).decode(errors="replace")
        for line in text.splitlines():
            if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                try:
                    t = int(line.split("=", 1)[1]) / 1e6
                except ValueError:
                    continue
                self.progress.emit(video, max(0.0, min(1.0, t / dur)), max(0.0, t - 1.0))

    def _done(self, video, code):
        job = self.jobs.pop(video, None)
        if not job:
            return
        proc, tmp, out, _dur = job
        if code == 0 and os.path.isfile(out) and os.path.getsize(out) > 0:
            with open(out[:-len(PREVIEW)] + DONE, "w") as f:
                f.write("ok")
            log.info("Preview copy ready: %s", out)
            self.ready.emit(video, out)
        else:
            err = bytes(proc.readAllStandardError()).decode(errors="replace").strip()[-300:]
            try:
                os.remove(tmp)
            except OSError:
                pass
            log.warning("Could not make the preview copy: %s", err or code)
            self.failed.emit(video, err or f"ffmpeg exit code {code}")

    def stop_all(self):
        for proc, tmp, _out, _d in list(self.jobs.values()):
            proc.kill()
            proc.waitForFinished(2000)
            try:
                os.remove(tmp)
            except OSError:
                pass
        self.jobs.clear()
