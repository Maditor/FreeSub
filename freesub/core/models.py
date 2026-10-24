"""Model downloads with progress into %LOCALAPPDATA%\\FreeSub\\models (download code from LazyK)."""
import hashlib
import logging
import os
import shutil
import time

import requests

from ..config import models_dir
from .jobs import Cancelled

log = logging.getLogger(__name__)

HF = "https://huggingface.co"
UA = {"User-Agent": "FreeSub"}

# name -> (Hugging Face repo, approx. size, note shown in the menu, only language or None)
WHISPER = {
    "large-v3-turbo": ("mobiuslabsgmbh/faster-whisper-large-v3-turbo", "1.6 GB", "fast, a little less accurate", None),
    "large-v3": ("Systran/faster-whisper-large-v3", "3.1 GB", "most accurate, slower", None),
    "distil-large-v3": ("Systran/faster-distil-whisper-large-v3", "1.5 GB", "English only, fast", "en"),
    "kotoba-whisper-v2.0": ("kotoba-tech/kotoba-whisper-v2.0-faster", "1.5 GB", "Japanese only, fast + accurate", "ja"),
    # BELLE-2 Chinese fine-tunes of Whisper (Apache-2.0), community CTranslate2 conversions
    "belle-large-v3-zh": ("k1nto/Belle-whisper-large-v3-zh-punct-ct2", "3.1 GB",
                          "Chinese only, most accurate, with punctuation", "zh"),
    "belle-large-v3-turbo-zh": ("flateon/Belle-whisper-large-v3-turbo-zh-ct2", "1.6 GB", "Chinese only, fast, a little less accurate", "zh"),
}
DEFAULT = "large-v3-turbo"
WHISPER_REQUIRED = ("config.json", "model.bin")
WHISPER_OPTIONAL = ("tokenizer.json", "vocabulary.txt", "vocabulary.json", "preprocessor_config.json")
# models converted without tokenizer.json use the standard multilingual Whisper tokenizer
FALLBACK_TOKENIZER = "openai/whisper-large-v2"


def whisper_lang(name: str):
    return WHISPER.get(name, (None, None, None, None))[3]


def whisper_dir(name: str) -> str:
    return os.path.join(models_dir(), "whisper", name)


def for_language(lang: str | None) -> list[str]:
    """The models offered in Steps for the spoken language: the ones for every language, and the ones made
    only for that language (none of those for Auto detect)."""
    return [n for n, v in WHISPER.items() if v[3] is None or (lang and v[3] == lang)]


def fit(name: str, lang: str | None) -> str:
    """name if it is offered for that language, else the default model."""
    return name if name in for_language(lang) else DEFAULT


def whisper_ready(name: str) -> bool:
    d = whisper_dir(name)
    return all(os.path.isfile(os.path.join(d, f)) for f in WHISPER_REQUIRED + ("tokenizer.json",))


def remove_whisper(name: str):
    shutil.rmtree(whisper_dir(name), ignore_errors=True)


def _repo_files(repo: str) -> list[tuple[str, int]]:
    """(file, size) of the repo's main branch; falls back to the known file names."""
    try:
        r = requests.get(f"{HF}/api/models/{repo}/tree/main", headers=UA, timeout=20)
        r.raise_for_status()
        out = [(x["path"], int(x.get("size") or 0)) for x in r.json() if x.get("type") == "file"]
        if out:
            return out
    except Exception as e:  # noqa: BLE001
        log.warning("Could not list %s (%s), using the known file names", repo, e)
    return [(f, 0) for f in WHISPER_REQUIRED + WHISPER_OPTIONAL]


def download_whisper(name: str, control=None):
    """Download a faster-whisper model (only the files it needs) with overall progress."""
    repo = WHISPER[name][0]
    dest = whisper_dir(name)
    os.makedirs(dest, exist_ok=True)
    wanted = set(WHISPER_REQUIRED + WHISPER_OPTIONAL)
    files = [(f, s) for f, s in _repo_files(repo) if f in wanted]
    total = sum(s for _, s in files) or 1
    done = 0
    for fname, size in files:
        target = os.path.join(dest, fname)
        if os.path.isfile(target) and (not size or os.path.getsize(target) == size):
            done += size
            continue
        url = f"{HF}/{repo}/resolve/main/{fname}"

        def prog(got, tot, base=done, fname=fname):
            if control is not None:
                frac = (base + got) / total if total > 1 else None
                control.progress(frac, f"Downloading {name}: {fname} · {(base + got) / 1e6:.0f}/{total / 1e6:.0f} MB")
        try:
            download(url, target, progress=prog, control=control)
        except requests.HTTPError as e:
            if fname in WHISPER_OPTIONAL and getattr(e.response, "status_code", 0) == 404:
                continue
            raise
        done += size or os.path.getsize(target)
    tok = os.path.join(dest, "tokenizer.json")
    if not os.path.isfile(tok):                  # converted without one (PhoWhisper): the standard one, BEFORE
        download(f"{HF}/{FALLBACK_TOKENIZER}/resolve/main/tokenizer.json", tok, control=control)    # the check
    if not whisper_ready(name):
        raise RuntimeError(f"Model {name} is incomplete after download")
    log.info("Whisper model %s ready in %s", name, dest)


def download(url, dest, sha=None, progress=None, control=None):
    """Stream a file to dest via dest.part; resumable is not needed, a broken file is never kept."""
    tmp = dest + ".part"
    h = hashlib.sha256()
    with requests.get(url, stream=True, timeout=(15, 60), headers=UA) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        got, last = 0, 0.0
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                if control is not None:
                    try:
                        control.check()
                    except Cancelled:
                        f.close()
                        os.remove(tmp)
                        raise
                f.write(chunk)
                if sha:
                    h.update(chunk)
                got += len(chunk)
                if progress and time.time() - last > 0.25:
                    progress(got, total)
                    last = time.time()
        if progress:
            progress(got, total)
    if total and got < total:
        os.remove(tmp)
        raise RuntimeError("download incomplete")
    if sha and h.hexdigest() != sha:
        os.remove(tmp)
        raise RuntimeError("checksum mismatch")
    os.replace(tmp, dest)
