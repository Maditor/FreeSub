"""ffmpeg / ffprobe as separate programs: locate them, probe media, run with progress."""
import json
import logging
import os
import sys
import re
import shutil
import subprocess
import threading

from ..config import app_dir, data_dir, settings
from .jobs import Cancelled

log = logging.getLogger(__name__)

VIDEO_EXTS = (".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".flv", ".wmv", ".ts", ".mts", ".m2ts", ".mpg",
              ".mpeg", ".3gp")
AUDIO_EXTS = (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma")
MEDIA_EXTS = VIDEO_EXTS + AUDIO_EXTS

# hide the console window of child processes on Windows
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _exe(name: str) -> str:
    return name + (".exe" if os.name == "nt" else "")


def find(tool: str = "ffmpeg") -> str | None:
    """Path of ffmpeg / ffprobe: settings.ffmpeg_path, then ffmpeg\\bin next to the app, then PATH."""
    custom = (settings().get("ffmpeg_path") or "").strip().strip('"')
    cands = []
    if custom:
        folder = custom if os.path.isdir(custom) else os.path.dirname(custom)
        cands += [os.path.join(folder, _exe(tool)), os.path.join(folder, "bin", _exe(tool))]
    for here in (app_dir(), data_dir()):
        cands += [os.path.join(here, "ffmpeg", "bin", _exe(tool)), os.path.join(here, "ffmpeg", _exe(tool))]
    for c in cands:
        if os.path.isfile(c):
            return c
    return shutil.which(tool)


def available() -> bool:
    return bool(find("ffmpeg") and find("ffprobe"))


def version() -> str:
    exe = find("ffmpeg")
    if not exe:
        return ""
    try:
        out = subprocess.run([exe, "-hide_banner", "-version"], capture_output=True, text=True, timeout=10,
                             creationflags=_NO_WINDOW).stdout
        return out.splitlines()[0] if out else ""
    except Exception:  # noqa: BLE001
        return ""


class FFmpegMissing(RuntimeError):
    def __init__(self):
        super().__init__("ffmpeg not found. Open FreeSub again to download it, or set its folder in Settings."
                         if getattr(sys, "frozen", False) else
                         "ffmpeg not found. Run setup.bat (it downloads ffmpeg) or set its folder in Settings.")


def probe(path: str) -> dict:
    """Duration, video size / fps and stream list of a media file."""
    exe = find("ffprobe")
    if not exe:
        raise FFmpegMissing()
    r = subprocess.run([exe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
                       creationflags=_NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {r.stderr.strip()[:300]}")
    data = json.loads(r.stdout or "{}")
    fmt = data.get("format", {})
    info = {"duration": float(fmt.get("duration") or 0), "width": 0, "height": 0, "fps": 0.0,
            "has_video": False, "audio_streams": [], "subtitle_streams": []}
    for s in data.get("streams", []):
        kind = s.get("codec_type")
        if kind == "video" and not info["has_video"] and s.get("disposition", {}).get("attached_pic") != 1:
            info.update(has_video=True, width=int(s.get("width") or 0), height=int(s.get("height") or 0),
                        vcodec=s.get("codec_name", ""))
            try:
                num, den = (s.get("avg_frame_rate") or "0/1").split("/")
                info["fps"] = float(num) / float(den) if float(den) else 0.0
            except (ValueError, ZeroDivisionError):
                pass
            if not info["duration"]:
                info["duration"] = float(s.get("duration") or 0)
        elif kind == "audio":
            info["audio_streams"].append({"index": s.get("index"), "codec": s.get("codec_name"),
                                          "lang": (s.get("tags") or {}).get("language", "")})
        elif kind == "subtitle":
            info["subtitle_streams"].append({"index": s.get("index"), "codec": s.get("codec_name"),
                                             "lang": (s.get("tags") or {}).get("language", "")})
    return info


_TIME = re.compile(r"out_time_(?:us|ms)=(\d+)")


def run(args: list, duration: float = 0.0, control=None, label: str = "ffmpeg") -> None:
    """Run ffmpeg with `args` (without the exe), report progress from -progress, stop on Cancel."""
    exe = find("ffmpeg")
    if not exe:
        raise FFmpegMissing()
    cmd = [exe, "-hide_banner", "-nostdin", "-y", "-progress", "pipe:1", "-nostats", "-loglevel", "error"] + args
    log.info("ffmpeg %s", " ".join(f'"{a}"' if " " in str(a) else str(a) for a in args))
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                         errors="replace", creationflags=_NO_WINDOW)
    err_lines = []

    def read_err():
        for line in p.stderr:
            err_lines.append(line.rstrip())
            del err_lines[:-40]

    threading.Thread(target=read_err, daemon=True).start()
    try:
        for line in p.stdout:
            if control is not None:
                if control.stopped:
                    p.kill()
                    raise Cancelled()
                if control.paused:            # ffmpeg itself keeps running; we just wait for the user
                    control.check()
            m = _TIME.match(line.strip())
            if m and duration and control is not None:
                secs = int(m.group(1)) / 1_000_000
                control.progress(secs / duration, f"{label} {secs / duration * 100:.0f}%")
        p.wait()
    except Cancelled:
        p.wait()
        raise
    if p.returncode != 0:
        # the first real error says what went wrong; the last lines only say that it stopped
        first = [l for l in err_lines if re.search(r"Invalid|[Ee]rror|No such|not found|Unable|[Ff]ailed", l)][:3]
        msg = "\n".join(dict.fromkeys(first + err_lines[-3:])) or f"exit code {p.returncode}"
        raise RuntimeError(f"ffmpeg failed: {msg}")
