"""Steps of build.bat around PyInstaller.

    python tools/build_helper.py version X.Y.Z  write the version to VERSION (empty = keep it)
    python tools/build_helper.py prepare     versions of the add-ons + Windows version info for the exe
    python tools/build_helper.py finish      licences, README, LICENSE next to FreeSub.exe, then the .zip
"""
import json
import os
import shutil
import sys
import zipfile
from importlib import metadata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from freesub.config import APP_NAME, APP_URL, APP_VERSION  # noqa: E402

DIST = os.path.join(ROOT, "dist", "FreeSub")
ADDON_PACKAGES = ("piper-tts", "nvidia-cublas-cu12", "nvidia-cudnn-cu12")
NOT_SHIPPED = ADDON_PACKAGES + ("pip", "setuptools", "wheel", "pyinstaller", "pyinstaller-hooks-contrib",
                                "altgraph", "pefile", "pywin32-ctypes", "packaging", "nvidia-cuda-runtime-cu12",
                                "nvidia-cuda-nvrtc-cu12",
                                # left in a .venv by the Qwen3-ASR test, never in the exe (FreeSub.spec excludes them)
                                "torch", "transformers", "accelerate", "librosa", "numba", "llvmlite", "qwen-asr",
                                "nagisa", "soynlp", "sympy", "mpmath", "networkx", "safetensors", "soundfile",
                                "soxr", "audioread", "lazy-loader", "pooch", "scikit-learn", "scipy", "joblib",
                                "threadpoolctl", "msgpack", "dynet38", "cython")

NOTICE = f"""{APP_NAME} {APP_VERSION} - {APP_URL}

FreeSub itself is under the MIT licence (LICENSE).
This folder also contains libraries made by others, each under its own licence (the folders next to
this file hold the licence texts that came with each one). Among them:

- Qt for Python (PySide6) and Qt: LGPL-3.0. They are separate DLL / .pyd files in the _internal folder
  and can be replaced by another build of the same version. Source: https://code.qt.io
  Qt's media plugin uses FFmpeg libraries under the LGPL: https://ffmpeg.org
- edge-tts: LGPL-3.0 (Python files, replaceable). Source: https://github.com/rany2/edge-tts
- faster-whisper, CTranslate2, ONNX Runtime, NumPy, requests, PyAV, OpenCV, Pillow, RapidOCR, vig2p and the
  rest: MIT, BSD or Apache-2.0 (see their folders).

Not included, downloaded only when you ask for them inside FreeSub:
- ffmpeg (GPL): https://ffmpeg.org/legal.html
- Piper voices engine piper-tts with espeak-ng (GPL-3.0): https://github.com/OHF-Voice/piper1-gpl
- NVIDIA CUDA / cuDNN libraries (NVIDIA licence), for NVIDIA graphics cards
- Whisper, voice and OCR models: from their authors' pages, under their own licences
"""


def prepare():
    lock = {}
    for pkg in ADDON_PACKAGES:
        try:
            lock[pkg] = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            pass
    with open(os.path.join(ROOT, "freesub", "addons_lock.json"), "w", encoding="utf-8") as f:
        json.dump(lock, f, indent=1)
    print("  add-on versions:", lock or "(defaults)")
    nums = [int(x) for x in APP_VERSION.split(".")[:3]] + [0]
    while len(nums) < 4:
        nums.insert(-1, 0)
    t = tuple(nums[:4])
    os.makedirs(os.path.join(ROOT, "build"), exist_ok=True)
    with open(os.path.join(ROOT, "build", "version_info.txt"), "w", encoding="utf-8") as f:
        f.write(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={t}, prodvers={t}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1,
                    subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Maditor'),
      StringStruct('FileDescription', '{APP_NAME}'),
      StringStruct('FileVersion', '{APP_VERSION}'),
      StringStruct('InternalName', '{APP_NAME}'),
      StringStruct('LegalCopyright', 'MIT licence'),
      StringStruct('OriginalFilename', '{APP_NAME}.exe'),
      StringStruct('ProductName', '{APP_NAME}'),
      StringStruct('ProductVersion', '{APP_VERSION}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)
""")
    print("  version", APP_VERSION)


def _licences(dest):
    os.makedirs(dest, exist_ok=True)
    with open(os.path.join(dest, "THIRD-PARTY-NOTICES.txt"), "w", encoding="utf-8") as f:
        f.write(NOTICE)
    n = 0
    for dist in metadata.distributions():
        name = (dist.metadata.get("Name") or "").strip()
        if not name or name.lower() in NOT_SHIPPED or name.lower().startswith("nvidia-"):
            continue
        files = [p for p in (dist.files or []) if os.path.basename(str(p)).upper().startswith(
            ("LICENSE", "LICENCE", "COPYING", "NOTICE", "AUTHORS"))]
        if not files:
            continue
        folder = os.path.join(dest, name)
        os.makedirs(folder, exist_ok=True)
        for p in files:
            src = dist.locate_file(p)
            if os.path.isfile(src):
                shutil.copyfile(src, os.path.join(folder, os.path.basename(str(p))))
                n += 1
    print(f"  {n} licence files")


def finish():
    if not os.path.isfile(os.path.join(DIST, "FreeSub.exe")) and not os.path.isfile(os.path.join(DIST, "FreeSub")):
        sys.exit("dist\\FreeSub\\FreeSub.exe is missing: the PyInstaller step failed")
    for f in ("LICENSE", "README.md"):
        shutil.copyfile(os.path.join(ROOT, f), os.path.join(DIST, f))
    _licences(os.path.join(DIST, "licenses"))
    out = os.path.join(ROOT, "dist", f"FreeSub-{APP_VERSION}-win64.zip")
    if os.path.exists(out):
        os.remove(out)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for root, _dirs, files in os.walk(DIST):
            for f in files:
                full = os.path.join(root, f)
                z.write(full, os.path.join("FreeSub", os.path.relpath(full, DIST)))
    size = sum(os.path.getsize(os.path.join(r, f)) for r, _d, fs in os.walk(DIST) for f in fs)
    print(f"  folder: {size / 1e6:.0f} MB  ->  {out} ({os.path.getsize(out) / 1e6:.0f} MB)")


def version():
    """build.bat asks for the version; Enter keeps the one in VERSION."""
    import re
    new = (sys.argv[2] if len(sys.argv) > 2 else "").strip().lstrip("vV")
    if not new:
        print("  version", APP_VERSION)
        return
    if not re.fullmatch(r"\d+\.\d+\.\d+", new):
        sys.exit(f"  '{new}' is not a version like 1.0.0")
    with open(os.path.join(ROOT, "VERSION"), "w", encoding="utf-8") as f:
        f.write(new + "\n")
    print("  version", new)


if __name__ == "__main__":          # each step is its own run, so prepare / finish read the new VERSION
    {"version": version, "prepare": prepare, "finish": finish}[sys.argv[1]]()
