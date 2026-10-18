"""Settings for one video only (Batch → gear). 'Shared' keeps the setting every video uses."""
from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QPushButton, QVBoxLayout

from ..config import settings
from ..core.overrides import fields
from ..core.project import Project
from .widgets import form_row, label


class VideoSettingsDialog(QDialog):
    def __init__(self, video: str, parent=None):
        super().__init__(parent)
        import os
        self.project = Project(video)
        over = self.project.meta.get("overrides") or {}
        self.setWindowTitle("Settings for this video")
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        lay.addWidget(label(os.path.basename(video), name="CardTitle"))
        self.combos = []
        s = settings()
        for title, choices in fields():
            c = QComboBox()
            shared = next((t for t, v in choices if all(s[k] == x for k, x in v.items())), "")
            c.addItem(f"Shared ({shared})" if shared else "Shared", None)
            for text, values in choices:
                c.addItem(text, values)
            for i in range(1, c.count()):
                if all(k in over and over[k] == v for k, v in c.itemData(i).items()):
                    c.setCurrentIndex(i)
                    break
            self.combos.append(c)
            lay.addWidget(form_row(title, c))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        reset = QPushButton("All shared")
        reset.clicked.connect(lambda: [c.setCurrentIndex(0) for c in self.combos])
        bb.addButton(reset, QDialogButtonBox.ResetRole)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addSpacing(4)
        lay.addWidget(bb)

    def _ok(self):
        over = {}
        for c in self.combos:
            if c.currentData():
                over.update(c.currentData())
        if over:
            self.project.meta["overrides"] = over
        else:
            self.project.meta.pop("overrides", None)
        self.project.save()
        self.accept()
