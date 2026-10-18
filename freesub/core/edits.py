"""Projects: what the Projects screen shows. A project holds several videos (in the order of the Media list)
and the timeline made of them.

    %LOCALAPPDATA%\\FreeSub\\edits\\<id>\\edit.json    {id, name, created, opened, videos: [...], clips: [...]}

Each video keeps its own work folder (projects\\<video>-<tag>: subtitles, translation, narration, style; see
project.py); a project only says which videos it has, in what order, and its timeline. Two projects with the
same video share that video's work.
"""
import json
import os
import shutil
import threading
import time
import uuid

from ..config import data_dir
from .project import (TRASH_NOTE, _read_json, _rmtree, folder_for, trash_root, work_root)

EDIT = "edit.json"
_lock = threading.Lock()


def edits_root() -> str:
    path = os.path.join(data_dir(), "edits")
    os.makedirs(path, exist_ok=True)
    return path


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


class Edit:
    def __init__(self, folder: str):
        self.folder = folder
        data = _read_json(os.path.join(folder, EDIT))
        data = data if isinstance(data, dict) else {}
        self.id = data.get("id") or os.path.basename(folder)
        self.name = data.get("name") or "Project"
        self.created = data.get("created") or _now()
        self.opened = data.get("opened") or self.created
        self.videos = [os.path.abspath(v) for v in data.get("videos") or [] if isinstance(v, str) and v]
        self.clips = [c for c in data.get("clips") or [] if isinstance(c, dict) and c.get("video")]
        self.auto_name = bool(data.get("auto_name", False))      # named by FreeSub: the first video renames it

    def save(self):
        os.makedirs(self.folder, exist_ok=True)
        data = {"id": self.id, "name": self.name, "created": self.created, "opened": self.opened,
                "videos": self.videos, "clips": self.clips, "auto_name": self.auto_name}
        path = os.path.join(self.folder, EDIT)
        tmp = path + f".{threading.get_ident()}.tmp"
        with _lock:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            for k in range(20):
                try:
                    os.replace(tmp, path)
                    break
                except PermissionError:
                    if k == 19:
                        raise
                    time.sleep(0.05)

    def touch(self):
        self.opened = _now()
        self.save()

    def set_videos(self, videos):
        self.videos = [os.path.abspath(v) for v in videos]
        if self.auto_name and self.videos:            # "New project" takes the name of its first video
            self.name = _video_name(self.videos[0])
            self.auto_name = False
        self.save()

    def rename(self, name: str):
        name = " ".join(name.split())
        if name:
            self.name, self.auto_name = name, False
            self.save()


def _video_name(video: str) -> str:
    return os.path.splitext(os.path.basename(video))[0][:80] or "Project"


def create(videos=(), name: str = "") -> Edit:
    eid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    e = Edit(os.path.join(edits_root(), eid))
    e.id = eid
    e.created = e.opened = _now()
    e.videos = [os.path.abspath(v) for v in videos]
    if name:
        e.name = name
    elif e.videos:
        e.name = _video_name(e.videos[0])
    else:
        e.name, e.auto_name = time.strftime("Project %d-%m %H%M"), True
    e.save()
    return e


def load(eid: str) -> Edit | None:
    folder = os.path.join(edits_root(), eid)
    return Edit(folder) if os.path.isfile(os.path.join(folder, EDIT)) else None


def _duration(video: str) -> float:
    meta = _read_json(os.path.join(folder_for(video), "project.json")) or {}
    return float((meta.get("info") or {}).get("duration") or 0)


def _thumb(videos) -> tuple[str, str]:
    """(the picture, the video it is of): the first video that has one; else ('', first video there)."""
    there = [v for v in videos if os.path.isfile(v)]
    for v in there:
        t = os.path.join(folder_for(v), "thumb.jpg")
        if os.path.isfile(t) and os.path.getsize(t) > 0:
            return t, v
    return "", (there[0] if there else "")


def list_edits() -> list[dict]:
    """Every project, last opened first: {id, folder, name, videos, opened, thumb, thumb_of, duration, missing}."""
    migrate()
    out = []
    root = edits_root()
    for name in os.listdir(root):
        folder = os.path.join(root, name)
        if not os.path.isfile(os.path.join(folder, EDIT)):
            continue
        e = Edit(folder)
        thumb, of = _thumb(e.videos)
        out.append({"id": e.id, "folder": folder, "name": e.name, "videos": list(e.videos), "opened": e.opened,
                    "thumb": thumb, "thumb_of": of, "duration": sum(_duration(v) for v in e.videos),
                    "missing": bool(e.videos) and not any(os.path.isfile(v) for v in e.videos)})
    out.sort(key=lambda d: d["opened"], reverse=True)
    return out


def used_videos(skip: str = "") -> set:
    """Videos that a project (other than folder skip) has."""
    used = set()
    root = edits_root()
    for name in os.listdir(root):
        folder = os.path.join(root, name)
        if os.path.abspath(folder) == os.path.abspath(skip or "~"):
            continue
        data = _read_json(os.path.join(folder, EDIT))
        if isinstance(data, dict):
            used.update(os.path.abspath(v) for v in data.get("videos") or [])
    return used


# ------------------------------------------------------------------ from older FreeSub: one project per video
def migrate():
    """Once: every video project of an older FreeSub becomes a project with that one video."""
    mark = os.path.join(edits_root(), ".from_videos")
    if os.path.isfile(mark):
        return
    try:
        root = work_root()
        for name in os.listdir(root):
            meta = _read_json(os.path.join(root, name, "project.json"))
            if not isinstance(meta, dict) or not meta.get("video"):
                continue
            e = create([meta["video"]])
            e.created = meta.get("created") or e.created
            e.opened = meta.get("opened") or e.created
            e.save()
        with open(mark, "w") as f:
            f.write(_now())
    except OSError:
        pass


# ------------------------------------------------------------------ Trash
def trash_edit(folder: str) -> list[str]:
    """Move a project to the Trash. The work of its videos goes with it when no other project has them.
    Returns those videos (the window lets go of their files first)."""
    folder = os.path.abspath(folder)
    if os.path.dirname(folder) != os.path.abspath(edits_root()) or not os.path.isdir(folder):
        return []
    e = Edit(folder)
    others = used_videos(skip=folder)
    alone = [v for v in e.videos if v not in others]
    dest = os.path.join(trash_root(), f"{os.path.basename(folder)}~{int(time.time())}")
    shutil.copytree(folder, dest, dirs_exist_ok=True)
    _rmtree(folder)
    moved = []
    for v in alone:
        work = folder_for(v)
        if not os.path.isdir(work):
            continue
        target = os.path.join(dest, "videos", os.path.basename(work))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        for k in range(8):
            try:
                os.replace(work, target)
                break
            except OSError:
                if k == 7:                            # still held: copy, then empty the work folder
                    shutil.copytree(work, target, dirs_exist_ok=True)
                    try:
                        os.remove(os.path.join(work, "project.json"))
                    except OSError:
                        pass
                    _rmtree(work)
                else:
                    time.sleep(0.3)
        moved.append(v)
    with open(os.path.join(dest, TRASH_NOTE), "w", encoding="utf-8") as f:
        json.dump({"deleted": time.time(), "name": os.path.basename(folder), "kind": "edit", "title": e.name},
                  f, ensure_ascii=False)
    return moved


def trash_info(folder: str, note: dict) -> dict:
    e = Edit(folder)
    thumb = ""
    vdir = os.path.join(folder, "videos")
    for v in e.videos:                                # its picture: in the Trash with it, or still in use
        for base in (os.path.join(vdir, os.path.basename(folder_for(v))), folder_for(v)):
            t = os.path.join(base, "thumb.jpg")
            if os.path.isfile(t):
                thumb = t
                break
        if thumb:
            break
    return {"folder": folder, "video": "\n".join(e.videos), "name": note.get("title") or e.name,
            "thumb": thumb}


def restore_edit(folder: str) -> str:
    """Put a project back from the Trash (with its videos' work). Its videos' newer work is kept as it is."""
    note = _read_json(os.path.join(folder, TRASH_NOTE)) or {}
    name = note.get("name") or os.path.basename(folder).rsplit("~", 1)[0]
    dest = os.path.join(edits_root(), name)
    if os.path.exists(dest):
        dest = os.path.join(edits_root(), name + "-" + uuid.uuid4().hex[:4])
    vdir = os.path.join(folder, "videos")
    if os.path.isdir(vdir):
        for w in os.listdir(vdir):
            target = os.path.join(work_root(), w)
            if os.path.exists(target):                # the video was opened again meanwhile: keep the new work
                continue
            os.replace(os.path.join(vdir, w), target)
        shutil.rmtree(vdir, ignore_errors=True)
    os.replace(folder, dest)
    try:
        os.remove(os.path.join(dest, TRASH_NOTE))
    except OSError:
        pass
    e = Edit(dest)
    e.id = os.path.basename(dest)
    e.save()
    return dest


def edit_for_video(video: str) -> Edit:
    """A project restored from an older FreeSub's Trash (one video): it gets a project of its own."""
    return create([video])

