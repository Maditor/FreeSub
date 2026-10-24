# PyInstaller recipe for FreeSub.exe (one folder). Run build.bat, not this file directly.
#
# Left out on purpose (downloaded by the app when the user asks, see freesub/core/addons.py):
#   piper-tts + espeak-ng (GPL-3.0), NVIDIA CUDA / cuDNN (1.3 GB, NVIDIA licence), ffmpeg (GPL).
import os
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = os.path.abspath(SPECPATH)

datas = [
    (os.path.join(ROOT, "assets"), "assets"),
    (os.path.join(ROOT, "VERSION"), "."),
    (os.path.join(ROOT, "freesub", "lang"), os.path.join("freesub", "lang")),
    (os.path.join(ROOT, "freesub", "tts", "loanwords_vi.txt"), os.path.join("freesub", "tts")),
]
lock = os.path.join(ROOT, "freesub", "addons_lock.json")
if os.path.isfile(lock):
    datas.append((lock, "freesub"))
for pkg in ("faster_whisper", "rapidocr", "vig2p", "sea_g2p", "jaconv"):    # sea_g2p: its dictionary (.bin)
    try:
        datas += collect_data_files(pkg, excludes=["**/pytorch/**", "**/paddle/**", "**/__pycache__/**"])
    except Exception:
        pass

binaries = []
for pkg in ("ctranslate2", "onnxruntime"):
    binaries += collect_dynamic_libs(pkg)

# RapidOCR picks its engine by name at run time: only the ONNX Runtime one is used
hidden = collect_submodules("rapidocr", filter=lambda m: not any(
    x in m for x in ("pytorch", "paddle", "openvino", "tensorrt", "mnn")))
hidden += collect_submodules("vig2p") + collect_submodules("edge_tts") + collect_submodules("sea_g2p")

EXCLUDES = [
    "piper", "nvidia",                                   # add-ons, see above
    "torch", "torchvision", "torchaudio", "tensorflow", "paddle", "openvino", "tensorrt",
    "transformers", "accelerate", "librosa", "numba", "llvmlite", "qwen_asr", "gradio",     # never used by FreeSub
    "scipy", "matplotlib", "pandas", "IPython", "jupyter", "notebook", "tkinter", "pytest",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtWebChannel",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQml",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs", "PySide6.QtPdf",
    "PySide6.QtPdfWidgets", "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning",
    "PySide6.QtLocation", "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtSql", "PySide6.QtTest",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtRemoteObjects", "PySide6.QtScxml",
    "PySide6.QtTextToSpeech", "PySide6.QtWebSockets", "PySide6.QtHttpServer", "PySide6.QtSpatialAudio",
]

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    excludes=EXCLUDES,
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FreeSub",
    icon=os.path.join(ROOT, "assets", "icon.ico"),
    console=False,
    upx=False,                       # packed exes are flagged by some antivirus programs
    version=os.path.join(ROOT, "build", "version_info.txt") if os.path.isfile(
        os.path.join(ROOT, "build", "version_info.txt")) else None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="FreeSub")
