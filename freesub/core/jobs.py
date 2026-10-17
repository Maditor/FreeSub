"""Pause / stop control and progress reporting shared by every step (no UI code here)."""
import threading
import time


class Cancelled(Exception):
    """The user pressed Stop."""


class Control:
    """Passed into every step. Steps call check() often: it waits while paused, raises when stopped."""

    def __init__(self, on_progress=None, on_status=None):
        self._stop = threading.Event()
        self._run = threading.Event()
        self._run.set()
        self.on_progress = on_progress   # (fraction 0..1 or None, text)
        self.on_status = on_status       # (text)

    # --- buttons
    def stop(self):
        self._stop.set()
        self._run.set()

    def pause(self):
        self._run.clear()

    def resume(self):
        self._run.set()

    @property
    def paused(self) -> bool:
        return not self._run.is_set()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    @property
    def stop_event(self) -> threading.Event:
        return self._stop

    # --- used by steps
    def check(self):
        while not self._run.wait(0.2):
            if self._stop.is_set():
                break
        if self._stop.is_set():
            raise Cancelled()

    def progress(self, fraction, text=""):
        if self.on_progress:
            self.on_progress(None if fraction is None else max(0.0, min(1.0, float(fraction))), text)

    def status(self, text):
        if self.on_status:
            self.on_status(text)


class NullControl(Control):
    pass


def fmt_eta(elapsed: float, fraction: float) -> str:
    if fraction <= 0.01 or elapsed < 2:
        return ""
    left = elapsed * (1 - fraction) / fraction
    return f"~{fmt_secs(left)} left"


def fmt_secs(s: float) -> str:
    s = int(round(s))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {s % 3600 // 60:02d}m"


class Stopwatch:
    def __init__(self):
        self.t0 = time.perf_counter()

    @property
    def elapsed(self):
        return time.perf_counter() - self.t0
