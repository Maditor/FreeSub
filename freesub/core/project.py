"""One project per video.

Work files (audio, separated voice, OCR frames, voice clips...) live in a hidden work folder:
    %LOCALAPPDATA%\\FreeSub\\projects\\<video name>-<id>\\
        project.json     step status, settings, timings (the checkpoint)
        audio.wav        16 kHz mono speech track
        vocals.wav       voice only (Best quality) / music.wav background without the voice
        subs.src.srt     subtitles in the original language (Whisper, OCR or imported)
        subs.tr.srt      translation (same lines and timing)
        dub.wav          dubbed voice track / mix.wav final audio

What the user keeps goes to the output folder (a "freesub_<first words of the name>" folder next to the
video, or a folder chosen in Settings):
        <video>.<lang>.srt         original subtitles
        <video>.<target>.srt       translated subtitles
        <video>.freesub.mp4        exported video
"""
import hashlib
import json
import os
import shutil
import threading
import time

from ..config import data_dir, settings

AUDIO = "audio.wav"
SRC_SRT = "subs.src.srt"
TR_SRT = "subs.tr.srt"
STEPS = ("audio", "separate", "transcribe", "ocr", "translate", "dub", "export")


def _safe(name: str) -> str:
    bad = '<>:"/\\|?*'
    return "".join("_" if c in bad or ord(c) < 32 else c for c in name).strip(" .") or "video"


def stem_of(video: str) -> str:
    return _safe(os.path.splitext(os.path.basename(video))[0])


def work_root() -> str:
    path = os.path.join(data_dir(), "projects")
    os.makedirs(path, exist_ok=True)
    return path


def folder_for(video: str) -> str:
    """Hidden work folder of this video (same file name in different folders never shares one)."""
    tag = hashlib.sha1(os.path.abspath(video).lower().encode("utf-8")).hexdigest()[:8]
    return os.path.join(work_root(), f"{stem_of(video)[:60]}-{tag}")


def short_name(video: str, words: int = 5) -> str:
    """First words of the video name: 'Frieren Beyond Journey's End Episode 01' -> 'Frieren Beyond Journey's End Episode…'."""
    parts = [w for w in stem_of(video).replace("_", " ").replace(".", " ").split() if any(c.isalnum() for c in w)]
    out = " ".join(parts[:words]) or "video"
    return (out + "…") if len(parts) > words else out


def output_dir_for(video: str) -> str:
    """Where the .srt files and the exported video go (Settings: a freesub_ folder next to the video, or one
    folder for everything)."""
    s = settings()
    root = (s.get("output_root") or "").strip()
    if s.get("output_location") == "custom" and root:
        return root
    return os.path.join(os.path.dirname(os.path.abspath(video)), f"freesub_{short_name(video)}")


def fingerprint(video: str) -> str:
    """Size + modification time: a different file put at the same path gets a fresh project."""
    try:
        st = os.stat(video)
        return f"{st.st_size}-{int(st.st_mtime)}"
    except OSError:
        return ""


# a finished step only counts while the file it made is still there
STEP_FILES = {"audio": AUDIO, "separate": "vocals.wav", "transcribe": SRC_SRT, "ocr": SRC_SRT, "import": SRC_SRT,
              "translate": TR_SRT, "dub": "mix.wav"}


class Project:
    def __init__(self, video: str, folder: str | None = None):
        self.video = os.path.abspath(video)
        self.folder = folder or folder_for(self.video)
        self.meta_path = os.path.join(self.folder, "project.json")
        self.meta = {"video": self.video, "steps": {}, "source_lang": "", "info": {}}
        self._load()
        self._check()

    def _check(self):
        """Make the saved state match the files: a new video at the same path starts from scratch, and a
        step whose file was deleted is no longer 'done' (so it runs again)."""
        fp = fingerprint(self.video)
        old = self.meta.get("fingerprint")
        different = bool(fp and old and fp != old)
        if fp and not old and (self.meta.get("info") or {}).get("duration"):
            # project from an older FreeSub (no fingerprint yet): compare the length of the video once
            try:
                from . import ffmpeg
                now = ffmpeg.probe(self.video).get("duration") or 0
                different = abs(now - self.meta["info"]["duration"]) > 0.5
            except Exception:  # noqa: BLE001
                pass
        if different:
            import logging
            logging.getLogger(__name__).info("A different video is at %s now: starting its project again", self.video)
            keep = {k: self.meta[k] for k in ("created", "ocr_region") if k in self.meta}
            for name in os.listdir(self.folder) if os.path.isdir(self.folder) else []:
                p = os.path.join(self.folder, name)
                if name != "project.json":
                    shutil.rmtree(p, ignore_errors=True) if os.path.isdir(p) else os.remove(p)
            self.meta = {"video": self.video, "steps": {}, "source_lang": "", "info": {}, **keep}
        if fp:
            self.meta["fingerprint"] = fp
        changed = fp != old
        for name, rec in list(self.meta.get("steps", {}).items()):
            if rec.get("status") != "done":
                continue
            need = STEP_FILES.get(name)
            gone = (need and not self.has(need)) or \
                   (name == "export" and not os.path.isfile(self.meta.get("outputs", {}).get("video", "")))
            if gone:
                rec["status"] = "missing"
                changed = True
        if changed and os.path.isfile(self.meta_path):
            self.save()

    # ------------------------------------------------------------- work files
    def path(self, name: str) -> str:
        return os.path.join(self.folder, name)

    def ensure(self):
        """The work folder, with its project.json first (a folder without one is taken as deleted)."""
        os.makedirs(self.folder, exist_ok=True)
        if not os.path.isfile(self.meta_path):
            self.save()

    def has(self, name: str) -> bool:
        p = self.path(name)
        return os.path.isfile(p) and os.path.getsize(p) > 0

    @property
    def audio(self) -> str:
        return self.path(AUDIO)

    @property
    def src_srt(self) -> str:
        return self.path(SRC_SRT)

    @property
    def tr_srt(self) -> str:
        return self.path(TR_SRT)

    def clear_work(self):
        """Delete the work folder (keeps nothing; the output folder is not touched)."""
        shutil.rmtree(self.folder, ignore_errors=True)

    # ------------------------------------------------------------- output files
    @property
    def out_dir(self) -> str:
        return output_dir_for(self.video)

    @property
    def stem(self) -> str:
        return stem_of(self.video)

    def out_srt(self, lang: str) -> str:
        return os.path.join(self.out_dir, f"{self.stem}.{lang or 'orig'}.srt")

    def out_video(self, ext: str = "mp4") -> str:
        return os.path.join(self.out_dir, f"{self.stem}.freesub.{ext}")

    def ensure_published(self, work_name: str, lang: str):
        """A finished step is skipped: put its .srt back in the output folder if it was deleted there."""
        if self.has(work_name) and not os.path.isfile(self.out_srt(lang)):
            self.publish(work_name, lang)

    def publish(self, work_name: str, lang: str) -> str:
        """Copy a finished subtitle file to the output folder; returns its path there."""
        dst = self.out_srt(lang)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(self.path(work_name), dst)
        self.meta.setdefault("outputs", {})[work_name] = dst
        self.save()
        return dst

    # ------------------------------------------------------------- meta
    def _load(self):
        import copy
        data = _read_json(self.meta_path)
        self._base = copy.deepcopy(data) if isinstance(data, dict) else {}     # as read: save() writes only changes
        if isinstance(data, dict):
            self.meta.update(data)
            self.meta["video"] = self.video
        elif not os.path.exists(self.meta_path):
            clear_leftovers(self.folder)          # no project.json: whatever is still there is from a deleted one

    def save(self):
        """Write project.json safely. The window reads it while the work thread writes it, and on Windows a
        file being read cannot be replaced (WinError 5 / 32), so reads and writes take turns (a lock per
        file), and a replace blocked by another program (antivirus, indexer) is tried again."""
        import copy
        os.makedirs(self.folder, exist_ok=True)
        tmp = f"{self.meta_path}.{os.getpid()}.{threading.get_ident()}.tmp"
        with _file_lock(self.meta_path):
            # only what THIS copy changed since it was read goes in: another copy (the work thread, the subtitle
            # table, the window) may have saved meanwhile (a box moved, a step finished) and keeps its change
            disk = _read_json(self.meta_path)
            merged = _merge(disk if isinstance(disk, dict) else {}, getattr(self, "_base", {}), self.meta)
            self.meta = merged
            self._base = copy.deepcopy(merged)
            text = json.dumps(merged, ensure_ascii=False, indent=1)
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
            for k in range(30):
                try:
                    os.replace(tmp, self.meta_path)
                    return
                except PermissionError:
                    time.sleep(0.1)
            try:                                  # still held by another program: write it in place
                with open(self.meta_path, "w", encoding="utf-8") as f:
                    f.write(text)
            finally:
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def step(self, name: str) -> dict:
        return self.meta["steps"].get(name, {})

    def is_done(self, name: str, output: str | None = None) -> bool:
        st = self.step(name).get("status") == "done"
        return st and (output is None or self.has(output))

    def mark(self, name: str, status: str, **info):
        """status: running | done | failed | stopped | skipped. Kept in project.json (the checkpoint)."""
        rec = self.meta["steps"].setdefault(name, {})
        rec.update(info)
        rec["status"] = status
        rec["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self.save()

    def touch(self):
        """Remember the project (and when it was last opened) so it shows on the Projects screen."""
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        self.meta.setdefault("created", now)
        self.meta["opened"] = now
        self.save()

    @property
    def thumb(self) -> str:
        return self.path("thumb.jpg")

    def make_thumb(self) -> str | None:
        """A small picture of the video for the Projects screen (made once)."""
        if self.has("thumb.jpg"):
            return self.thumb
        if not os.path.isfile(self.video):
            return None
        from . import ffmpeg
        exe = ffmpeg.find("ffmpeg")
        if not exe:
            return None
        import subprocess
        dur = (self.meta.get("info") or {}).get("duration") or 0
        if not dur:
            try:
                info = ffmpeg.probe(self.video)
                self.meta["info"] = info
                self.save()
                dur = info.get("duration") or 0
            except Exception:  # noqa: BLE001
                return None
        at = min(max(1.0, dur * 0.1), max(0.0, dur - 0.5))
        self.ensure()
        subprocess.run([exe, "-v", "error", "-y", "-ss", f"{at:.2f}", "-i", self.video, "-frames:v", "1",
                        "-vf", "scale=320:-2", "-q:v", "4", self.thumb], capture_output=True, timeout=60,
                       creationflags=0x08000000 if os.name == "nt" else 0)
        return self.thumb if self.has("thumb.jpg") else None

    def strip_state(self) -> dict | None:
        """The timeline's film strip made so far (see core/filmstrip): {step, count, done, pictures}."""
        from . import filmstrip
        return filmstrip.state(self.folder)

    def make_strip(self) -> dict | None:
        """Make (or finish) the timeline's film strip; run it in a thread. Returns strip_state()."""
        st = self.strip_state()
        if st and st["done"]:
            return st
        if not os.path.isfile(self.video):
            return None
        from . import ffmpeg, filmstrip
        exe = ffmpeg.find("ffmpeg")
        if not exe:
            return None
        dur = (self.meta.get("info") or {}).get("duration") or 0
        if not dur:
            try:
                dur = ffmpeg.probe(self.video).get("duration") or 0
            except Exception:  # noqa: BLE001
                return None
        if dur <= 0:
            return None
        self.ensure()
        return filmstrip.make(self.folder, self.video, dur, exe)

    # ------------------------------------------------------------- cut
    @property
    def cut(self):
        """{'mode': 'keep' | 'remove', 'start': s, 'end': e} or None."""
        c = self.meta.get("cut")
        if c and c.get("end", 0) > c.get("start", 0):
            return c
        return None

    def set_cut(self, mode, start, end):
        if mode and end is not None and start is not None and end > start:
            self.meta["cut"] = {"mode": mode, "start": round(float(start), 3), "end": round(float(end), 3)}
        else:
            self.meta.pop("cut", None)
        self.save()

    def has_subs(self) -> bool:
        return self.has(SRC_SRT)

    def summary(self) -> str:
        """Short status for the video list."""
        if self.is_done("export"):
            return "Exported"
        for name in STEPS:
            st = self.step(name).get("status")
            if st == "failed":
                return f"{name.capitalize()} failed"
            if st in ("running", "stopped"):
                return "Stopped - runs on from here"
        if self.has(TR_SRT):
            return "Translated"
        if self.has_subs():
            return "Subtitles ready"
        return ""


# ------------------------------------------------------------------ all projects (the Projects screen)
_locks: dict = {}
_locks_guard = threading.Lock()


def _merge(disk: dict, base: dict, mine: dict) -> dict:
    """disk + what changed from base to mine (keys added, changed or removed), dicts merged key by key."""
    out = dict(disk)
    for k, v in mine.items():
        if k in base and base[k] == v:
            continue                                          # not changed here: the file's value stays
        if isinstance(v, dict) and isinstance(base.get(k), dict) and isinstance(disk.get(k), dict):
            out[k] = _merge(disk[k], base[k], v)
        else:
            out[k] = v
    for k in base:
        if k not in mine:
            out.pop(k, None)                                  # removed here
    return out


def _file_lock(path):
    with _locks_guard:
        return _locks.setdefault(os.path.normcase(os.path.abspath(path)), threading.RLock())


def _read_json(path):
    """The JSON in path (None when missing or unreadable), taking turns with save() for the same file."""
    for k in range(10):
        try:
            with _file_lock(path):
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
        except PermissionError:                   # being replaced by another program right now
            time.sleep(0.05)
        except (OSError, ValueError):
            return None
    return None


def list_projects() -> list[dict]:
    """Every saved project, last opened first: {video, folder, name, status, opened, missing, thumb, duration}."""
    out = []
    root = work_root()
    for name in os.listdir(root):
        meta_path = os.path.join(root, name, "project.json")
        meta = _read_json(meta_path)
        if not isinstance(meta, dict):
            continue
        video = meta.get("video") or ""
        if not video:
            continue
        p = Project(video, folder=os.path.join(root, name))
        out.append({"video": video, "folder": p.folder, "name": os.path.basename(video),
                    "status": p.summary() or "New", "opened": meta.get("opened") or meta.get("created") or "",
                    "missing": not os.path.isfile(video), "thumb": p.thumb if p.has("thumb.jpg") else "",
                    "duration": (meta.get("info") or {}).get("duration") or 0})
    out.sort(key=lambda d: d["opened"], reverse=True)
    return out


# files that only depend on the video itself (not on the work done): they may stay
_SAFE_LEFT = {"thumb.jpg", "preview.mp4", "preview.done", "strip", "strip.json"}


def _rmtree(path: str, tries: int = 6) -> bool:
    """Delete a folder, trying again for a moment when Windows still holds a file (player, antivirus, indexer)
    and clearing read-only flags. True when it is gone."""
    import stat

    def unlock(func, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass
    for k in range(tries):
        if not os.path.exists(path):
            return True
        shutil.rmtree(path, onerror=unlock)
        if not os.path.exists(path):
            return True
        time.sleep(0.3 * (k + 1))
    return not os.path.exists(path)


def clear_leftovers(folder: str):
    """A work folder without project.json (its project was deleted while a file was still open): empty it, so
    opening the video again really starts from nothing."""
    if not os.path.isdir(folder):
        return
    for name in os.listdir(folder):
        if name in _SAFE_LEFT:
            continue
        p = os.path.join(folder, name)
        try:
            shutil.rmtree(p, ignore_errors=True) if os.path.isdir(p) else os.remove(p)
        except OSError:
            pass


def clean_work_root():
    """At start: work folders left without project.json (a delete that could not finish) are removed."""
    root = work_root()
    for name in os.listdir(root):
        folder = os.path.join(root, name)
        if os.path.isdir(folder) and not os.path.isfile(os.path.join(folder, "project.json")):
            _rmtree(folder, tries=1)


def delete_project(folder: str):
    """Forget a project: its work folder goes; the .srt files and exported videos are kept."""
    if os.path.dirname(os.path.abspath(folder)) == os.path.abspath(work_root()):
        _rmtree(folder)


# ------------------------------------------------------------------ the Trash: deleted projects, kept TRASH_DAYS
TRASH_DAYS = 20
TRASH_NOTE = "trashed.json"


def trash_root() -> str:
    path = os.path.join(data_dir(), "trash")
    os.makedirs(path, exist_ok=True)
    return path


def trash_project(folder: str):
    """Move a project to the Trash (its work folder, as it is). Gone for good after TRASH_DAYS."""
    folder = os.path.abspath(folder)
    if os.path.dirname(folder) != os.path.abspath(work_root()) or not os.path.isdir(folder):
        return
    name = os.path.basename(folder)
    dest = os.path.join(trash_root(), f"{name}~{int(time.time())}")
    for attempt in range(8):                     # a file still open for a moment (thumbnail, player)
        try:
            os.replace(folder, dest)
            break
        except OSError:
            if attempt == 7:                     # still held: copy it to the Trash, then empty the work folder
                shutil.copytree(folder, dest, dirs_exist_ok=True)
                try:                             # first of all: no project.json = no project any more
                    os.remove(os.path.join(folder, "project.json"))
                except OSError:
                    pass
                if not _rmtree(folder):
                    clear_leftovers(folder)
                    import logging
                    logging.getLogger(__name__).warning("Some work files of %s are still open: they go at the "
                                                        "next start", name)
            else:
                time.sleep(0.3)
    with open(os.path.join(dest, TRASH_NOTE), "w", encoding="utf-8") as f:
        json.dump({"deleted": time.time(), "name": name}, f)


def list_trash() -> list[dict]:
    """Projects in the Trash, last deleted first: {folder, video, name, deleted, days_left, thumb}."""
    out = []
    root = trash_root()
    for entry in os.listdir(root):
        folder = os.path.join(root, entry)
        note = _read_json(os.path.join(folder, TRASH_NOTE))
        meta = _read_json(os.path.join(folder, "project.json"))
        if not isinstance(note, dict):
            continue
        deleted = float(note.get("deleted") or 0)
        left = TRASH_DAYS - (time.time() - deleted) / 86400
        if note.get("kind") == "edit":                       # a project (several videos)
            from .edits import trash_info
            d = trash_info(folder, note)
        else:                                                 # one video's work (older FreeSub)
            video = (meta or {}).get("video") or ""
            thumb = os.path.join(folder, "thumb.jpg")
            d = {"folder": folder, "video": video, "name": os.path.basename(video) or entry,
                 "thumb": thumb if os.path.isfile(thumb) else ""}
        out.append(dict(d, deleted=deleted, days_left=max(0, int(left + 0.999))))
    out.sort(key=lambda d: d["deleted"], reverse=True)
    return out


def restore_project(folder: str) -> str:
    """Put a project back from the Trash. Raises FileExistsError when the video has a newer project."""
    note = _read_json(os.path.join(folder, TRASH_NOTE)) or {}
    from . import edits
    if note.get("kind") == "edit":
        return edits.restore_edit(folder)
    meta = _read_json(os.path.join(folder, "project.json")) or {}
    name = note.get("name") or os.path.basename(folder).rsplit("~", 1)[0]
    dest = os.path.join(work_root(), name)
    if os.path.exists(dest):
        raise FileExistsError(dest)
    os.replace(folder, dest)
    try:
        os.remove(os.path.join(dest, TRASH_NOTE))
    except OSError:
        pass
    if meta.get("video") and os.path.abspath(meta["video"]) not in edits.used_videos():
        edits.create([meta["video"]])                     # it shows on the Projects screen again
    return dest


def purge_trash(everything: bool = False) -> int:
    """Delete projects that have been in the Trash for TRASH_DAYS (or all of them). Returns how many."""
    n = 0
    root = trash_root()
    for entry in os.listdir(root):
        folder = os.path.join(root, entry)
        note = _read_json(os.path.join(folder, TRASH_NOTE))
        old = not isinstance(note, dict) or time.time() - float(note.get("deleted") or 0) > TRASH_DAYS * 86400
        if everything or old:
            n += _rmtree(folder)
    return n
