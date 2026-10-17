"""Freeze detector: when the window stops responding for a few seconds, write where every thread is to the log.

A timer on the window's thread ticks every 250 ms; a small background thread checks the ticks. After 4 s
without one, the stack of every thread goes to freesub.log once (then 'responding again' when it is over), so
a hang can be found and fixed from the log alone.
"""
import logging
import sys
import threading
import time
import traceback

from PySide6.QtCore import QObject, QTimer

log = logging.getLogger("freesub.watchdog")
LIMIT = 4.0
FREEZE_DUMP = 8.0


class Watchdog(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.beat = time.monotonic()
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self.main_id = threading.get_ident()
        self._stop = False
        threading.Thread(target=self._watch, name="freeze-watch", daemon=True).start()
        # A hang inside Qt can hold Python's lock, so the thread above cannot run. faulthandler's own C thread
        # still can: it writes every thread's stack to logs/freeze.log when the window has not ticked for
        # FREEZE_DUMP seconds (re-armed at every tick, so nothing is written while all is well).
        self._fh = None
        try:
            import faulthandler
            import os
            from ..config import logs_dir
            folder = logs_dir()
            self._fh = open(os.path.join(folder, "freeze.log"), "a", encoding="utf-8")   # noqa: SIM115
            self._faulthandler = faulthandler
            self._arm()
        except Exception as e:  # noqa: BLE001
            log.info("Freeze log not available: %s", e)

    def _arm(self):
        if self._fh is None:
            return
        self._faulthandler.cancel_dump_traceback_later()
        self._faulthandler.dump_traceback_later(FREEZE_DUMP, repeat=False, file=self._fh)

    def _tick(self):
        now = time.monotonic()
        if self._fh is not None and now - getattr(self, "_armed_at", 0) > 1.0:
            self._armed_at = now
            self._arm()
        self.beat = now

    def _watch(self):
        frozen_since = None
        while not self._stop:
            time.sleep(1.0)
            idle = time.monotonic() - self.beat
            if idle > LIMIT and frozen_since is None:
                frozen_since = self.beat
                try:
                    self._dump(idle)
                except Exception:  # noqa: BLE001
                    pass
            elif idle <= LIMIT and frozen_since is not None:
                log.warning("Window responding again after %.1f s", time.monotonic() - frozen_since)
                frozen_since = None

    def _dump(self, idle):
        names = {t.ident: t.name for t in threading.enumerate()}
        parts = [f"Window not responding for {idle:.1f} s. Where each thread is:"]
        frames = sys._current_frames()          # noqa: SLF001
        order = sorted(frames, key=lambda k: k != self.main_id)        # the window's thread first
        for tid in order:
            label = "WINDOW THREAD" if tid == self.main_id else names.get(tid, str(tid))
            stack = "".join(traceback.format_stack(frames[tid])[-14:])
            parts.append(f"--- {label}\n{stack}")
        log.error("\n".join(parts))

    def stop(self):
        self._stop = True
        if self._fh is not None:
            self._faulthandler.cancel_dump_traceback_later()
