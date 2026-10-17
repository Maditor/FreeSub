"""Graphics cards as DirectML numbers them (Windows DXGI adapters), from LazyK.

The list is read once in a short-lived child process (`main.py --list-gpus FILE`): a mistake in the native
calls can only end that child, never FreeSub. Used to run ONNX models (voice separation, OCR later) on the
dedicated card instead of the integrated one.
"""
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading

log = logging.getLogger(__name__)
_cache = {"list": None}
_lock = threading.Lock()


# ---------------------------------------------------------------- the child process
def enumerate_adapters():
    """DXGI adapters as [{"id", "name", "mb"}], software adapters left out (their numbers are kept).
    Run only in the child process."""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import POINTER, byref, c_int, c_long, c_size_t, c_ubyte, c_uint, c_uint16, c_uint32, c_ulong, c_void_p, c_wchar

    class GUID(ctypes.Structure):
        _fields_ = [("a", c_uint32), ("b", c_uint16), ("c", c_uint16), ("d", c_ubyte * 8)]

    class DESC1(ctypes.Structure):  # DXGI_ADAPTER_DESC1
        _fields_ = [("Description", c_wchar * 128), ("VendorId", c_uint), ("DeviceId", c_uint),
                    ("SubSysId", c_uint), ("Revision", c_uint), ("DedicatedVideoMemory", c_size_t),
                    ("DedicatedSystemMemory", c_size_t), ("SharedSystemMemory", c_size_t),
                    ("LuidLow", c_uint), ("LuidHigh", c_int), ("Flags", c_uint)]

    # IDXGIFactory1 {770aae78-f26f-4dba-a829-253c83d1b387}
    iid = GUID(0x770AAE78, 0xF26F, 0x4DBA, (c_ubyte * 8)(0xA8, 0x29, 0x25, 0x3C, 0x83, 0xD1, 0xB3, 0x87))

    def method(obj, index, restype, *argtypes):
        vtbl = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
        return ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)(vtbl[index])

    factory = c_void_p()
    if ctypes.windll.dxgi.CreateDXGIFactory1(byref(iid), byref(factory)) != 0 or not factory.value:
        return []
    out = []
    try:
        enum_adapters1 = method(factory, 12, c_long, c_uint, POINTER(c_void_p))   # IDXGIFactory1::EnumAdapters1
        for i in range(32):
            adapter = c_void_p()
            if enum_adapters1(factory, i, byref(adapter)) != 0 or not adapter.value:
                break
            try:
                desc = DESC1()
                if method(adapter, 10, c_long, POINTER(DESC1))(adapter, byref(desc)) == 0:   # GetDesc1
                    software = bool(desc.Flags & 2) or desc.VendorId == 0x1414   # WARP / Basic Render Driver
                    if not software:
                        out.append({"id": i, "name": desc.Description.strip(),
                                    "mb": int(desc.DedicatedVideoMemory // (1024 * 1024))})
            finally:
                method(adapter, 2, c_ulong)(adapter)   # Release
    finally:
        method(factory, 2, c_ulong)(factory)
    return out


def write_listing(path: str):
    """`main.py --list-gpus FILE`: write the cards as JSON. Never raises."""
    try:
        data = enumerate_adapters()
    except Exception:
        data = []
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError:
        pass


# ---------------------------------------------------------------- the parent side
def _child_cmd(out: str):
    if getattr(sys, "frozen", False):
        return [sys.executable, "--list-gpus", out]
    from ..config import app_dir
    return [sys.executable, os.path.join(app_dir(), "main.py"), "--list-gpus", out]


def adapters() -> list:
    """[{"id", "name", "mb"}] (cached; empty when not on Windows or unreadable)."""
    with _lock:
        if _cache["list"] is not None:
            return _cache["list"]
        data = []
        if sys.platform == "win32":
            fd, out = tempfile.mkstemp(prefix="freesub_gpus_", suffix=".json")
            os.close(fd)
            try:
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                subprocess.run(_child_cmd(out), timeout=20, creationflags=flags, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                with open(out, encoding="utf-8") as f:
                    data = [g for g in json.load(f) if isinstance(g, dict) and isinstance(g.get("id"), int)]
            except Exception as e:  # noqa: BLE001
                log.warning("Could not list the graphics cards: %s", e)
            finally:
                try:
                    os.remove(out)
                except OSError:
                    pass
        log.info("Graphics cards (DirectML): %s", [(g["id"], g["name"], g["mb"]) for g in data] or "none")
        _cache["list"] = data
        return data


def best_id() -> int | None:
    """DirectML device number of the card with the most video memory (the dedicated one), or None."""
    cards = adapters()
    return max(cards, key=lambda g: g.get("mb", 0))["id"] if cards else None
