"""The timeline's film strip: small pictures along a video, one every `step` seconds, like video editors show.

Made in the background, in two passes, and shown while it is made (the timeline reads the folder again on
every refresh):
1. key frames only (`-skip_frame nokey`): seconds even for a long video. A video with key frames close together
   is done then; with few of them (often downloaded videos) these are the nearest key frames, a rough look.
2. otherwise the exact pictures, each put in place as soon as it is made, the whole length roughly first then
   the gaps filled. Which way is quicker is worked out from the key frames found:
   * seek: jump to each moment (decodes from the key frame before it): a long video with key frames,
   * read: the whole video once, start to end: a short one, or one with (almost) no key frames.
Files in <work folder>/strip: fNNNN.jpg (pass 1), rNNNN.jpg (pass 2, used before f); NNNN = 1 + index, the
picture of index i is at i * step. strip.json: {v, step, count, done}. A pass 2 cut short (FreeSub closed) goes on
from where it was next time.
"""
import atexit
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time

log = logging.getLogger(__name__)
HEIGHT = 56                     # px
VERSION = 3
MAX_PICTURES = 240
_NAME = re.compile(r"^([fr])(\d{4})\.jpg$")
_busy: set[str] = set()         # work folders being made now (the strip is made once at a time)
_lock = threading.Lock()
_procs: set = set()             # ffmpeg runs to stop when FreeSub closes
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


@atexit.register
def _stop_all():
    for p in list(_procs):
        try:
            p.kill()
        except Exception:  # noqa: BLE001
            pass


def step_for(duration: float) -> float:
    return max(2.0, duration / MAX_PICTURES)


def _meta_path(folder):
    return os.path.join(folder, "strip.json")


def _read_meta(folder) -> dict | None:
    try:
        with open(_meta_path(folder), encoding="utf-8") as f:
            meta = json.load(f)
        if meta.get("v") == VERSION and float(meta["step"]) > 0 and int(meta["count"]) > 0:
            return meta
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    return None


def _write_meta(folder, **meta):
    tmp = _meta_path(folder) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"v": VERSION, **meta}, f)
    os.replace(tmp, _meta_path(folder))


def state(folder) -> dict | None:
    """{step, count, done, pictures: {index: path}} of what is made so far (None: nothing yet)."""
    meta = _read_meta(folder)
    if not meta:
        return None
    pics, d = {}, os.path.join(folder, "strip")
    try:
        names = os.listdir(d)
    except OSError:
        names = []
    for n in sorted(names):                      # f before r: an exact picture replaces the rough one
        m = _NAME.match(n)
        if m:
            pics[int(m.group(2)) - 1] = os.path.join(d, n)
    count = int(meta["count"])
    return {"step": float(meta["step"]), "count": count, "done": bool(meta.get("done")),
            "pictures": {i: p for i, p in pics.items() if i < count}}


def busy(folder) -> bool:
    with _lock:
        return folder in _busy


def make(folder, video, duration, exe) -> dict | None:
    """Make (or finish) the strip of `video`; returns state() at the end. Run it in a thread."""
    with _lock:
        if folder in _busy:
            return state(folder)
        _busy.add(folder)
    try:
        return _make(folder, video, duration, exe)
    finally:
        with _lock:
            _busy.discard(folder)


def _make(folder, video, duration, exe):
    st = state(folder)
    if st and st["done"]:
        return st
    step = step_for(duration)
    count = int(max(0.0, duration - 0.1) / step) + 1          # none at the very end (no frame there)
    out = os.path.join(folder, "strip")
    t0 = time.time()
    if not st or abs(st["step"] - step) > 1e-6 or st["count"] != count:
        shutil.rmtree(out, ignore_errors=True)
        os.makedirs(out, exist_ok=True)
        _write_meta(folder, step=step, count=count, done=False)
        _key_frames(exe, video, step, out)
    files = [n for n in os.listdir(out) if n.startswith("f")]
    different = len({_md5(os.path.join(out, n)) for n in files})
    if len(files) >= count * 0.9 and different >= len(files) * 0.85:
        log.info("Timeline pictures: %d from the key frames in %.1f s", len(files), time.time() - t0)
        _write_meta(folder, step=step, count=count, done=True)
        return state(folder)
    # few key frames: the exact pictures. Seek to each one, or read the whole video once: what decodes less
    gap = duration / max(1, different)                     # seconds between key frames, about
    todo = [i for i in _coarse_first(count) if not os.path.isfile(os.path.join(out, f"r{i + 1:04d}.jpg"))]
    how = choose(duration, count, different)
    log.info("Timeline pictures: %d key frames for %d pictures (one every %.0f s): %s the %d left", different,
             count, gap, "jumping to" if how == "seek" else "reading the video for", len(todo))
    if todo:
        (_seek if how == "seek" else _read)(exe, video, step, out, todo)
    done = all(os.path.isfile(os.path.join(out, f"r{i + 1:04d}.jpg")) or
               os.path.isfile(os.path.join(out, f"f{i + 1:04d}.jpg")) for i in range(count))
    _write_meta(folder, step=step, count=count, done=done)
    log.info("Timeline pictures: done in %.1f s", time.time() - t0)
    return state(folder)


def choose(duration: float, count: int, key_frames: int) -> str:
    """'seek' (jump to each picture: decodes from the key frame before it, half the gap on average, plus
    starting ffmpeg) or 'read' (the whole video once): whichever decodes less."""
    gap = duration / max(1, key_frames)
    return "seek" if count * (gap / 2 + 1.5) < duration else "read"


def _md5(path):
    try:
        with open(path, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()
    except OSError:
        return path


def _coarse_first(count):
    """0..count-1, every 32nd first, then every 16th, ... so the whole length gets a look early."""
    order, seen, s = [], set(), 32
    while s >= 1:
        for i in range(0, count, s):
            if i not in seen:
                seen.add(i)
                order.append(i)
        s //= 2
    return order


def _run(cmd, timeout):
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=NO_WINDOW)
    _procs.add(p)
    try:
        _out, err = p.communicate(timeout=timeout)
        return p.returncode, err
    except subprocess.TimeoutExpired:
        p.kill()
        return -1, b""
    finally:
        _procs.discard(p)


def _key_frames(exe, video, step, out):
    _code, err = _run([exe, "-v", "error", "-y", "-skip_frame", "nokey", "-i", video, "-an", "-sn", "-dn",
                       "-vf", f"fps=1/{step:.3f},scale=-2:{HEIGHT}", "-q:v", "5",
                       os.path.join(out, "f%04d.jpg")], 900)
    if err and not os.listdir(out):
        log.info("Timeline pictures (key frames): ffmpeg said %s", err.decode("utf-8", "replace")[-300:])


def _seek(exe, video, step, out, todo, workers=2):
    """Each picture by jumping to its moment (two at a time), put in place as soon as it is made."""
    todo = list(todo)
    lock = threading.Lock()

    def worker():
        while True:
            with lock:
                if not todo:
                    return
                i = todo.pop(0)
            tmp = os.path.join(out, f"t{i + 1:04d}.jpg")
            _run([exe, "-v", "error", "-y", "-skip_loop_filter", "all", "-ss", f"{i * step:.3f}", "-i", video,
                  "-an", "-sn", "-dn", "-frames:v", "1", "-vf", f"scale=-2:{HEIGHT}", "-q:v", "5", tmp], 120)
            if os.path.isfile(tmp) and os.path.getsize(tmp):
                os.replace(tmp, os.path.join(out, f"r{i + 1:04d}.jpg"))
    threads = [threading.Thread(target=worker, daemon=True) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def _read(exe, video, step, out, todo):
    """The whole video once, start to end; each picture is put in place when the next one is begun (then it
    is written whole)."""
    want = set(todo)
    tmp_dir = os.path.join(out, "tmp")
    shutil.rmtree(tmp_dir, ignore_errors=True)
    os.makedirs(tmp_dir, exist_ok=True)
    cmd = [exe, "-v", "error", "-y", "-skip_frame", "bidir", "-skip_loop_filter", "all", "-i", video,
           "-an", "-sn", "-dn", "-vf", f"fps=1/{step:.3f},scale=-2:{HEIGHT}", "-q:v", "5",
           os.path.join(tmp_dir, "%04d.jpg")]
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
    _procs.add(p)

    def move(final=False):
        names = sorted(os.listdir(tmp_dir))
        for n in names if final else names[:-1]:             # the newest may still be being written
            try:
                i = int(n[:4]) - 1
            except ValueError:
                continue
            src = os.path.join(tmp_dir, n)
            if i in want:
                os.replace(src, os.path.join(out, f"r{i + 1:04d}.jpg"))
            else:
                os.remove(src)
    try:
        while p.poll() is None:
            time.sleep(0.5)
            move()
        move(final=True)
    finally:
        if p.poll() is None:
            p.kill()
        _procs.discard(p)
        shutil.rmtree(tmp_dir, ignore_errors=True)
