"""NVIDIA GPU for faster-whisper (CTranslate2 + CUDA 12 / cuDNN 9 from pip, as in Japo)."""
import logging
import os
import shutil
import subprocess
import sys

log = logging.getLogger(__name__)
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_CACHE = {}


def add_cuda_dll_paths():
    """Let Windows find the pip-installed cuBLAS / cuDNN DLLs (source run and frozen build)."""
    if os.name != "nt" or _CACHE.get("dll"):
        return
    _CACHE["dll"] = True
    import site
    bases = []
    if getattr(sys, "_MEIPASS", None):
        bases.append(sys._MEIPASS)
    try:
        bases += list(site.getsitepackages())
        bases.append(site.getusersitepackages())
    except Exception:  # noqa: BLE001
        pass
    for base in bases:
        for sub in ("nvidia/cublas/bin", "nvidia/cudnn/bin", "nvidia/cuda_runtime/bin", "nvidia/cuda_nvrtc/bin"):
            p = os.path.join(base, sub)
            if os.path.isdir(p):
                try:
                    os.add_dll_directory(p)
                    os.environ["PATH"] = p + os.pathsep + os.environ.get("PATH", "")
                except Exception:  # noqa: BLE001
                    pass


def cuda_count() -> int:
    if "count" not in _CACHE:
        try:
            add_cuda_dll_paths()
            import ctranslate2
            _CACHE["count"] = int(ctranslate2.get_cuda_device_count())
        except Exception as e:  # noqa: BLE001
            log.info("CUDA not available: %s", e)
            _CACHE["count"] = 0
    return _CACHE["count"]


def nvidia_name() -> str:
    """'NVIDIA GeForce RTX 2050 (4096 MiB)' via nvidia-smi (installed with the driver), or ''."""
    if "name" not in _CACHE:
        _CACHE["name"] = ""
        exe = shutil.which("nvidia-smi")
        if exe:
            try:
                out = subprocess.run([exe, "--query-gpu=name,memory.total", "--format=csv,noheader"],
                                     capture_output=True, text=True, timeout=8, creationflags=_NO_WINDOW).stdout
                first = out.strip().splitlines()[0] if out.strip() else ""
                if first:
                    name, _, mem = first.partition(",")
                    _CACHE["name"] = f"{name.strip()} ({mem.strip()})" if mem else name.strip()
            except Exception:  # noqa: BLE001
                pass
    return _CACHE["name"]


def whisper_device(choice: str) -> tuple[str, str]:
    """(device, compute_type) for the setting: auto | cuda | cpu."""
    if choice == "cpu":
        return "cpu", "int8"
    if cuda_count() > 0:
        return "cuda", "int8_float16"     # fits 4 GB VRAM even with large-v3
    if choice == "cuda":
        log.warning("CUDA requested but no NVIDIA GPU / CUDA libraries found: using the CPU")
    return "cpu", "int8"
