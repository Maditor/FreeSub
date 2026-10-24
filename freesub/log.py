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
    if sys.stderr is not None and getattr(sys.stderr, "name", "") not in (os.devnull,) and \
            not str(getattr(sys.stderr, "name", "")).endswith("startup.txt"):      # a real console only
        ch = logging.StreamHandler()
        ch.setFormatter(fmt)
        root.addHandler(ch)
    for noisy in ("urllib3", "faster_whisper", "httpx", "huggingface_hub"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _catch_crashes()
    return os.path.join(folder, "freesub.log")


def _catch_crashes():
    """An error nobody caught (on the window's thread or a work thread) goes into the log: with no console
    window (run.bat / the exe) it would otherwise be lost."""
    import threading
    old = sys.excepthook

    def hook(kind, value, tb):
        if not issubclass(kind, KeyboardInterrupt):
            log.critical("Unexpected error", exc_info=(kind, value, tb))
        old(kind, value, tb)

    def thread_hook(a):
        if a.exc_type is not SystemExit:
            log.critical("Unexpected error in %s", getattr(a.thread, "name", "a thread"),
                         exc_info=(a.exc_type, a.exc_value, a.exc_traceback))
    sys.excepthook = hook
    threading.excepthook = thread_hook


def no_console_output():
    """Started without a console (pythonw / the exe): print() and libraries writing to the console (download
    bars) need somewhere to write. It goes to logs\\startup.txt, which also keeps an error that happens before
    the log is set up."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        f = open(os.path.join(logs_dir(), "startup.txt"), "w", encoding="utf-8", buffering=1)
    except OSError:
        f = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = f
    if sys.stderr is None:
        sys.stderr = f


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
