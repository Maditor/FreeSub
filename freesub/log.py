"""Logging to logs\\freesub.log (+ console) and a timer for every processing step."""
import contextlib
import logging
import logging.handlers
import os
import sys
import time

from .config import logs_dir

log = logging.getLogger("freesub")


def setup():
    folder = logs_dir()
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fh = logging.handlers.RotatingFileHandler(os.path.join(folder, "freesub.log"), maxBytes=5_000_000,
                                              backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if sys.stderr is not None:          # windowed exe has no console
        ch = logging.StreamHandler()
        ch.setFormatter(fmt)
        root.addHandler(ch)
    for noisy in ("urllib3", "faster_whisper", "httpx", "huggingface_hub"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return os.path.join(folder, "freesub.log")


@contextlib.contextmanager
def step_timer(name: str, video: str = ""):
    """Log how long a step took: 'TIMING transcribe video.mp4: 83.2 s'."""
    t0 = time.perf_counter()
    log.info("START %s %s", name, os.path.basename(video))
    ok = False
    try:
        yield
        ok = True
    finally:
        dt = time.perf_counter() - t0
        log.info("TIMING %s %s: %.1f s%s", name, os.path.basename(video), dt, "" if ok else " (not finished)")
