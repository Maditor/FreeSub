"""Asked when Export is pressed (saved for next time on OK): the video with the subtitles burned in, or only
the subtitle files (.srt, original and translation), no video. On the editing page it is the timeline (one
video / one .srt); on the Batch page, each ticked video."""
from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QDialogButtonBox, QVBoxLayout, QWidget

from ..config import settings
from .widgets import Segmented, form_row, label, tick_row


def _combo(items, value):
    c = QComboBox()
    for k, t in items:
        c.addItem(t, k)
    c.setCurrentIndex(max(0, c.findData(value)))
    return c


class ExportDialog(QDialog):
    def __init__(self, parent=None, count: int = 1):
        super().__init__(parent)
        from ..steps import export as ex
        s = settings()
        self.setWindowTitle("Export" + (f" · {count} videos" if count > 1 else ""))
        self.setMinimumWidth(420)
        target = s["export_target"] if s["export_target"] in ("video", "srt") else "video"
        self.target = Segmented([("video", "Video with subtitles"), ("srt", "Subtitles only (.srt)")], target)
        self.which = _combo([("translation", "Translation"), ("original", "Original"),
                             ("both", "Translation + original")], s["export_which"])
        self.res = _combo(ex.RESOLUTIONS, s["export_resolution"])
        self.q = _combo(list(ex.QUALITY_TEXT.items()), s["export_quality"])
        self.open_after = QCheckBox("Open the folder when done")
        self.open_after.setChecked(bool(s["open_folder_after"]))

        # the video's options, hidden for Subtitles only
        self.video_box = QWidget()
        vl = QVBoxLayout(self.video_box)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(10)
        vl.addWidget(form_row("Show", self.which))
        vl.addWidget(form_row("Size", self.res))
        vl.addWidget(form_row("Quality", self.q))

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.addWidget(label("Export", muted=True))
        lay.addWidget(self.target)
        lay.addWidget(self.video_box)
        lay.addWidget(tick_row(self.open_after))
        lay.addSpacing(4)
        lay.addWidget(bb)
        self.target.changed.connect(lambda _v: self._show())
        self._show()

    def _show(self, *_):
        srt = self.target.value() == "srt"
        self.video_box.setVisible(not srt)
        self.adjustSize()

    def _ok(self):
        settings().update(export_target=self.target.value(), export_subs="hard",
                          export_which=self.which.currentData(), export_resolution=self.res.currentData(),
                          export_quality=self.q.currentData(), open_folder_after=self.open_after.isChecked())
        self.accept()
