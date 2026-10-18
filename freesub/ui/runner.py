"""Runs steps on a worker thread and turns their callbacks into Qt signals (one job at a time)."""
import logging
import threading

from PySide6.QtCore import QObject, Signal

from ..core.jobs import Cancelled, Control, Stopwatch
from ..steps import pipeline

log = logging.getLogger(__name__)


class Runner(QObject):
    progress = Signal(float, str)               # fraction (-1 = unknown), text
    video_progress = Signal(str, float, str)    # video, fraction, text (two videos may run at once)
    status = Signal(str)
    step = Signal(str, str, str, str)           # video, step id, state, text
    video_started = Signal(str, int, int)       # video, index, count
    video_finished = Signal(str, str)           # video, done | failed | stopped
    started = Signal(str, list)                 # title, step ids
    finished = Signal(str, str)                 # done | failed | stopped, message
    paused_changed = Signal(bool)
    release = Signal(str)                       # video: let go of its sound files (blocking: see pipeline)

    def __init__(self):
        super().__init__()
        from ..steps import pipeline
        pipeline.before_write = self._before_write
        self.control = None
        self.thread = None
        self.watch = None

    def _before_write(self, video):
        import threading
        if threading.current_thread() is threading.main_thread():
            return                              # no window is waiting on this thread (command line)
        self.release.emit(video)                # BlockingQueuedConnection: back once the window is done

    @property
    def busy(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def _new_control(self):
        self._cur = {"v": ""}

        def prog(f, t):
            f = -1.0 if f is None else f
            self.progress.emit(f, t)
            self.video_progress.emit(self._cur["v"], f, t)
        self.control = Control(on_progress=prog, on_status=self.status.emit)
        self.watch = Stopwatch()
        return self.control

    def _lane_control(self, tag):
        """A second Control sharing Stop / Pause with the first, with its own progress (for its own video)."""
        cur = {"v": ""}

        def prog(f, t):
            f = -1.0 if f is None else f
            self.progress.emit(f, f"{tag}{t}")
            self.video_progress.emit(cur["v"], f, t)
        c = Control(on_progress=prog, on_status=self.status.emit)
        c._stop, c._run = self.control._stop, self.control._run       # noqa: SLF001
        return c, cur

    def run_videos(self, videos, steps=None, force=False, title="Working", upto=None):
        """Run `steps` (None = the saved recipe) on every video; one failing video does not stop the others."""
        if self.busy or not videos:
            return False
        control = self._new_control()
        shown = list(steps) if steps is not None else pipeline.recipe(None, upto)
        self.started.emit(title, shown)

        def work():
            failed, errors = 0, []
            for i, v in enumerate(videos):
                self._cur["v"] = v
                self.video_started.emit(v, i, len(videos))
                try:
                    pipeline.run_video(v, steps, control, force=force, upto=upto,
                                       on_step=lambda sid, st, txt, v=v: self.step.emit(v, sid, st, txt))
                    self.video_finished.emit(v, "done")
                except Cancelled:
                    self.video_finished.emit(v, "stopped")
                    self.finished.emit("stopped", "Stopped")
                    return
                except Exception as e:  # noqa: BLE001
                    failed += 1
                    errors.append(str(e))
                    self.video_finished.emit(v, "failed")
            if failed:
                msg = errors[0] if len(videos) == 1 else f"{failed} of {len(videos)} videos failed: {errors[0]}"
                self.finished.emit("failed", msg)
            else:
                self.finished.emit("done", f"Finished in {self._elapsed()}")

        def entry():
            # decided on this thread: finding the graphics card may load big libraries
            if steps is None and upto is None and len(videos) > 1:
                try:
                    two = pipeline.lanes(len(videos)) == 2
                except Exception:  # noqa: BLE001
                    two = False
                if two:
                    return self._two_lanes(videos, force)
            return work()

        self.thread = threading.Thread(target=entry, daemon=True)
        self.thread.start()
        return True

    def run_task(self, fn, title="Working"):
        """fn(control) on the worker thread (model download, import...)."""
        if self.busy:
            return False
        control = self._new_control()
        self.started.emit(title, [])

        def work():
            try:
                fn(control)
                self.finished.emit("done", f"{title}: done")
            except Cancelled:
                self.finished.emit("stopped", "Stopped")
            except Exception as e:  # noqa: BLE001
                log.exception("%s failed", title)
                self.finished.emit("failed", str(e))

        self.thread = threading.Thread(target=work, daemon=True)
        self.thread.start()
        return True

    def _two_lanes(self, videos, force):
        """Batch, two videos at once: lane A makes the subtitles / translation (graphics card) of the next
        video while lane B makes the narration and export of the one before. A is never more than one video
        ahead, so at most two are open."""
        import queue
        log.info("Batch: two videos at a time (subtitles of the next while the last is narrated / exported)")
        n = len(videos)
        ca, cur_a = self._lane_control("")
        cb, cur_b = self._lane_control("")
        ready = queue.Queue(maxsize=1)
        lock = threading.Lock()
        res = {"failed": 0, "errors": [], "stopped": False}

        def fail(v, e):
            with lock:
                res["failed"] += 1
                res["errors"].append(str(e))
            self.video_finished.emit(v, "failed")

        def lane_a():
            try:
                for i, v in enumerate(videos):
                    if ca.stopped:
                        break
                    cur_a["v"] = v
                    self.video_started.emit(v, i, n)
                    try:
                        pipeline.run_video(v, None, ca, force=force, part="front",
                                           on_step=lambda sid, st, txt, v=v: self.step.emit(v, sid, st, txt))
                    except Cancelled:
                        res["stopped"] = True
                        self.video_finished.emit(v, "stopped")
                        break
                    except Exception as e:  # noqa: BLE001
                        fail(v, e)
                        continue
                    while True:                        # wait for lane B (it may be busy), unless stopped
                        try:
                            ready.put(v, timeout=0.3)
                            break
                        except queue.Full:
                            if ca.stopped:
                                return
            finally:
                ready.put(None)

        def lane_b():
            while True:
                v = ready.get()
                if v is None:
                    return
                cur_b["v"] = v
                try:
                    pipeline.run_video(v, None, cb, force=force, part="back",
                                       on_step=lambda sid, st, txt, v=v: self.step.emit(v, sid, st, txt))
                    self.video_finished.emit(v, "done")
                except Cancelled:
                    res["stopped"] = True
                    self.video_finished.emit(v, "stopped")
                    while ready.get() is not None:     # let lane A finish
                        pass
                    return
                except Exception as e:  # noqa: BLE001
                    fail(v, e)

        ta = threading.Thread(target=lane_a, name="batch-subtitles", daemon=True)
        tb = threading.Thread(target=lane_b, name="batch-narration", daemon=True)
        ta.start()
        tb.start()
        ta.join()
        tb.join()
        if res["stopped"] or self.control.stopped:
            self.finished.emit("stopped", "Stopped")
        elif res["failed"]:
            msg = res["errors"][0] if n == 1 else f"{res['failed']} of {n} videos failed: {res['errors'][0]}"
            self.finished.emit("failed", msg)
        else:
            self.finished.emit("done", f"Finished in {self._elapsed()}")

    def _elapsed(self):
        from ..core.jobs import fmt_secs
        return fmt_secs(self.watch.elapsed) if self.watch else ""

    def pause(self):
        if self.control and self.busy:
            self.control.pause()
            self.paused_changed.emit(True)

    def resume(self):
        if self.control:
            self.control.resume()
            self.paused_changed.emit(False)

    def stop(self):
        if self.control:
            self.control.stop()
            self.paused_changed.emit(False)


class LogBridge(QObject):
    """logging -> Qt signal, for the log panel (safe from any thread)."""
    line = Signal(str, int)

    def __init__(self):
        super().__init__()
        bridge = self

        class _Handler(logging.Handler):
            def emit(self, record):
                try:
                    bridge.line.emit(self.format(record), record.levelno)
                except Exception:  # noqa: BLE001
                    pass

        self.handler = _Handler(logging.INFO)
        self.handler.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))
        logging.getLogger().addHandler(self.handler)

    def close(self):
        logging.getLogger().removeHandler(self.handler)
