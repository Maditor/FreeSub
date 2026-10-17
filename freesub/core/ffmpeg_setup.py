"""Download a ready-made ffmpeg build for Windows into ffmpeg\\bin next to the app (used by setup.bat).

ffmpeg stays a separate program (FreeSub only runs ffmpeg.exe / ffprobe.exe); its licence files are kept
next to it. The builds used here are GPL builds (they include x264 and the NVENC / QSV / AMF encoders).
"""
import logging
import os
import shutil
import sys
import tempfile
import zipfile

from ..config import app_dir, data_dir
from .jobs import Control
from .models import download

log = logging.getLogger(__name__)

SOURCES = [
    "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
]
WANT = ("ffmpeg.exe", "ffprobe.exe")


def target_dir() -> str:
    """ffmpeg next to the app for a source run (setup.bat); for the exe in %LOCALAPPDATA%\\FreeSub, so the
    app folder can stay read-only (e.g. in Program Files) and an update of the exe keeps ffmpeg."""
    if getattr(sys, "frozen", False):
        return os.path.join(data_dir(), "ffmpeg")
    return os.path.join(app_dir(), "ffmpeg")


def install(control=None) -> str:
    dest = target_dir()
    bin_dir = os.path.join(dest, "bin")
    if all(os.path.isfile(os.path.join(bin_dir, f)) for f in WANT):
        return bin_dir
    os.makedirs(bin_dir, exist_ok=True)
    control = control or Control()
    errors = []
    for url in SOURCES:
        tmp = os.path.join(tempfile.gettempdir(), "freesub_ffmpeg.zip")
        try:
            log.info("Downloading ffmpeg from %s", url)
            download(url, tmp, progress=lambda g, t: control.progress(
                g / t if t else None, f"ffmpeg {g / 1e6:.0f}/{t / 1e6:.0f} MB" if t else f"ffmpeg {g / 1e6:.0f} MB"),
                control=control)
            with zipfile.ZipFile(tmp) as z:
                for info in z.infolist():
                    base = os.path.basename(info.filename)
                    low = info.filename.lower()
                    if base in WANT:
                        target = os.path.join(bin_dir, base)
                    elif base.upper().startswith(("LICENSE", "README")) and "/doc/" not in low:
                        target = os.path.join(dest, base)
                    else:
                        continue
                    with z.open(info) as src, open(target, "wb") as out:
                        shutil.copyfileobj(src, out)
            if all(os.path.isfile(os.path.join(bin_dir, f)) for f in WANT):
                with open(os.path.join(dest, "SOURCE.txt"), "w", encoding="utf-8") as f:
                    f.write(f"Downloaded from {url}\nffmpeg is licensed under the GPL: https://ffmpeg.org/legal.html\n")
                return bin_dir
            errors.append(f"{url}: ffmpeg.exe not in the archive")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{url}: {e}")
            log.warning("ffmpeg download failed from %s: %s", url, e)
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
    raise RuntimeError("Could not download ffmpeg:\n" + "\n".join(errors))


def main():
    def prog(frac, text):
        print("\r  " + text.ljust(40), end="", flush=True)
    try:
        path = install(Control(on_progress=prog))
        print("\n  ffmpeg ready in", path)
        return 0
    except Exception as e:  # noqa: BLE001
        print("\n ", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
