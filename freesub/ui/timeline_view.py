"""The timeline (under the video when the timeline button is on): clips of the listed videos one after another.

    ruler       click or drag: go there
    subtitles   each video's lines where they fall; click one to give it a look of its own (Style), drag it to
                move it, drag its ends to change when it starts / ends, double-click to edit its text
    video       the clips; drag one to change the order, drag its ends to cut off the start / end
    narration   where the narration speaks (when it was made)

Videos are dragged here from the list on the left. S (or Ctrl+B) cuts the clip in two at the line,
Delete removes the selected clip, Ctrl+Z / Ctrl+Y undo / redo the clip changes, Ctrl + wheel zooms.
"""
import os

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QListWidget, QScrollBar, QSlider, QVBoxLayout, QWidget

from ..core.project import TR_SRT, Project
from ..core.subtitle import fmt_clock, load_srt
from ..steps.timeline import Timeline
from . import theme
from .widgets import IconButton, label

RULER = 26
SUB_Y, SUB_H = RULER + 10, 30
VID_Y, VID_H = SUB_Y + SUB_H + 8, 60
NAR_Y, NAR_H = VID_Y + VID_H + 8, 26
HEADS = 78                   # the column of track names and locks on the left
PAD = 14                     # room before 0:00
EDGE = 6                     # px: grab an end to trim / stretch
STEPS = (0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200)


class _Video:
    """What the timeline shows of one video (read again when its files change)."""

    def __init__(self, path, duration):
        self.path = path
        self.duration = duration or 0.0
        self.stamp = None
        self.cues = []                  # [(start, end, text, index)]
        self.starts = []
        self.text_of = {}
        self.spans = []
        self.thumb = None
        self.looks = {}
        self._making = False

    def refresh(self):
        p = Project(self.path)
        self._picture(p)
        srt = p.tr_srt if p.has(TR_SRT) else p.src_srt
        stamp = tuple(os.path.getmtime(f) if os.path.isfile(f) else 0 for f in (p.src_srt, p.tr_srt,
                                                                                p.path("dub.json"),
                                                                                p.path("project.json")))
        if stamp == self.stamp:
            return
        self.stamp = stamp
        cues = load_srt(srt) if os.path.isfile(srt) else []
        self.cues = [(c.start, c.end, " ".join(c.text.split()), i) for i, c in enumerate(cues)]
        self.cues.sort(key=lambda q: q[0])
        self.starts = [q[0] for q in self.cues]                 # for finding the lines on screen quickly
        self.text_of = {q[3]: q[2] for q in self.cues}
        try:
            from ..steps import dub
            self.spans = dub.placed_spans(p) if p.has("dub.json") else []
        except Exception:  # noqa: BLE001
            self.spans = []
        from ..core.style import line_looks
        self.looks = line_looks(p)

    def _picture(self, p):
        """The video's picture: read it once it is there; when it is not, have it made (once, in the background).
        Checked on every refresh, so a picture made later (or made again) shows up."""
        if self.thumb is not None:
            return
        if p.has("thumb.jpg"):
            pm = QPixmap(p.thumb)
            if not pm.isNull():
                self.thumb = pm.scaledToHeight(VID_H - 4, Qt.SmoothTransformation)
                return
        if not self._making and os.path.isfile(self.path):
            self._making = True
            import threading

            def make():
                try:
                    Project(self.path).make_thumb()
                except Exception:  # noqa: BLE001
                    pass
            threading.Thread(target=make, daemon=True).start()


class _Canvas(QWidget):
    def __init__(self, view):
        super().__init__(view)
        self.v = view
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.ClickFocus)
        self.setMinimumHeight(NAR_Y + NAR_H + 10)
        self.drag = None                 # what the mouse is doing (see mousePressEvent)
        self.drop_x = None               # where dragged videos would go

    # ------------------------------------------------------------ geometry
    def x_of(self, t):
        return PAD + t * self.v.px - self.v.scroll.value()

    def t_of(self, x):
        return max(0.0, (x - PAD + self.v.scroll.value()) / self.v.px)

    def clip_rect(self, i, tl=None):
        tl = tl or self.v.tl
        a = tl.start_of(i)
        return QRectF(self.x_of(a), VID_Y, Timeline.length(tl.clips[i]) * self.v.px, VID_H)

    def cue_rects(self, visible_only=True):
        """[(rect, clip index, video, cue index, start, end)] of the lines that are inside their clip (and, by
        default, on screen: a long video has thousands)."""
        import bisect
        out = []
        tl = self.v.tl
        t0, t1 = self.t_of(-20), self.t_of(self.width() + 20)
        for i, c in enumerate(tl.clips):
            info = self.v.video(c["video"])
            base = tl.start_of(i)
            lo, hi = c["in"], c["out"]
            if visible_only:                       # the part of this clip that is on screen, in its video's time
                lo = max(lo, c["in"] + (t0 - base))
                hi = min(hi, c["in"] + (t1 - base))
                if hi <= lo:
                    continue
            starts = info.starts
            j = max(0, bisect.bisect_left(starts, lo) - 3)      # a few before: lines that began earlier
            for (a, b, _text, k) in info.cues[j:]:
                if a >= hi:
                    break
                if b <= c["in"] or a >= c["out"] or b <= lo:
                    continue
                a2, b2 = max(a, c["in"]), min(b, c["out"])
                x0, x1 = self.x_of(base + a2 - c["in"]), self.x_of(base + b2 - c["in"])
                out.append((QRectF(x0, SUB_Y, max(3.0, x1 - x0), SUB_H), i, c["video"], k, a, b))
        return out

    def insert_index(self, x):
        tl = self.v.tl
        t = self.t_of(x)
        for i in range(len(tl.clips)):
            mid = tl.start_of(i) + Timeline.length(tl.clips[i]) / 2
            if t < mid:
                return i
        return len(tl.clips)

    # ------------------------------------------------------------ painting
    # Everything but the playhead is kept as a picture: while the video plays only the playhead moves (many times
    # a second), and drawing the whole timeline each time took the time the video needed (it stuttered).
    def update(self, *args):
        self._static = None                          # something changed: drawn again
        super().update(*args)

    def move_playhead(self, old_x, new_x):
        """Only the strip around the old and the new playhead is painted again."""
        lo, hi = min(old_x, new_x), max(old_x, new_x)
        super().update(int(lo) - 8, 0, int(hi - lo) + 17, self.height())

    def paintEvent(self, e):
        c = theme.colors()
        ratio = self.devicePixelRatioF()
        st = getattr(self, "_static", None)
        if st is None or st.size() != self.size() * ratio:
            st = QPixmap(self.size() * ratio)
            st.setDevicePixelRatio(ratio)
            q = QPainter(st)
            q.setRenderHint(QPainter.Antialiasing)
            self._paint_static(q, c)
            q.end()
            self._static = st
        p = QPainter(self)
        p.drawPixmap(e.rect(), st, QRectF(e.rect().x() * ratio, e.rect().y() * ratio,
                                          e.rect().width() * ratio, e.rect().height() * ratio).toRect())
        p.setRenderHint(QPainter.Antialiasing)
        self._paint_playhead(p, c)
        p.end()

    def _paint_static(self, p, c):
        p.fillRect(self.rect(), QColor(c["panel"] if "panel" in c else c["bg"]))
        self._ruler(p, c)
        for y, h in ((SUB_Y, SUB_H), (VID_Y, VID_H), (NAR_Y, NAR_H)):          # track lanes
            lane = QColor(c["border"])
            lane.setAlpha(60)
            p.fillRect(QRectF(0, y, self.width(), h), lane)
        tl = self.drag.get("preview") if self.drag and self.drag.get("preview") else self.v.tl
        self._clips(p, c, tl)
        self._cues(p, c)
        if self.drop_x is not None:
            pen = QPen(QColor(c["accent"]), 2)
            p.setPen(pen)
            p.drawLine(QPointF(self.drop_x, SUB_Y - 4), QPointF(self.drop_x, NAR_Y + NAR_H))
        if not self.v.tl.clips:
            p.setPen(QColor(c["muted"]))
            p.drawText(QRectF(0, VID_Y, self.width(), VID_H), Qt.AlignCenter,
                       self.v.empty_text)

    def _paint_playhead(self, p, c):
        x = self.x_of(self.v.playhead)                                       # the playhead
        p.setPen(QPen(QColor(c["accent"]), 1.5))
        p.drawLine(QPointF(x, 4), QPointF(x, self.height()))
        tri = QPainterPath()
        tri.moveTo(x - 6, 2)
        tri.lineTo(x + 6, 2)
        tri.lineTo(x, 10)
        tri.closeSubpath()
        p.fillPath(tri, QColor(c["accent"]))

    def _ruler(self, p, c):
        step = next((s for s in STEPS if s * self.v.px >= 70), STEPS[-1])
        t0 = self.t_of(0)
        t = (int(t0 / step)) * step
        f = QFont(self.font())
        f.setPointSizeF(max(7.0, f.pointSizeF() - 1))
        p.setFont(f)
        while self.x_of(t) < self.width():
            x = self.x_of(t)
            p.setPen(QColor(c["faint"]))
            p.drawLine(QPointF(x, RULER - 8), QPointF(x, RULER))
            p.setPen(QColor(c["muted"]))
            p.drawText(QPointF(x + 3, RULER - 10), fmt_clock(t).split(".")[0] if step >= 1 else fmt_clock(t))
            for k in range(1, 5):
                xm = self.x_of(t + step * k / 5)
                p.setPen(QColor(c["border"]))
                p.drawLine(QPointF(xm, RULER - 4), QPointF(xm, RULER))
            t += step

    def _clips(self, p, c, tl):
        f = QFont(self.font())
        f.setPointSizeF(max(7.0, f.pointSizeF() - 1))
        p.setFont(f)
        for i, clip in enumerate(tl.clips):
            r = self.clip_rect(i, tl)
            if r.right() < 0 or r.left() > self.width():
                continue
            info = self.v.video(clip["video"])
            path = QPainterPath()
            path.addRoundedRect(r.adjusted(1, 1, -1, -1), 6, 6)
            p.save()
            p.setClipPath(path)
            p.fillRect(r, QColor(c["video"]))
            if info.thumb is not None:                       # the picture, repeated along the clip
                w = info.thumb.width()
                x = r.left() + 2
                while x < r.right():
                    p.drawPixmap(QPointF(x, r.top() + 2), info.thumb)
                    x += w + 2
            name = f"{os.path.basename(clip['video'])}  {fmt_clock(Timeline.length(clip))}"
            fm = QFontMetricsF(f)
            tag = QRectF(r.left() + 6, r.top() + 5, min(r.width() - 12, fm.horizontalAdvance(name) + 10),
                         fm.height() + 4)
            if tag.width() > 20:
                p.fillRect(tag, QColor(0, 0, 0, 150))
                p.setPen(QColor("#ffffff"))
                p.drawText(tag.adjusted(5, 0, -3, 0), Qt.AlignVCenter | Qt.AlignLeft,
                           fm.elidedText(name, Qt.ElideRight, tag.width() - 8))
            p.restore()
            sel = self.v.sel == ("clip", clip["id"])
            p.setPen(QPen(QColor(c["accent"] if sel else c["stroke_strong"]), 2 if sel else 1))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 6, 6)
            if sel:                                           # the ends that can be dragged
                for x in (r.left() + 1, r.right() - 5):
                    p.fillRect(QRectF(x, r.top() + 14, 4, r.height() - 28), QColor(c["accent"]))
            span_col = QColor(c["accent"])                   # narration under the clip
            span_col.setAlpha(150)
            base = tl.start_of(i)
            for a, b in info.spans:
                if b <= clip["in"] or a >= clip["out"]:
                    continue
                x0 = self.x_of(base + max(a, clip["in"]) - clip["in"])
                x1 = self.x_of(base + min(b, clip["out"]) - clip["in"])
                p.fillRect(QRectF(x0, NAR_Y + 3, max(2.0, x1 - x0), NAR_H - 6), span_col)

    def _cues(self, p, c):
        f = QFont(self.font())
        f.setPointSizeF(max(7.0, f.pointSizeF() - 1))
        p.setFont(f)
        fm = QFontMetricsF(f)
        live = self.drag if self.drag and self.drag.get("kind", "").startswith("cue") else None
        for (r, _i, video, k, _a, _b) in self.cue_rects():
            if live and live["video"] == video and live["cue"] == k and live.get("rect") is not None:
                r = live["rect"]
            if r.right() < 0 or r.left() > self.width():
                continue
            sel = self.v.sel == ("cue", video, k)
            own = str(k) in self.v.video(video).looks
            fill = QColor(c["accent"])
            fill.setAlpha(225 if sel else 70)
            p.setPen(QPen(QColor(c["accent"] if sel else c["stroke_strong"]), 1.5 if sel else 1))
            p.setBrush(fill)
            p.drawRoundedRect(r.adjusted(0.5, 1, -0.5, -1), 4, 4)
            if own:                                           # a look of its own: a small dot
                p.setBrush(QColor(c["accent_fg"] if sel else c["accent"]))
                p.setPen(Qt.NoPen)
                p.drawEllipse(QPointF(r.right() - 6, r.top() + 7), 2.5, 2.5)
            if r.width() > 18:
                text = self.v.video(video).text_of.get(k, "")
                p.setPen(QColor(c["accent_fg"] if sel else c["fg"]))
                p.drawText(r.adjusted(5, 0, -8, 0), Qt.AlignVCenter | Qt.AlignLeft,
                           fm.elidedText(text, Qt.ElideRight, r.width() - 12))

    # ------------------------------------------------------------ mouse
    def _hit_cue(self, pos):
        for (r, i, video, k, a, b) in self.cue_rects():
            if r.adjusted(-2, 0, 2, 0).contains(pos):
                return r, i, video, k, a, b
        return None

    def _hit_clip(self, pos):
        for i in range(len(self.v.tl.clips)):
            r = self.clip_rect(i)
            if r.contains(pos):
                return i, r
        return None

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        pos = e.position()
        self.setFocus()
        if pos.y() < RULER + 4:
            self.drag = {"kind": "scrub"}
            self.v.seek_to(self.t_of(pos.x()))
            return
        if SUB_Y <= pos.y() <= SUB_Y + SUB_H:
            hit = self._hit_cue(pos)
            if hit:
                r, i, video, k, a, b = hit
                kind = "cue_l" if pos.x() - r.left() <= EDGE else "cue_r" if r.right() - pos.x() <= EDGE \
                    else "cue_move"
                if self.v.locks["subs"]:
                    kind = "none"                         # locked: chosen, never moved
                self.drag = {"kind": kind, "x0": pos.x(), "rect0": QRectF(r), "rect": None, "video": video,
                             "cue": k, "a": a, "b": b, "clip": i, "moved": False}
                self.v.select(("cue", video, k))
                return
        if VID_Y <= pos.y() <= VID_Y + VID_H:
            hit = self._hit_clip(pos)
            if hit:
                i, r = hit
                clip = self.v.tl.clips[i]
                kind = "trim_l" if pos.x() - r.left() <= EDGE else "trim_r" if r.right() - pos.x() <= EDGE \
                    else "clip_move"
                if self.v.locks["video"]:
                    kind = "none"
                self.drag = {"kind": kind, "x0": pos.x(), "index": i, "clip": dict(clip), "moved": False,
                             "preview": None}
                self.v.select(("clip", clip["id"]))
                return
        self.v.select(None)
        self.drag = {"kind": "scrub"}
        self.v.seek_to(self.t_of(pos.x()))

    def mouseMoveEvent(self, e):
        pos = e.position()
        d = self.drag
        if d is None:
            self._hover(pos)
            return
        kind = d["kind"]
        if kind == "scrub":
            self.v.seek_to(self.t_of(pos.x()))
            return
        if kind == "none":
            return
        dx = pos.x() - d["x0"]
        if not d["moved"] and abs(dx) < 3:
            return
        d["moved"] = True
        dt = dx / self.v.px
        if kind.startswith("trim") or kind == "clip_move":
            tl = self.v.tl.copy()
            i = d["index"]
            if kind == "trim_l":
                tl.trim(i, d["clip"]["in"] + dt, None, self.v.video(d["clip"]["video"]).duration)
            elif kind == "trim_r":
                tl.trim(i, None, d["clip"]["out"] + dt, self.v.video(d["clip"]["video"]).duration)
            else:
                self.drop_x = self.x_of(self.v.tl.start_of(self.insert_index(pos.x())))
                tl = None
            d["preview"] = tl
        else:                                                    # a subtitle line
            r = QRectF(d["rect0"])
            if kind == "cue_move":
                r.translate(dx, 0)
            elif kind == "cue_l":
                r.setLeft(min(r.left() + dx, r.right() - 4))
            else:
                r.setRight(max(r.right() + dx, r.left() + 4))
            d["rect"] = r
            d["dt"] = dt
        self.update()

    def mouseReleaseEvent(self, e):
        d, self.drag = self.drag, None
        drop_x, self.drop_x = self.drop_x, None
        if not d or not d.get("moved"):
            self.update()
            return
        kind = d["kind"]
        if kind in ("trim_l", "trim_r") and d.get("preview") is not None:
            self.v.apply(d["preview"])
        elif kind == "clip_move" and drop_x is not None:
            to = self.insert_index(e.position().x())
            i = d["index"]
            if to > i:
                to -= 1
            if to != i:
                tl = self.v.tl.copy()
                tl.move(i, to)
                self.v.apply(tl)
        elif kind.startswith("cue") and "dt" in d:
            dt = d["dt"]
            a, b = d["a"], d["b"]
            if kind == "cue_move":
                a, b = a + dt, b + dt
            elif kind == "cue_l":
                a = min(a + dt, b - 0.2)
            else:
                b = max(b + dt, a + 0.2)
            self.v.retime(d["video"], d["cue"], a, b, kind == "cue_move")
        self.update()

    def mouseDoubleClickEvent(self, e):
        hit = self._hit_cue(e.position()) if SUB_Y <= e.position().y() <= SUB_Y + SUB_H else None
        if hit:
            self.v.edit_text.emit(hit[2], hit[3])

    def _hover(self, pos):
        cursor = Qt.ArrowCursor
        if SUB_Y <= pos.y() <= SUB_Y + SUB_H and not self.v.locks["subs"]:
            hit = self._hit_cue(pos)
            if hit:
                r = hit[0]
                cursor = Qt.SizeHorCursor if (pos.x() - r.left() <= EDGE or r.right() - pos.x() <= EDGE) \
                    else Qt.OpenHandCursor
        elif VID_Y <= pos.y() <= VID_Y + VID_H and not self.v.locks["video"]:
            hit = self._hit_clip(pos)
            if hit:
                r = hit[1]
                cursor = Qt.SizeHorCursor if (pos.x() - r.left() <= EDGE or r.right() - pos.x() <= EDGE) \
                    else Qt.OpenHandCursor
        self.setCursor(cursor)

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            self.v.zoom(1.2 if e.angleDelta().y() > 0 else 1 / 1.2, e.position().x())
        else:
            step = -e.angleDelta().y() or -e.angleDelta().x()
            self.v.scroll.setValue(self.v.scroll.value() + int(step))
        e.accept()

    # ------------------------------------------------------------ videos dragged from the list
    def _paths(self, e):
        src = e.source()
        if isinstance(src, QListWidget):
            return [i.data(Qt.UserRole) for i in src.selectedItems() if i.data(Qt.UserRole)]
        md = e.mimeData()
        if md.hasUrls():
            return [u.toLocalFile() for u in md.urls() if u.isLocalFile()]
        return []

    def dragEnterEvent(self, e):
        if self._paths(e):
            e.setDropAction(Qt.CopyAction)          # copied: the video stays in the Media list
            e.accept()

    def dragMoveEvent(self, e):
        if self._paths(e):
            self.drop_x = self.x_of(self.v.tl.start_of(self.insert_index(e.position().x())))
            self.update()
            e.setDropAction(Qt.CopyAction)
            e.accept()

    def dragLeaveEvent(self, _e):
        self.drop_x = None
        self.update()

    def dropEvent(self, e):
        paths = self._paths(e)
        self.drop_x = None
        if paths:
            self.v.add_requested.emit(paths, self.insert_index(e.position().x()))
            e.setDropAction(Qt.CopyAction)
            e.accept()
        self.update()


class _Heads(QWidget):
    """Left of the tracks: what each one is, and a lock so it cannot be moved by mistake."""

    def __init__(self, view):
        super().__init__(view)
        self.v = view
        self.setFixedWidth(HEADS)
        self.rows = []
        for key, icon, tip, y, h in (("subs", "cc", "Subtitles", SUB_Y, SUB_H),
                                     ("video", "video", "Video", VID_Y, VID_H),
                                     ("audio", "volume", "Narration", NAR_Y, NAR_H)):
            name = IconButton(icon, tip, size=16, parent=self)
            name.setCursor(Qt.ArrowCursor)
            name.setAttribute(Qt.WA_TransparentForMouseEvents)     # a sign, not a button
            lock = IconButton("unlock", "Lock this track (nothing on it can be moved)", size=14, checkable=True,
                              parent=self)
            lock.toggled.connect(lambda on, k=key, b=lock: self._lock(k, b, on))
            self.rows.append((name, lock, y, h))

    def _lock(self, key, b, on):
        self.v.locks[key] = on
        b.key = "lock" if on else "unlock"
        b.refresh_icon()
        b.setToolTip("Unlock this track" if on else "Lock this track (nothing on it can be moved)")
        self.v.canvas.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        for name, lock, y, h in self.rows:
            name.move(10, int(y + (h - name.height()) / 2))
            lock.move(10 + name.width() + 4, int(y + (h - lock.height()) / 2))

    def paintEvent(self, _e):
        c = theme.colors()
        p = QPainter(self)
        p.setPen(QColor(c["border"]))
        p.drawLine(self.width() - 1, RULER, self.width() - 1, self.height())
        p.end()


class TimelineView(QWidget):
    seek = Signal(float)                    # timeline time
    changed = Signal()                      # the clips changed (saved)
    add_requested = Signal(list, int)       # videos dropped here, at clip index
    selected = Signal(object)               # ("clip", id) | ("cue", video, index) | None
    retimed = Signal(str, int, float, float)    # video, line index, start, end (its video's time)
    edit_text = Signal(str, int)            # video, line index

    def __init__(self, duration_of):
        super().__init__()
        self.duration_of = duration_of      # video -> seconds
        self.tl = Timeline()                # the open project's (set_timeline)
        self.store = None                   # tl -> None: keeps the clips (in the project)
        self.px = 40.0                      # pixels per second
        self.playhead = 0.0
        self.sel = None
        self._videos = {}
        self._undo, self._redo = [], []
        self.empty_text = "Drag videos here from the list on the left"

        self.locks = {"subs": False, "video": False, "audio": False}
        self.b_undo = IconButton("undo", "Undo (Ctrl+Z)", size=18)
        self.b_redo = IconButton("redo_arrow", "Redo (Ctrl+Shift+Z)", size=18)
        self.b_split = IconButton("split", "Cut the clip in two at the line (Ctrl+B)", size=18)
        self.b_left = IconButton("trim_left", "Delete the part of the clip left of the line (Q)", size=18)
        self.b_right = IconButton("trim_right", "Delete the part of the clip right of the line (W)", size=18)
        self.b_delete = IconButton("delete", "Remove the selected clip (Delete)", size=18)
        self.total = label("", muted=True)
        self.b_zout = IconButton("zoom_out", "Zoom out (Ctrl+- or Ctrl + wheel)", size=18)
        self.b_zin = IconButton("zoom_in", "Zoom in (Ctrl+= or Ctrl + wheel)", size=18)
        self.b_fit = IconButton("fit", "Show the whole timeline (Shift+Z)", size=18)
        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setRange(0, 100)
        self.zoom_slider.setFixedWidth(110)
        self.zoom_slider.setProperty("wheel_ok", True)
        self.b_split.clicked.connect(self.split)
        self.b_left.clicked.connect(lambda: self.delete_side("left"))
        self.b_right.clicked.connect(lambda: self.delete_side("right"))
        self.b_delete.clicked.connect(self.delete_selected)
        self.b_undo.clicked.connect(self.undo)
        self.b_redo.clicked.connect(self.redo)
        self.b_zout.clicked.connect(lambda: self.zoom(1 / 1.4))
        self.b_zin.clicked.connect(lambda: self.zoom(1.4))
        self.b_fit.clicked.connect(self.fit)
        self.zoom_slider.valueChanged.connect(self._slider_zoom)
        bar_box = QWidget()                          # one row, everything on the same middle line
        bar_box.setFixedHeight(34)
        bar = QHBoxLayout(bar_box)
        bar.setContentsMargins(HEADS - 8, 0, 0, 0)
        bar.setSpacing(4)
        for w in (self.b_undo, self.b_redo, None, self.b_split, self.b_left, self.b_right, self.b_delete):
            if w is None:
                bar.addSpacing(10)
            else:
                bar.addWidget(w, 0, Qt.AlignVCenter)
        bar.addSpacing(14)
        bar.addWidget(self.total, 0, Qt.AlignVCenter)
        bar.addStretch(1)
        for w in (self.b_zout, self.zoom_slider, self.b_zin, self.b_fit):
            bar.addWidget(w, 0, Qt.AlignVCenter)

        self.canvas = _Canvas(self)
        self.heads = _Heads(self)
        self.scroll = QScrollBar(Qt.Horizontal)
        self.scroll.valueChanged.connect(self._scrolled)
        self._auto = False                  # the scroll is being moved by the playhead (not by the user)
        self._user_until = 0.0              # the user scrolled: the playhead does not pull the view back until
        self.scroll.sliderReleased.connect(self._released)
        mid = QHBoxLayout()
        mid.setContentsMargins(0, 0, 0, 0)
        mid.setSpacing(0)
        mid.addWidget(self.heads)
        mid.addWidget(self.canvas, 1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addWidget(bar_box)
        lay.addLayout(mid, 1)
        lay.addWidget(self.scroll)
        self._sync_slider()
        self._changed(save=False)
        self._poll = QTimer(self)                     # subtitles / narration made meanwhile show up
        self._poll.setInterval(1500)
        self._poll.timeout.connect(self._refresh_videos)

    # ------------------------------------------------------------ data
    def video(self, path) -> _Video:
        v = self._videos.get(path)
        if v is None:
            v = self._videos[path] = _Video(path, self.duration_of(path))
            v.refresh()
        return v

    def _refresh_videos(self):
        if not self.isVisible():
            return
        for v in list(self._videos.values()):
            v.refresh()
        self.canvas.update()

    def showEvent(self, e):
        super().showEvent(e)
        self._poll.start()
        self._refresh_videos()

    def hideEvent(self, e):
        super().hideEvent(e)
        self._poll.stop()

    def reload_video(self, path):
        if path in self._videos:
            self._videos[path].stamp = None
            self._videos[path].refresh()
            self.canvas.update()

    def apply(self, tl: Timeline, undoable=True):
        if undoable:
            self._undo.append(self.tl.copy())
            del self._undo[:-50]
            self._redo.clear()
        self.tl = tl
        self._changed()

    def set_timeline(self, tl: Timeline, store):
        """Another project is open: its timeline, kept by store(tl) on every change; undo starts over."""
        self.store = store
        self.tl = tl
        self.sel = None
        self.playhead = 0.0
        self._undo, self._redo = [], []
        self._changed(save=False)
        self.fit()

    def _changed(self, save=True):
        if save and self.store is not None:
            self.store(self.tl)
        total = self.tl.total()
        n = len(self.tl.clips)
        self.total.setText(f"{n} clip{'s' if n != 1 else ''} · {fmt_clock(total)}" if n else "")
        self.playhead = min(self.playhead, total)
        self._update_scroll()
        for b in (self.b_split, self.b_delete, self.b_left, self.b_right):
            b.setEnabled(n > 0)
        self.b_undo.setEnabled(bool(self._undo))
        self.b_redo.setEnabled(bool(self._redo))
        if save:
            self.changed.emit()
        self.canvas.update()

    def add(self, paths, index):
        tl = self.tl.copy()
        k = index
        for p in paths:
            if tl.insert(k, p, self.duration_of(p)) is not None:
                k += 1
        if k != index:
            self.apply(tl)

    # ------------------------------------------------------------ edits
    def select(self, sel):
        self.sel = sel
        self.selected.emit(sel)
        self.canvas.update()

    def selected_clip(self) -> int:
        if self.sel and self.sel[0] == "clip":
            return next((i for i, c in enumerate(self.tl.clips) if c["id"] == self.sel[1]), -1)
        return -1

    def delete_selected(self):
        i = self.selected_clip()
        if i < 0 or self.locks["video"]:
            return
        from .widgets import confirm
        if not confirm(self, "Remove this clip from the timeline?\n(Ctrl+Z brings it back.)", "tl_delete",
                       "Remove", danger=True):
            return
        tl = self.tl.copy()
        tl.remove(i)
        self.select(None)
        self.apply(tl)

    def split(self):
        if self.locks["video"]:
            return
        tl = self.tl.copy()
        if tl.split(self.playhead):
            self.apply(tl)

    def delete_side(self, side):
        """Cut away the part of the clip under the line that is left (or right) of it."""
        if self.locks["video"]:
            return
        tl = self.tl.copy()
        i, vt = tl.locate(self.playhead)
        if i < 0:
            return
        clip = tl.clips[i]
        dur = self.video(clip["video"]).duration or clip["out"]
        if (vt - clip["in"] < 0.05) if side == "left" else (clip["out"] - vt < 0.05):
            return
        from .widgets import confirm
        if not confirm(self, ("Delete the part of the clip left of the line?" if side == "left" else
                              "Delete the part of the clip right of the line?") + "\n(Ctrl+Z brings it back.)",
                       "tl_trim", "Delete", danger=True):
            return
        if side == "left":
            if vt - clip["in"] < 0.05:
                return
            tl.trim(i, vt, None, dur)
            self.apply(tl)
            self.seek_to(tl.start_of(i))
        else:
            if clip["out"] - vt < 0.05:
                return
            tl.trim(i, None, vt, dur)
            self.apply(tl)

    def undo(self):
        if self._undo:
            self._redo.append(self.tl.copy())
            self.tl = self._undo.pop()
            self._changed()

    def redo(self):
        if self._redo:
            self._undo.append(self.tl.copy())
            self.tl = self._redo.pop()
            self._changed()

    def retime(self, video, index, a, b, keep_length=False):
        """A line dragged: kept inside its video and between its neighbours (moved: same length)."""
        info = self.video(video)
        prev_end = max([e for (_s, e, _t, k) in info.cues if k < index] or [0.0])
        next_start = min([s for (s, _e, _t, k) in info.cues if k > index] or [info.duration or b + 3600])
        if keep_length:
            n = min(b - a, next_start - prev_end)
            a = min(max(a, prev_end), next_start - n)
            b = a + n
        else:
            a, b = max(a, prev_end), min(b, next_start)
        if b - a < 0.2:
            return
        self.retimed.emit(video, index, round(a, 3), round(b, 3))

    # ------------------------------------------------------------ view
    def seek_to(self, t):
        t = max(0.0, min(t, self.tl.total()))
        self.playhead = t
        self.seek.emit(t)
        self.canvas.update()

    HOLD = 3.0          # seconds after the user scrolled before the view follows the playhead again

    def reveal(self, t):
        """The playhead at t (timeline time), scrolled into sight if it is not (the view's own move)."""
        self.playhead = max(0.0, min(t, self.tl.total()))
        x = self.canvas.x_of(self.playhead)
        w = self.canvas.width()
        if x < 0 or x > w - 40:
            self._auto = True
            self.scroll.setValue(int(max(0.0, self.playhead * self.px - w * 0.3)))
            self._auto = False
        self.canvas.update()

    def _released(self):
        import time
        self._user_until = time.monotonic() + self.HOLD     # let go: a moment more to look before it follows

    def _scrolled(self, _v):
        if not self._auto:                             # the user moved it (bar, wheel, zoom): leave it there
            import time
            self._user_until = time.monotonic() + self.HOLD
        self.canvas.update()

    def set_playhead(self, t, follow=True):
        import time
        old_x = self.canvas.x_of(self.playhead)
        self.playhead = t
        user = self.scroll.isSliderDown() or time.monotonic() < self._user_until
        if follow and not user:                        # keep the line in sight while playing
            x = self.canvas.x_of(t)
            w = self.canvas.width()
            if x > w - 40 or x < 0:
                self._auto = True
                self.scroll.setValue(int(max(0.0, t * self.px - w * 0.2)))
                self._auto = False
                self.canvas.update()                   # scrolled: everything moves
                return
        self.canvas.move_playhead(old_x, self.canvas.x_of(t))

    def zoom(self, factor, at_x=None):
        at_x = self.canvas.width() / 2 if at_x is None else at_x
        t = self.canvas.t_of(at_x)
        self.px = max(self.min_px(), min(400.0, self.px * factor))
        self._update_scroll()
        self.scroll.setValue(int(max(0.0, PAD + t * self.px - at_x)))
        self._sync_slider()
        self.canvas.update()

    def min_px(self) -> float:
        """Zoomed out as far as the whole timeline taking a quarter of the width (to drop videos after it)."""
        total = self.tl.total()
        if total <= 0:
            return 2.0
        return max(0.005, min(2.0, self.canvas.width() * 0.25 / total))

    def fit(self):
        total = self.tl.total()
        if total > 0:
            self.px = max(self.min_px(), min(400.0, (self.canvas.width() - PAD * 2) / total))
            self._update_scroll()
            self.scroll.setValue(0)
            self._sync_slider()
            self.canvas.update()

    def _sync_slider(self):
        import math
        self.zoom_slider.blockSignals(True)
        lo = self.min_px()
        self.zoom_slider.setValue(int(100 * math.log(self.px / lo) / math.log(400.0 / lo)))
        self.zoom_slider.blockSignals(False)

    def _slider_zoom(self, v):
        lo = self.min_px()
        self.zoom(lo * (400.0 / lo) ** (v / 100) / self.px)

    def _update_scroll(self):
        content = PAD * 2 + self.tl.total() * self.px + max(200, self.canvas.width() * 0.4)   # room after the end
        self.scroll.setRange(0, int(max(0, content - self.canvas.width())))
        self.scroll.setPageStep(max(1, self.canvas.width()))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._update_scroll()
