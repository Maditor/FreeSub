"""Kokoro (Vietnamese): natural Vietnamese voices (Ngọc Huyền, Mai Linh, Tuấn Ngọc...), run with onnxruntime.

THIRD-PARTY VOICES. FreeSub does not include or host them: the user downloads them on request from the
Hugging Face repo below (Apache-2.0 there; the repo does not say where its training voices came from).
Text -> phonemes with vig2p (Apache-2.0, a PyPI package installed by setup.bat).

Files (in models/kokoro): kokoro_vi.onnx, config.json (phoneme vocabulary), voicepacks/<voice>.pt.
A voicepack is a PyTorch tensor [510, 1, 256] (one style vector per phoneme count); it is read here without
PyTorch (the .pt zip holds a pickle and the raw float32 data).
"""
import io
import json
import logging
import os
import pickle
import re
import threading
import wave
import zipfile

import numpy as np

from ..config import models_dir
from ..core.models import download

log = logging.getLogger(__name__)
REPO = "contextboxai/Kokoro-Vietnamese"
HF = f"https://huggingface.co/{REPO}/resolve/main/"
PAGE = f"https://huggingface.co/{REPO}"
SIZE = "about 330 MB"
RATE = 24000
NOTE = ("Third-party voices from Hugging Face (contextboxai/Kokoro-Vietnamese). FreeSub does not include them; "
        "where their training voices came from is not stated, so check before any commercial use.")
# voice id -> (label, gender)
VOICES = {
    "ngoc_huyen": ("Ngọc Huyền", "f"), "mai_linh": ("Mai Linh", "f"), "my_yen": ("Mỹ Yến", "f"),
    "diem_trinh": ("Diễm Trinh", "f"), "thuc_trinh": ("Thục Trinh", "f"), "mai_loan": ("Mai Loan", "f"),
    "tuan_ngoc": ("Tuấn Ngọc", "m"), "manh_dung": ("Mạnh Dũng", "m"), "thanh_dat": ("Thành Đạt", "m"),
    "hung_thinh": ("Hưng Thịnh", "m"), "phat_tai": ("Phát Tài", "m"), "duc_an": ("Đức An", "m"),
    "duc_duy": ("Đức Duy", "m"), "storyvert": ("Storyvert (narrator)", "m"),
}
_LOCK = threading.Lock()
_STATE = {"session": None, "config": None, "packs": {}}


def folder() -> str:
    return os.path.join(models_dir(), "kokoro")


def g2p_installed() -> bool:
    try:
        import vig2p  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def ready() -> bool:
    d = folder()
    return os.path.isfile(os.path.join(d, "kokoro_vi.onnx")) and os.path.isfile(os.path.join(d, "config.json")) \
        and all(os.path.isfile(os.path.join(d, "voicepacks", v + ".pt")) for v in VOICES)


def download_all(control=None):
    """Model + config + every voicepack (each voicepack is about 0.5 MB)."""
    d = folder()
    os.makedirs(os.path.join(d, "voicepacks"), exist_ok=True)
    files = [("config.json", "config.json"), ("kokoro_vi.onnx", "kokoro_vi.onnx")] + \
            [(f"voicepacks/{v}.pt", f"voicepacks/{v}.pt") for v in VOICES]
    for i, (remote, local) in enumerate(files):
        dest = os.path.join(d, local)
        if os.path.isfile(dest):
            continue

        def prog(got, total, name=os.path.basename(local), i=i):
            if control is not None:
                frac = got / total if total else None
                control.progress(frac, f"Kokoro voices · {name} · {got / 1e6:.0f} MB ({i + 1}/{len(files)})")
        download(HF + remote, dest, progress=prog, control=control)


# ------------------------------------------------------------------ reading a .pt tensor without PyTorch
_DTYPES = {"FloatStorage": np.float32, "HalfStorage": np.float16, "DoubleStorage": np.float64,
           "BFloat16Storage": "bf16"}


class _Storage:
    def __init__(self, name):
        self.name = name


def load_pt(path) -> np.ndarray:
    """The (first) tensor in a torch.save() file, as a numpy array."""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        pkl = next(n for n in names if n.endswith("data.pkl"))
        root = pkl[:-len("data.pkl")]

        def storage(key, kind):
            raw = z.read(f"{root}data/{key}")
            dt = _DTYPES.get(kind.name, np.float32)
            if dt == "bf16":
                u = np.frombuffer(raw, dtype=np.uint16).astype(np.uint32) << 16
                return u.view(np.float32)
            return np.frombuffer(raw, dtype=dt)

        def rebuild(st, offset, size, stride, *_rest):
            arr = st[offset:]
            if not size:
                return arr[:1].reshape(())
            return np.lib.stride_tricks.as_strided(arr, shape=tuple(size),
                                                   strides=tuple(s * arr.itemsize for s in stride)).copy()

        class U(pickle.Unpickler):
            def find_class(self, module, name):
                if module == "torch._utils" and name.startswith("_rebuild_tensor"):
                    return rebuild
                if module == "torch" and name.endswith("Storage"):
                    return _Storage(name)
                if module == "collections" and name == "OrderedDict":
                    import collections
                    return collections.OrderedDict
                if module == "torch" and name == "device":
                    return lambda *a: None
                raise pickle.UnpicklingError(f"not allowed in a voicepack: {module}.{name}")

            def persistent_load(self, pid):
                # ('storage', storage type, key, location, size)
                return storage(pid[2], pid[1])

        obj = U(io.BytesIO(z.read(pkl))).load()
    if isinstance(obj, dict):
        obj = next(iter(obj.values()))
    return np.asarray(obj, dtype=np.float32)


# ------------------------------------------------------------------ synthesis
def _session():
    if _STATE["session"] is None:
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.log_severity_level = 3
        path = os.path.join(folder(), "kokoro_vi.onnx")
        _STATE["session"] = ort.InferenceSession(path, so, providers=["CPUExecutionProvider"])
        with open(os.path.join(folder(), "config.json"), encoding="utf-8") as f:
            _STATE["config"] = json.load(f)
        log.info("Kokoro voices loaded")
    return _STATE["session"], _STATE["config"]


def _pack(voice):
    if voice not in _STATE["packs"]:
        p = load_pt(os.path.join(folder(), "voicepacks", voice + ".pt"))
        _STATE["packs"][voice] = p.reshape(p.shape[0], -1)          # [510, 256]
    return _STATE["packs"][voice]


def split_sentences(text: str, limit: int = 300) -> list[str]:
    """Pieces short enough for the model (it takes up to ~510 phonemes at a time)."""
    parts = [p.strip() for p in re.split(r"(?<=[.!?…;:])\s+", text.strip()) if p.strip()]
    out = []
    for p in parts:
        while len(p) > limit:
            cut = p.rfind(",", 0, limit)
            cut = cut if cut > limit // 3 else p.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            out.append(p[:cut + 1].strip())
            p = p[cut + 1:].strip()
        if p:
            out.append(p)
    return out


def synth(text: str, voice: str, path: str, speed: int = 100) -> str:
    from vig2p import phonemize_text
    with _LOCK:
        sess, cfg = _session()
        vocab = cfg["vocab"]
        ctx = int(cfg.get("plbert", {}).get("max_position_embeddings", 512))
        pack = _pack(voice if voice in VOICES else "ngoc_huyen")
        chunks = []
        for piece in split_sentences(text):
            ps = phonemize_text(piece)
            ids = [vocab[c] for c in ps if c in vocab][:ctx - 2]
            if not ids:
                continue
            style = pack[min(len(ids), len(pack)) - 1][None, :].astype(np.float32)
            wav = sess.run(None, {"input_ids": np.asarray([[0, *ids, 0]], dtype=np.int64), "ref_s": style,
                                  "speed": np.asarray(max(0.5, min(2.0, speed / 100)), dtype=np.float32)})[0]
            chunks.append(np.asarray(wav, dtype=np.float32).reshape(-1))
            chunks.append(np.zeros(int(0.12 * RATE), dtype=np.float32))     # short pause between sentences
    audio = np.concatenate(chunks) if chunks else np.zeros(RATE // 10, dtype=np.float32)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
    return path
