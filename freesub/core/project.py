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
        os.makedirs(self.folder, exist_ok=True)

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
        data = _read_json(self.meta_path)
        if isinstance(data, dict):
            self.meta.update(data)
            self.meta["video"] = self.video

    def save(self):
        """Write project.json safely. The window reads it while the work thread writes it, and on Windows a
        file being read cannot be replaced (WinError 5 / 32), so reads and writes take turns (a lock per
        file), and a replace blocked by another program (antivirus, indexer) is tried again."""
        self.ensure()
        text = json.dumps(self.meta, ensure_ascii=False, indent=1)
        tmp = f"{self.meta_path}.{os.getpid()}.{threading.get_ident()}.tmp"
        with _file_lock(self.meta_path):
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


def delete_project(folder: str):
    """Forget a project: its work folder goes; the .srt files and exported videos are kept."""
    if os.path.dirname(os.path.abspath(folder)) == os.path.abspath(work_root()):
        shutil.rmtree(folder, ignore_errors=True)
