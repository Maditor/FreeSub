"""Video preview: Qt Multimedia player in a zoomable graphics view, with the subtitle drawn on top."""
import os

from PySide6.QtCore import QPointF, QRectF, QSizeF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QGraphicsItem, QGraphicsRectItem, QGraphicsScene, QGraphicsSimpleTextItem,
                               QGraphicsView, QHBoxLayout, QLabel, QSlider, QVBoxLayout, QWidget)

from ..config import settings
from ..core.style import sub_style
from ..core.subtitle import cue_at, fmt_clock
from . import theme
from .widgets import IconButton


class Timeline(QSlider):
    """The video's position slider. Parts of the timeline can be lit (the narration made so far)."""

    def __init__(self):
        super().__init__(Qt.Horizontal)
        self.ready = []                 # [(from, to)] seconds
        self.setMinimumHeight(20)
        self.setMouseTracking(True)

    def set_ready(self, spans):
        self.ready = list(spans or [])
        self.update()

    def _x(self, ms, left, width):
        lo, hi = self.minimum(), self.maximum()
        return left + (0 if hi <= lo else (ms - lo) / (hi - lo) * width)

    def paintEvent(self, _e):
        c = theme.colors()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = 7                                            # the handle's radius (as in the theme)
        left, width = r, max(1, self.width() - 2 * r)
        cy = self.height() / 2
        groove = QRectF(left, cy - 2, width, 4)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(c["stroke_strong"]))
        p.drawRoundedRect(groove, 2, 2)
        if self.ready:                                   # narration ready: a lighter accent band
            col = QColor(c["accent"])
            col.setAlpha(110)
            p.setBrush(col)
            for a, b in self.ready:
                x0, x1 = self._x(a * 1000, left, width), self._x(b * 1000, left, width)
                if x1 > x0:
                    p.drawRoundedRect(QRectF(x0, cy - 3, x1 - x0, 6), 2, 2)
        x = self._x(self.value(), left, width)
        on = self.isEnabled()
        p.setBrush(QColor(c["accent"] if on else c["faint"]))
        p.drawRoundedRect(QRectF(left, cy - 2, max(0.0, x - left), 4), 2, 2)
        p.setBrush(QColor(c["accent_hover"] if on and self.underMouse() else (c["accent"] if on else c["faint"])))
        p.drawEllipse(QPointF(x, cy), r, r)
        p.end()


_QT_FAMILY = {}


def quiet_player_log():
    """Qt's video player is built on FFmpeg, which prints to the console. A video it cannot decode on the GPU
    (e.g. AV1 on an RTX 20 card) then floods it with "hwaccel initialisation returned error" while it simply
    decodes on the CPU instead. Only fatal messages are kept."""
    import ctypes
    import glob
    import PySide6
    base = os.path.dirname(PySide6.__file__)
    pats = ["avutil-*.dll"] if os.name == "nt" else ["Qt/lib/libavutil.so*", "libavutil.so*"]
    for pat in pats:
        for path in sorted(glob.glob(os.path.join(base, pat))):
            try:
                ctypes.CDLL(path).av_log_set_level(8)        # AV_LOG_FATAL
                return True
            except (OSError, AttributeError):
                continue
    return False


def qt_family(family: str) -> tuple[str, str]:
    """(family, style) Qt draws with, from the SAME font files the export hands to ffmpeg, so the preview shows
    what gets burned in. Qt may group "Montserrat SemiBold" as family "Montserrat", style "SemiBold"."""
    if family in _QT_FAMILY:
        return _QT_FAMILY[family]
    name, style = family, ""
    try:
        from PySide6.QtGui import QFontDatabase
        from ..core.fonts import find
        files, ass_name = find(family)
        loaded = []
        for f in files:
            fid = QFontDatabase.addApplicationFont(f)
            if fid >= 0:
                loaded += QFontDatabase.applicationFontFamilies(fid)
        low = {n.lower(): n for n in loaded}
        name = low.get(ass_name.lower()) or low.get(family.lower())
        if not name and loaded:                  # e.g. "DejaVu Sans Light" -> family "DejaVu Sans" + a light style
            base = max((n for n in loaded if family.lower().startswith(n.lower())), key=len, default=loaded[0])
            rest = family[len(base):].strip().lower() if family.lower().startswith(base.lower()) else ""
            name = base
            if rest:
                styles = QFontDatabase.styles(base)
                style = next((st for st in styles if st.lower() == rest), "") or \
                    next((st for st in styles if rest in st.lower()), "")
        name = name or family
    except Exception:  # noqa: BLE001
        pass
    _QT_FAMILY[family] = (name, style)
    return name, style


def qt_font(family: str, bold: bool) -> QFont:
    name, style = qt_family(family)
    f = QFont()
    f.setFamilies([name, family])
    if style and not bold:
        f.setStyleName(style)
    else:
        f.setWeight(QFont.Bold if bold else QFont.Normal)
    return f


class SubtitleItem(QGraphicsItem):
    """The subtitle as it will look when exported: text at the bottom (size, colour, margin from Settings), or
    for hard subtitles read with OCR, the read area turned into a dialogue box (blurred picture + tint) with
    the text inside it."""

    def __init__(self):
        super().__init__()
        self.text = ""
        self.rect = QRectF(0, 0, 1280, 720)
        self.box = None            # QRectF of the dialogue box (scene / video pixels) or None
        self.backdrop = None       # blurred QPixmap of the picture under the box
        self.setZValue(10)

    def set_rect(self, rect: QRectF):
        self.prepareGeometryChange()
        self.rect = QRectF(rect)
        self.update()

    def set_text(self, text: str):
        if text != self.text:
            self.text = text
            self.update()

    def set_box(self, box):
        self.box = QRectF(box) if box is not None else None
        if box is None:
            self.backdrop = None
        self.update()

    def set_backdrop(self, pm):
        self.backdrop = pm
        self.update()

    def boundingRect(self):
        return self.rect

    @staticmethod
    def _path(lines, font, cx, top, lh):
        fm = QFontMetricsF(font)
        path = QPainterPath()
        y = top + fm.ascent()
        for line in lines:
            path.addText(cx - fm.horizontalAdvance(line) / 2, y, font, line)
            y += lh
        return path

    def paint(self, p: QPainter, _opt, _w=None):
        if not self.text and self.box is None:
            return
        s = settings()
        r = self.rect
        st = sub_style(r.height())
        p.setRenderHint(QPainter.Antialiasing)
        font = qt_font(st["font"], st["bold"])
        lines = self.text.split("\n")
        font.setPixelSize(st["size"])
        if self.box is not None:                 # OCR: the read area becomes the dialogue box
            b = self.box
            if self.backdrop is not None:
                p.drawPixmap(b, self.backdrop, QRectF(self.backdrop.rect()))
            p.fillRect(b, QColor(0, 0, 0, int(255 * float(s["ocr_box_opacity"]) / 100)))
            if not self.text:                    # "Always visible": the box stays between lines
                return
            font.setPixelSize(max(8, int(min(st["size"], b.height() / (len(lines) * 1.25)))))
            fm = QFontMetricsF(font)
            while font.pixelSize() > 8 and max(fm.horizontalAdvance(l) for l in lines) > b.width() - 12:
                font.setPixelSize(font.pixelSize() - 1)
                fm = QFontMetricsF(font)
            lh = fm.height() * 1.05
            cx, top = b.center().x(), b.center().y() - lh * len(lines) / 2
        else:
            fm = QFontMetricsF(font)
            lh = fm.height() * 1.05
            cx, top = r.center().x(), r.bottom() - st["margin_v"] - lh * len(lines)
        path = self._path(lines, font, cx, top, lh)
        if st["outline"]:
            width = st["outline"] * 2 * font.pixelSize() / max(1, st["size"])
            pen = QPen(QColor(st["outline_color"]), width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            p.strokePath(path, pen)
        p.fillPath(path, QBrush(QColor(st["color"])))


class RegionItem(QGraphicsRectItem):
    """The subtitle area for OCR: a dashed frame with a small label."""

    def __init__(self):
        super().__init__()
        self.setZValue(15)
        self.label = QGraphicsSimpleTextItem("Subtitle area", self)
        self.apply_theme()
        self.setVisible(False)

    def apply_theme(self):
        c = QColor(theme.colors()["accent"])
        pen = QPen(c, 2, Qt.DashLine)
        pen.setCosmetic(True)
        self.setPen(pen)
        fill = QColor(c)
        fill.setAlpha(28)
        self.setBrush(fill)
        self.label.setBrush(c)

    def set_rect(self, rect: QRectF):
        self.setRect(rect)
        f = QFont("Segoe UI")
        f.setPixelSize(max(12, int(rect.height() * 0.18)))
        self.label.setFont(f)
        self.label.setPos(rect.left() + 4, rect.top() - self.label.boundingRect().height() - 2)


class VideoView(QGraphicsView):
    zoom_changed = Signal(float)
    region_drawn = Signal(QRectF)

    def __init__(self):
        super().__init__()
        self.scene_ = QGraphicsScene(self)
        self.setScene(self.scene_)
        self.video = QGraphicsVideoItem()
        self.video.setSize(QSizeF(1280, 720))
        self.scene_.addItem(self.video)
        self.subs = SubtitleItem()
        self.scene_.addItem(self.subs)
        self.region = RegionItem()
        self.scene_.addItem(self.region)
        self.drawing = False
        self._origin = None
        self.message = self.scene_.addText("")
        self.message.setZValue(20)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setFrameShape(QGraphicsView.NoFrame)
        self.fit = True
        self.video.nativeSizeChanged.connect(self._native_size)
        self.apply_theme()
        self._layout_items()

    def apply_theme(self):
        c = theme.colors()
        self.setBackgroundBrush(QColor(c["video"]))
        self.message.setDefaultTextColor(QColor("#9aa1ad"))
        if hasattr(self, "region"):
            self.region.apply_theme()

    def _native_size(self, size):
        if size.width() > 0 and size.height() > 0:
            self.video.setSize(size)
            self._layout_items()
            self.fit_view()

    def _layout_items(self):
        r = QRectF(self.video.pos(), self.video.size())
        self.subs.set_rect(r)
        self.scene_.setSceneRect(r)
        self._place_message()

    def show_message(self, text: str):
        self.message.setPlainText(text)
        f = QFont("Segoe UI")
        f.setPixelSize(max(14, int(self.video.size().height() * 0.035)))
        self.message.setFont(f)
        self._place_message()

    def _place_message(self):
        br = self.message.boundingRect()
        r = QRectF(self.video.pos(), self.video.size())
        self.message.setPos(r.center().x() - br.width() / 2, r.center().y() - br.height() / 2)

    # ---- zoom
    def fit_view(self):
        self.fit = True
        self.resetTransform()
        self.fitInView(QRectF(self.video.pos(), self.video.size()), Qt.KeepAspectRatio)
        self.setDragMode(QGraphicsView.NoDrag)
        self.zoom_changed.emit(self.transform().m11())

    def zoom(self, factor: float):
        cur = self.transform().m11()
        new = max(0.05, min(8.0, cur * factor))
        self.fit = False
        self.scale(new / cur, new / cur)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.zoom_changed.emit(new)

    def actual_size(self):
        self.fit = False
        self.resetTransform()
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.zoom_changed.emit(1.0)

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            self.zoom(1.15 if e.angleDelta().y() > 0 else 1 / 1.15)
            e.accept()
        else:
            super().wheelEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.fit:
            self.fit_view()

    # ---- drawing the subtitle area
    def start_drawing(self):
        self.drawing = True
        self.setDragMode(QGraphicsView.NoDrag)
        self.viewport().setCursor(Qt.CrossCursor)

    def stop_drawing(self):
        self.drawing = False
        self._origin = None
        self.viewport().unsetCursor()
        if not self.fit:
            self.setDragMode(QGraphicsView.ScrollHandDrag)

    def _clamp(self, pt):
        r = QRectF(self.video.pos(), self.video.size())
        return QPointF(min(max(pt.x(), r.left()), r.right()), min(max(pt.y(), r.top()), r.bottom()))

    def mousePressEvent(self, e):
        if self.drawing and e.button() == Qt.LeftButton:
            self._origin = self._clamp(self.mapToScene(e.position().toPoint()))
            self.region.set_rect(QRectF(self._origin, self._origin))
            self.region.setVisible(True)
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self.drawing and self._origin is not None:
            pt = self._clamp(self.mapToScene(e.position().toPoint()))
            self.region.set_rect(QRectF(self._origin, pt).normalized())
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self.drawing and self._origin is not None and e.button() == Qt.LeftButton:
            rect = self.region.rect()
            self.stop_drawing()
            if rect.width() > 10 and rect.height() > 6:
                self.region_drawn.emit(rect)
            return
        super().mouseReleaseEvent(e)


class Preview(QWidget):
    end_reached = Signal()                 # played to the end of what the file has (a growing preview copy)
    position_changed = Signal(float)       # seconds
    region_selected = Signal(list)         # [x, y, w, h] fractions of the picture

    def __init__(self):
        super().__init__()
        self.cues = []
        self.path = ""
        self.duration = 0.0
        self.fps = 30.0
        self._seeking = False
        self.region = None                 # [x, y, w, h] fractions
        self.show_region = False
        self.box_mode = False

        self.view = VideoView()
        self.player = QMediaPlayer(self)
        quiet_player_log()                       # after the player: its FFmpeg is loaded by now
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.view.video)
        self.audio.setVolume(settings()["preview_volume"] / 100)
        from .mix_audio import MixAudio
        from .audio_engine import AudioEngine
        self.engine = AudioEngine(self.player, self)        # one sound stream for the narration preview
        self.engine.volume = settings()["preview_volume"] / 100
        self.mix = MixAudio(self.player, self.audio, self.engine, self)

        # controls
        self.b_play = IconButton("play", "Play / pause (Space)", 18)
        self.b_back = IconButton("back5", "Back 5 s (Left)")
        self.b_fwd = IconButton("fwd5", "Forward 5 s (Right)")
        self.b_prev = IconButton("prev", "Previous subtitle (Up)")
        self.b_next = IconButton("next", "Next subtitle (Down)")
        self.slider = Timeline()
        self.slider.setRange(0, 0)
        self.slider.setProperty("wheel_ok", True)
        self.time = QLabel("0:00.0 / 0:00.0")
        self.time.setProperty("muted", True)
        self.time.setMinimumWidth(130)
        self.time.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.b_cc = IconButton("cc", "Show subtitles on the video", checkable=True)
        self.b_cc.setChecked(bool(settings()["preview_show_subs"]))
        self.b_mix = IconButton("mic", "Narration: make it and hear it while the video plays (no export). "
                                       "The timeline lights up as far as it is ready.", checkable=True)
        self.b_mix.setEnabled(False)
        self.b_mix.setContextMenuPolicy(Qt.CustomContextMenu)     # right-click: make the narration again
        from .live_audio import LiveNarration
        self.live = LiveNarration(self.player, self.audio, self._master, self.engine, self)
        self.live.ready_changed.connect(self.slider.set_ready)
        self.b_vol = IconButton("volume", "Mute")
        self.vol = QSlider(Qt.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setValue(int(settings()["preview_volume"]))
        self.vol.setFixedWidth(80)
        self.vol.setProperty("wheel_ok", True)
        self.b_zout = IconButton("zoom_out", "Zoom out (Ctrl + wheel)")
        self.zoom_label = QLabel("Fit")
        self.zoom_label.setProperty("muted", True)
        self.zoom_label.setMinimumWidth(38)
        self.zoom_label.setAlignment(Qt.AlignCenter)
        self.b_zin = IconButton("zoom_in", "Zoom in (Ctrl + wheel)")
        self.b_fit = IconButton("fit", "Fit to window (double-click the label: 100%)")
        self.b_area = IconButton("area", "Draw the subtitle area (for reading hard subtitles)", checkable=True)

        bar = QHBoxLayout()
        bar.setContentsMargins(8, 4, 8, 4)
        bar.setSpacing(2)
        for w in (self.b_prev, self.b_back, self.b_play, self.b_fwd, self.b_next):
            bar.addWidget(w)
        bar.addSpacing(8)
        bar.addWidget(self.slider, 1)
        bar.addWidget(self.time)
        bar.addSpacing(8)
        for w in (self.b_area, self.b_mix, self.b_cc, self.b_vol, self.vol, self.b_zout, self.zoom_label, self.b_zin, self.b_fit):
            bar.addWidget(w)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.view, 1)
        lay.addLayout(bar)

        self.b_play.clicked.connect(self.toggle)
        self.b_back.clicked.connect(lambda: self.seek_rel(-5))
        self.b_fwd.clicked.connect(lambda: self.seek_rel(5))
        self.b_prev.clicked.connect(lambda: self.jump_cue(-1))
        self.b_next.clicked.connect(lambda: self.jump_cue(1))
        self.b_cc.toggled.connect(self._cc)
        self.b_vol.clicked.connect(self._mute)
        self.vol.valueChanged.connect(self._volume)
        self.b_zin.clicked.connect(lambda: self.view.zoom(1.25))
        self.b_zout.clicked.connect(lambda: self.view.zoom(0.8))
        self.b_fit.clicked.connect(self.view.fit_view)
        self.zoom_label.mouseDoubleClickEvent = lambda e: self.view.actual_size()
        self.view.zoom_changed.connect(self._zoom_label)
        self.b_area.toggled.connect(self._area_tool)
        self.view.region_drawn.connect(self._region_drawn)
        self.view.video.nativeSizeChanged.connect(lambda _s: self._place_region())
        self.view.video.videoSink().videoFrameChanged.connect(self._frame)
        self.slider.sliderPressed.connect(self._drag_start)
        self.slider.sliderReleased.connect(self._slider_released)
        self.slider.sliderMoved.connect(self._slider_moved)
        self.player.positionChanged.connect(self._position)
        self.player.durationChanged.connect(self._duration)
        self.player.mediaStatusChanged.connect(self._media_status)
        self.full_duration = 0.0           # the real length while a growing copy is played
        self.seek_hook = None              # seconds -> True when it took care of the jump
        self._pending = None               # (seconds, play) to apply once the file is loaded again
        self.player.playbackStateChanged.connect(self._state)
        self.player.errorOccurred.connect(self._error)
        self._move_timer = QTimer(self)
        self._move_timer.setSingleShot(True)
        self._move_timer.setInterval(60)
        self._move_timer.timeout.connect(lambda: self.seek(self.slider.value() / 1000))
        self.set_enabled(False)
        self.view.show_message("Add a video to start")

    # ------------------------------------------------------------ public
    def reload(self, seconds: float, play: bool):
        """Open the (growing) file again to see what was added, and go back to `seconds`."""
        if not getattr(self, "media", None):
            return
        self._pending = (max(0.0, seconds), play)
        self._armed = False                 # wait for the NEW file to load (not a late signal of the old one)
        self.player.setSource(QUrl())
        self.player.setSource(QUrl.fromLocalFile(os.path.abspath(self.media)))

    def _media_status(self, st):
        if st in (QMediaPlayer.NoMedia, QMediaPlayer.LoadingMedia):
            self._armed = True
        elif st in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia) and self._pending is not None \
                and getattr(self, "_armed", False) and self.player.duration() > 0:
            sec, play = self._pending
            self._pending = None
            self.player.setPosition(int(sec * 1000))
            if play:
                self.player.play()
            else:
                self.player.pause()
        elif st == QMediaPlayer.EndOfMedia:
            self.end_reached.emit()

    def load(self, path: str, fps: float = 0.0, media: str | None = None, waiting: str = "",
             full_duration: float = 0.0):
        """path: the video (its identity); media: the file actually played (a preview copy for AV1...);
        waiting: a message shown instead of a picture while the copy is being made."""
        media = None if waiting else (media or path)
        if path == self.path and media == getattr(self, "media", None):
            return
        self.player.stop()
        if path != self.path:
            self.set_mix(None, None)             # another video: its own mix is set by the main window
        self.path = path
        self.media = media
        self.full_duration = full_duration
        self._pending = None
        self.fps = fps or 30.0
        self.cues = []
        self.view.subs.set_text("")
        self.view.subs.set_box(None)
        if not path:
            self.player.setSource(QUrl())
            self.view.show_message("Add a video to start")
            self.set_enabled(False)
            return
        if waiting:
            self.player.setSource(QUrl())
            self.view.show_message(waiting)
            self.set_enabled(False)
            return
        self.view.show_message("")
        self.player.setSource(QUrl.fromLocalFile(os.path.abspath(media)))
        self.set_enabled(True)
        # show the first frame
        self.player.pause()

    def set_cues(self, cues):
        self.cues = cues or []
        self._update_sub(self.player.position() / 1000)

    def seek(self, seconds: float):
        seconds = max(0.0, seconds)
        if self.seek_hook and self.seek_hook(seconds):   # a growing preview copy handles it
            return
        self.player.setPosition(int(seconds * 1000))

    def seek_rel(self, delta: float):
        self.seek(self.player.position() / 1000 + delta)

    def step_frame(self, n: int):
        self.player.pause()
        self.seek(self.player.position() / 1000 + n / self.fps)

    def toggle(self):
        if not self.path:
            return
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def pause(self):
        self.player.pause()

    def jump_cue(self, direction: int):
        if not self.cues:
            return
        t = self.player.position() / 1000
        if direction > 0:
            nxt = next((c for c in self.cues if c.start > t + 0.05), None)
        else:
            cur = cue_at(self.cues, t)
            before = [c for c in self.cues if c.start < t - 0.3]
            nxt = before[-1] if before else (self.cues[0] if cur < 0 else None)
        if nxt:
            self.seek(nxt.start + 0.01)

    def set_enabled(self, on: bool):
        for w in (self.b_play, self.b_back, self.b_fwd, self.b_prev, self.b_next, self.slider, self.b_mix):
            w.setEnabled(on)

    def _master(self) -> float:
        return 0.0 if getattr(self, "_muted", False) else self.vol.value() / 100

    def apply_theme(self):
        self.view.apply_theme()

    # ------------------------------------------------------------ OCR area / dialogue box
    def start_area(self):
        if self.path:
            self.b_area.setChecked(True)

    def _area_tool(self, on):
        if on:
            self.player.pause()
            self.view.start_drawing()
            self.view.show_message("Drag a box around the subtitles")
        else:
            self.view.stop_drawing()
            self.view.show_message("")

    def _region_drawn(self, rect):
        size = self.view.video.size()
        x, y = rect.left() - self.view.video.pos().x(), rect.top() - self.view.video.pos().y()
        self.region = [round(x / size.width(), 4), round(y / size.height(), 4),
                       round(rect.width() / size.width(), 4), round(rect.height() / size.height(), 4)]
        self.b_area.setChecked(False)
        self._place_region()
        self.region_selected.emit(self.region)

    def set_region(self, region, show: bool, box_mode: bool):
        self.region = list(region) if region else None
        self.show_region = show
        self.box_mode = box_mode and bool(region)
        self.b_area.setVisible(show)
        self._place_region()
        self._update_sub(self.player.position() / 1000)

    def _region_rect(self):
        if not self.region:
            return None
        size = self.view.video.size()
        x, y, w, h = self.region
        o = self.view.video.pos()
        return QRectF(o.x() + x * size.width(), o.y() + y * size.height(), w * size.width(), h * size.height())

    def _place_region(self):
        r = self._region_rect()
        self.view.region.setVisible(bool(r) and self.show_region and not self.box_mode_active())
        if r:
            self.view.region.set_rect(r)

    def box_mode_active(self):
        return self.box_mode and (bool(self.view.subs.text) or bool(settings()["sub_box_always"]))

    def _frame(self, frame):
        """Blurred copy of the picture under the dialogue box (only while a line is shown)."""
        if not (self.box_mode_active() and self.region):
            return
        try:
            img = frame.toImage()
        except Exception:  # noqa: BLE001
            return
        if img.isNull():
            return
        x, y, w, h = self.region
        crop = img.copy(int(x * img.width()), int(y * img.height()), max(1, int(w * img.width())),
                        max(1, int(h * img.height())))
        # like ffmpeg's boxblur in the export: shrink a lot, grow back smoothly (twice)
        k = max(2.0, float(settings()["ocr_box_blur"]) * 1.6)
        img = crop
        for _ in range(2):
            small = img.scaled(max(1, int(crop.width() / k)), max(1, int(crop.height() / k)),
                               Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            img = small.scaled(crop.width(), crop.height(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        self.view.subs.set_backdrop(QPixmap.fromImage(img))

    # ------------------------------------------------------------ internals
    def _position(self, ms):
        if not self._seeking:
            self.slider.blockSignals(True)
            self.slider.setValue(ms)
            self.slider.blockSignals(False)
        t = ms / 1000
        self.time.setText(f"{fmt_clock(t)} / {fmt_clock(self.duration)}")
        self._update_sub(t)
        self.position_changed.emit(t)

    SAMPLE = "Sample subtitle line\nPhụ đề mẫu để xem kiểu chữ"

    def show_sample(self, seconds: float = 4.0):
        """Show a sample line for a few seconds (when no line is on screen) so a style change can be seen."""
        import time
        self._sample_until = time.monotonic() + seconds
        self._update_sub(self.player.position() / 1000)
        QTimer.singleShot(int(seconds * 1000) + 50, lambda: self._update_sub(self.player.position() / 1000))

    def _update_sub(self, t):
        import time
        text = ""
        if self.b_cc.isChecked():
            i = cue_at(self.cues, t)
            text = self.cues[i].text if i >= 0 else ""
        if not text and time.monotonic() < getattr(self, "_sample_until", 0):
            text = self.SAMPLE
        self.view.subs.set_text(text)
        shown = self.box_mode and (bool(text) or bool(settings()["sub_box_always"]))
        self.view.subs.set_box(self._region_rect() if shown else None)
        self.view.region.setVisible(bool(self.region) and self.show_region and not shown)

    def _duration(self, ms):
        self.duration = max(ms / 1000, self.full_duration)
        self.slider.setRange(0, int(self.duration * 1000))
        self.time.setText(f"{fmt_clock(self.player.position() / 1000)} / {fmt_clock(self.duration)}")

    def _state(self, st):
        self.b_play.set_icon("pause" if st == QMediaPlayer.PlayingState else "play")

    def _error(self, _err, msg):
        self.view.show_message(f"Cannot play this file here:\n{msg}\n(processing still works)")

    def _slider_moved(self, v):
        self._move_timer.start()
        self.time.setText(f"{fmt_clock(v / 1000)} / {fmt_clock(self.duration)}")

    def _drag_start(self):
        self._seeking = True
        self.mix.hold(True)                  # the narration waits while the timeline is dragged
        self.live.hold(True)

    def _slider_released(self):
        self._seeking = False
        self.seek(self.slider.value() / 1000)
        self.mix.hold(False)
        self.live.hold(False)

    def _cc(self, on):
        settings().update(preview_show_subs=on)
        self._update_sub(self.player.position() / 1000)

    def set_mix(self, sources, spans):
        """The narration mix of this video (None: there is none yet). The mic button stays as it is: the
        window decides what it does (hear this mix, or make the narration and hear it live)."""
        self.mix.load(sources, spans)
        self.mix.set_enabled(self.b_mix.isChecked() and self.mix.ready() and not self.live.on)

    def _volume(self, v):
        self.audio.setVolume(v / 100)
        self.audio.setMuted(self.mix.on)
        self.engine.volume = v / 100
        if self.live.on:
            self.live._duck()
        self.mix.refresh_levels()
        self.b_vol.set_icon("volume" if v else "mute")
        settings().data["preview_volume"] = v

    def _mute(self):
        m = not getattr(self, "_muted", False)
        self._muted = m
        self.audio.setMuted(m or self.mix.on)
        self.engine.muted = m
        self.b_vol.set_icon("mute" if m else "volume")

    def _zoom_label(self, z):
        self.zoom_label.setText("Fit" if self.view.fit else f"{z * 100:.0f}%")
