"""Optional parts that the FreeSub.exe does not ship, downloaded when the user asks for them.

    piper   Piper offline voices (piper-tts, GPL-3.0, with espeak-ng). Kept out of the exe so FreeSub's own
            files stay MIT; downloaded from PyPI as the project publishes it.
    gpu     NVIDIA CUDA 12 / cuDNN 9 libraries for Whisper on an NVIDIA card (about 1.3 GB, NVIDIA licence).
            Most PCs have no NVIDIA card, so they never download it.

Each is the official wheel from pypi.org (checked against its SHA-256), unpacked into
%LOCALAPPDATA%\\FreeSub\\addons\\<name>; activate() puts those folders on the import / DLL path at start.
A source run (setup.bat) already has them installed in .venv, so nothing is downloaded there.
"""
import json
import logging
import os
import shutil
import sys
import tempfile
import zipfile

from ..config import data_dir, resource_path
from .models import UA, download

log = logging.getLogger(__name__)
PYPI = "https://pypi.org/pypi"

# versions the exe was built and tested with (build.bat writes addons_lock.json with the .venv versions)
DEFAULT_LOCK = {
    "piper-tts": "1.8.0",
    "nvidia-cublas-cu12": "12.9.2.10",
    "nvidia-cudnn-cu12": "9.27.0.42",
}
ADDONS = {
    "piper": {"packages": ["piper-tts"], "keep": ("piper/",), "title": "Piper"},
    "gpu": {"packages": ["nvidia-cublas-cu12", "nvidia-cudnn-cu12"], "keep": ("nvidia/",),
            "only": (".dll",), "title": "NVIDIA GPU support"},
}
SIZES = {"piper": "34 MB", "gpu": "1.3 GB"}


def addons_dir(name: str = "") -> str:
    path = os.path.join(data_dir(), "addons", name) if name else os.path.join(data_dir(), "addons")
    return path


def _lock() -> dict:
    lock = dict(DEFAULT_LOCK)
    try:
        with open(resource_path(os.path.join("freesub", "addons_lock.json")), encoding="utf-8") as f:
            lock.update({k: v for k, v in json.load(f).items() if isinstance(v, str)})
    except (OSError, ValueError):
        pass
    return lock


def installed(name: str) -> bool:
    return os.path.isfile(os.path.join(addons_dir(name), "READY"))


def activate():
    """Make downloaded add-ons usable (call once at start, before piper / ctranslate2 are imported)."""
    p = addons_dir("piper")
    if installed("piper") and p not in sys.path:
        sys.path.append(p)
    if installed("gpu"):
        from . import gpu
        gpu.EXTRA_BASES.append(addons_dir("gpu"))


def _wheel(package: str, version: str) -> tuple[str, str, int]:
    """(url, sha256, size) of the Windows 64-bit wheel of package==version on PyPI."""
    import requests
    r = requests.get(f"{PYPI}/{package}/{version}/json", timeout=(15, 30), headers=UA)
    r.raise_for_status()
    files = [u for u in r.json().get("urls", []) if u["filename"].endswith("win_amd64.whl")]
    if not files:
        raise RuntimeError(f"No Windows download of {package} {version}")
    f = files[0]
    return f["url"], f["digests"]["sha256"], int(f.get("size") or 0)


def install(name: str, control=None):
    """Download and unpack add-on name (piper | gpu). Safe to run again: it starts clean."""
    spec = ADDONS[name]
    lock = _lock()
    dest = addons_dir(name)
    new = dest + ".new"
    shutil.rmtree(new, ignore_errors=True)
    os.makedirs(new, exist_ok=True)
    wheels = [(pkg, lock[pkg], *_wheel(pkg, lock[pkg])) for pkg in spec["packages"]]
    total = sum(w[4] for w in wheels) or 1
    done = 0
    for pkg, ver, url, sha, size in wheels:
        tmp = os.path.join(tempfile.gettempdir(), f"freesub_{pkg}.whl")

        def prog(got, tot, base=done):
            if control is not None:
                control.progress((base + got) / total,
                                 f"Downloading {spec['title']}: {(base + got) / 1e6:.0f}/{total / 1e6:.0f} MB")
        try:
            log.info("Downloading %s %s", pkg, ver)
            download(url, tmp, sha=sha, progress=prog, control=control)
            if control is not None:
                control.progress(None, f"Unpacking {spec['title']}…")
            with zipfile.ZipFile(tmp) as z:
                for info in z.infolist():
                    n = info.filename
                    if info.is_dir() or not n.startswith(spec["keep"]):
                        continue
                    if spec.get("only") and not n.lower().endswith(spec["only"]):
                        continue
                    target = os.path.join(new, *n.split("/"))
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with z.open(info) as src, open(target, "wb") as out:
                        shutil.copyfileobj(src, out, 1 << 20)
                lic = [i for i in z.namelist() if ".dist-info/" in i and
                       os.path.basename(i).upper().startswith(("LICENSE", "COPYING"))]
                for i in lic:
                    folder = os.path.join(new, "licenses", pkg)
                    os.makedirs(folder, exist_ok=True)
                    with z.open(i) as src, open(os.path.join(folder, os.path.basename(i)), "wb") as out:
                        shutil.copyfileobj(src, out)
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
        done += size
    with open(os.path.join(new, "READY"), "w", encoding="utf-8") as f:
        f.write("\n".join(f"{w[0]}=={w[1]}" for w in wheels) + "\n")
    shutil.rmtree(dest, ignore_errors=True)
    os.replace(new, dest)
    activate()
    log.info("%s installed in %s", spec["title"], dest)


def can_download() -> bool:
    """Only the exe needs these downloads; a source run gets them from setup.bat."""
    return bool(getattr(sys, "frozen", False))
