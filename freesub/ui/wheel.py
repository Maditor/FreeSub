"""The mouse wheel scrolls the page, never changes a value by accident.

Drop-down lists, number boxes and sliders inside the panels ignore the wheel: it goes to the scroll area
around them instead. (A drop-down list that is open still scrolls its own items; the preview's volume and
time sliders keep the wheel.)"""
from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QAbstractScrollArea, QAbstractSpinBox, QApplication, QComboBox, QSlider


class WheelGuard(QObject):
    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Wheel:
            target = obj
            # the text box inside a spin box / editable combo gets the event first
            for _ in range(2):
                if isinstance(target, (QComboBox, QAbstractSpinBox, QSlider)):
                    break
                target = target.parentWidget() if hasattr(target, "parentWidget") else None
                if target is None:
                    return False
            if isinstance(target, (QComboBox, QAbstractSpinBox)) or \
                    (isinstance(target, QSlider) and not target.property("wheel_ok")):
                area = target.parentWidget()
                while area is not None and not isinstance(area, QAbstractScrollArea):
                    area = area.parentWidget()
                if area is not None:                        # scroll the panel the widget sits in
                    QApplication.sendEvent(area.viewport(), ev)
                return True
        return False


def install(app):
    guard = WheelGuard(app)
    app.installEventFilter(guard)
    app._wheel_guard = guard
    return guard
