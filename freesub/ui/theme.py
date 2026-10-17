"""Light / dark theme: color tokens + one Qt stylesheet. 'system' follows Windows."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette

# Fluent 2 / Windows 11 colours: neutral layers, one blue accent, thin strokes.
DARK = dict(
    bg="#202020", panel="#272727", raised="#2d2d2d", field="#2d2d2d", hover="#353535", border="#3a3a3a",
    stroke_strong="#4a4a4a", fg="#ffffff", muted="#c5c5c5", faint="#8a8a8a", accent="#60cdff",
    accent_hover="#5ab9e6", accent_fg="#000000", ok="#6ccb5f", warn="#fce100", err="#ff99a4", video="#000000",
    select="rgba(96, 205, 255, 0.16)", pressed="#2a2a2a",
)
LIGHT = dict(
    bg="#f3f3f3", panel="#fbfbfb", raised="#ffffff", field="#ffffff", hover="#ececec", border="#e0e0e0",
    stroke_strong="#c8c8c8", fg="#1b1b1b", muted="#5c5c5c", faint="#8a8a8a", accent="#005fb8",
    accent_hover="#1a6fc0", accent_fg="#ffffff", ok="#0f7b0f", warn="#9d5d00", err="#c42b1c", video="#1b1b1b",
    select="rgba(0, 95, 184, 0.10)", pressed="#e4e4e4",
)

_current = {"name": "dark", "c": DARK}


def colors() -> dict:
    return _current["c"]


def is_dark() -> bool:
    return _current["name"] == "dark"


def system_is_dark() -> bool:
    try:
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except Exception:  # noqa: BLE001
        return True


def applied(mode: str) -> bool:
    """The stylesheet for this mode is already on (no need to restyle every widget again)."""
    dark = system_is_dark() if mode == "system" else mode == "dark"
    return _current.get("name") == ("dark" if dark else "light") and _current.get("done", False)


def apply(app, mode: str):
    """mode: system | light | dark."""
    dark = system_is_dark() if mode == "system" else mode == "dark"
    c = DARK if dark else LIGHT
    _current.update(name="dark" if dark else "light", c=c)
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(c["bg"]))
    pal.setColor(QPalette.WindowText, QColor(c["fg"]))
    pal.setColor(QPalette.Base, QColor(c["field"]))
    pal.setColor(QPalette.AlternateBase, QColor(c["raised"]))
    pal.setColor(QPalette.Text, QColor(c["fg"]))
    pal.setColor(QPalette.Button, QColor(c["raised"]))
    pal.setColor(QPalette.ButtonText, QColor(c["fg"]))
    pal.setColor(QPalette.Highlight, QColor(c["accent"]))
    pal.setColor(QPalette.HighlightedText, QColor(c["accent_fg"]))
    pal.setColor(QPalette.ToolTipBase, QColor(c["raised"]))
    pal.setColor(QPalette.ToolTipText, QColor(c["fg"]))
    pal.setColor(QPalette.PlaceholderText, QColor(c["faint"]))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor(c["faint"]))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(c["faint"]))
    pal.setColor(QPalette.Disabled, QPalette.WindowText, QColor(c["faint"]))
    app.setPalette(pal)
    # font and text colour for every widget come from the application font and palette, not from a "*" rule
    # in the stylesheet (a rule for every widget makes switching light / dark noticeably slower)
    from PySide6.QtGui import QFont
    f = QFont()
    f.setFamilies(["Segoe UI Variable Text", "Segoe UI", "sans-serif"])
    f.setPixelSize(13)
    app.setFont(f)
    app.setStyleSheet(stylesheet(c))
    _current["done"] = True


_CHECKS = {}


def _check_image(color: str, kind: str = "check") -> str:
    """A tick mark (or chevron) drawn once into a small PNG (style sheets take images from files)."""
    if (color, kind) in _CHECKS:
        return _CHECKS[(color, kind)]
    import os
    from PySide6.QtCore import QByteArray, QRectF
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    from ..config import data_dir
    d = {"check": "M3.6 8.3l2.9 2.9 5.9-6.4", "down": "M4 6l4 4 4-4", "up": "M4 10l4-4 4 4"}[kind]
    w = 1.8 if kind == "check" else 1.3
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><path d="{d}" fill="none" '
           f'stroke="{color}" stroke-width="{w}" stroke-linecap="round" stroke-linejoin="round"/></svg>')
    path = os.path.join(data_dir(), "ui", f"{kind}_{color.strip('#')}.png")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        img = QImage(32, 32, QImage.Format_ARGB32)
        img.fill(0)
        p = QPainter(img)
        QSvgRenderer(QByteArray(svg.encode())).render(p, QRectF(0, 0, 32, 32))
        p.end()
        img.save(path)
    except Exception:  # noqa: BLE001
        return ""
    _CHECKS[(color, kind)] = path.replace("\\", "/")
    return _CHECKS[(color, kind)]


def stylesheet(c: dict) -> str:
    chk, down, up = _check_image(c["accent_fg"]), _check_image(c["muted"], "down"), _check_image(c["muted"], "up")
    return f"""
QMainWindow, QDialog {{ background: {c['bg']}; }}
QToolTip {{ background: {c['raised']}; color: {c['fg']}; border: 1px solid {c['border']}; border-radius: 4px; padding: 5px 8px; }}

#Panel {{ background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 8px; }}
#Header, #CommandBar {{ background: {c['bg']}; }}
#AppTitle {{ font-size: 14px; font-weight: 600; }}
#Muted, QLabel[muted="true"] {{ color: {c['muted']}; }}
#SectionTitle {{ font-size: 12px; font-weight: 600; color: {c['fg']}; }}
#PanelTitle {{ font-size: 20px; font-weight: 600; }}
#PaneTitle {{ font-size: 13px; font-weight: 600; padding: 2px 0; }}
#Soon {{ color: {c['muted']}; font-size: 13px; }}

QPushButton {{ background: {c['raised']}; border: 1px solid {c['border']}; border-bottom-color: {c['stroke_strong']};
    border-radius: 4px; padding: 5px 12px; min-height: 20px; }}
QPushButton:hover {{ background: {c['hover']}; }}
QPushButton:pressed {{ background: {c['pressed']}; color: {c['muted']}; border-bottom-color: {c['border']}; }}
QPushButton:disabled {{ color: {c['faint']}; background: {c['panel']}; border-color: {c['border']}; }}
QPushButton[primary="true"] {{ background: {c['accent']}; color: {c['accent_fg']}; border: 1px solid {c['accent']};
    font-weight: 600; }}
QPushButton[primary="true"]:hover {{ background: {c['accent_hover']}; border-color: {c['accent_hover']}; }}
QPushButton[primary="true"]:disabled {{ background: {c['hover']}; color: {c['faint']}; border-color: {c['hover']}; }}
QToolButton {{ background: transparent; border: none; border-radius: 4px; padding: 4px; }}
QToolButton:hover {{ background: {c['hover']}; }}
QToolButton:pressed {{ background: {c['pressed']}; }}
QToolButton:checked {{ background: {c['select']}; }}
QToolButton:disabled {{ color: {c['faint']}; }}
#CommandBar QToolButton {{ padding: 5px 10px; }}
#CommandBar QToolButton[bar="true"] {{ padding: 0; }}
#CommandBar QToolButton[primary="true"] {{ background: {c['accent']}; color: {c['accent_fg']}; font-weight: 600; }}
#CommandBar QToolButton[primary="true"]:hover {{ background: {c['accent_hover']}; }}
#CommandBar QToolButton[primary="true"]:disabled {{ background: {c['hover']}; color: {c['faint']}; }}
#BarSep {{ background: {c['border']}; }}
QFrame#HLine {{ background: {c['border']}; border: none; }}
QToolButton#WinButton, QToolButton#WinClose {{ border-radius: 0; background: transparent; padding: 0; }}
QToolButton#WinButton:hover {{ background: {c['hover']}; }}
QToolButton#WinButton:pressed {{ background: {c['pressed']}; }}
QToolButton#WinClose:hover {{ background: #c42b1c; }}
QToolButton#WinClose:pressed {{ background: #c8423a; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background: {c['field']}; border: 1px solid {c['border']}; border-bottom-color: {c['stroke_strong']};
    border-radius: 4px; padding: 4px 8px; selection-background-color: {c['accent']}; selection-color: {c['accent_fg']}; }}
QSpinBox, QDoubleSpinBox, QComboBox, QLineEdit {{ min-height: 22px; }}
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {{ background: {c['hover']}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{
    background: {c['field']}; border-bottom: 2px solid {c['accent']}; }}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{ color: {c['faint']}; background: {c['panel']}; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{ image: url({down}); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{ background: {c['raised']}; border: 1px solid {c['border']}; border-radius: 6px;
    selection-background-color: {c['hover']}; selection-color: {c['fg']}; outline: none; padding: 4px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::down-button {{ width: 18px; border: none; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url({up}); width: 10px; height: 10px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url({down}); width: 10px; height: 10px; }}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 4px; border: 1px solid {c['faint']}; background: {c['field']}; }}
QCheckBox::indicator:hover {{ background: {c['hover']}; }}
QCheckBox::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; image: url({chk}); }}
QCheckBox::indicator:checked:hover {{ background: {c['accent_hover']}; border-color: {c['accent_hover']}; }}
QCheckBox::indicator:disabled {{ border-color: {c['border']}; }}
QCheckBox::indicator:checked:disabled {{ background: {c['faint']}; border-color: {c['faint']}; }}
QRadioButton {{ spacing: 8px; }}
QRadioButton::indicator {{ width: 18px; height: 18px; border-radius: 10px; border: 1px solid {c['faint']}; background: {c['field']}; }}
QRadioButton::indicator:checked {{ background: {c['accent_fg']}; border: 5px solid {c['accent']}; }}

QTabWidget::pane {{ border: none; }}
QTabBar {{ qproperty-drawBase: 0; }}
QTabBar::tab {{ background: transparent; color: {c['muted']}; padding: 7px 12px 8px 12px; margin-right: 2px;
    border: none; border-bottom: 3px solid transparent; }}
QTabBar::tab:hover {{ color: {c['fg']}; background: {c['hover']}; border-radius: 4px; }}
QTabBar::tab:selected {{ color: {c['fg']}; font-weight: 600; border-bottom: 3px solid {c['accent']}; }}

QListWidget, QTableWidget, QTreeWidget {{ background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 6px; outline: none;
    alternate-background-color: {c['raised']}; gridline-color: transparent; }}
QListWidget::item {{ padding: 6px 8px; border-radius: 4px; }}
QListWidget::item:hover, QTableWidget::item:hover {{ background: {c['hover']}; }}
QListWidget::item:selected, QTableWidget::item:selected {{ background: {c['select']}; color: {c['fg']}; }}
QTableWidget {{ selection-background-color: {c['select']}; selection-color: {c['fg']}; }}
QTableWidget QLineEdit {{ padding: 0 4px; border-radius: 0; }}
QTableWidget::item {{ padding: 2px 6px; border-bottom: 1px solid {c['border']}; }}
QHeaderView::section {{ background: {c['panel']}; color: {c['muted']}; border: none; border-bottom: 1px solid {c['border']};
    padding: 6px 6px; font-weight: 600; }}
QTableCornerButton::section {{ background: {c['panel']}; border: none; }}

QSlider::groove:horizontal {{ height: 4px; background: {c['stroke_strong']}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {c['accent']}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {c['accent']}; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}
QSlider::handle:horizontal:hover {{ background: {c['accent_hover']}; }}

QProgressBar {{ background: {c['stroke_strong']}; border: none; border-radius: 1px; max-height: 3px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {c['accent']}; border-radius: 1px; }}

QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c['stroke_strong']}; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {c['faint']}; }}
QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {c['stroke_strong']}; border-radius: 3px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

QSplitter::handle {{ background: transparent; }}
QGroupBox {{ border: 1px solid {c['border']}; border-radius: 6px; margin-top: 18px; padding: 10px 8px 8px 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {c['muted']}; font-weight: 600; }}
QMenu {{ background: {c['raised']}; border: 1px solid {c['border']}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 6px 20px 6px 12px; border-radius: 4px; }}
QMenu::item:selected {{ background: {c['hover']}; }}
QMenu::separator {{ height: 1px; background: {c['border']}; margin: 4px 6px; }}
#Card {{ background: {c['raised']}; border: 1px solid {c['border']}; border-radius: 6px; }}
#CardTitle {{ font-size: 13px; font-weight: 600; }}
#CardTitle:disabled {{ color: {c['faint']}; }}
QPushButton#SegFirst, QPushButton#SegMid, QPushButton#SegLast {{ background: {c['raised']}; border: 1px solid {c['border']};
    padding: 5px 10px; border-radius: 0; font-weight: 400; }}
QPushButton#SegFirst {{ border-top-left-radius: 4px; border-bottom-left-radius: 4px; }}
QPushButton#SegLast {{ border-top-right-radius: 4px; border-bottom-right-radius: 4px; border-left: none; }}
QPushButton#SegMid {{ border-left: none; }}
QPushButton#SegFirst:checked, QPushButton#SegMid:checked, QPushButton#SegLast:checked {{ background: {c['select']};
    color: {c['fg']}; font-weight: 600; }}
QPushButton#Big {{ font-size: 14px; padding: 7px 16px; }}
QListWidget#Queue {{ background: transparent; border: none; }}
QListWidget#Queue::item {{ padding: 7px 8px; }}
QListWidget#Queue::item:selected {{ background: {c['select']}; border-left: 3px solid {c['accent']}; }}
QListWidget#QueueGrid {{ background: transparent; border: none; }}
QListWidget#QueueGrid::item {{ padding: 4px 2px 2px 2px; margin: 2px; border-radius: 6px; }}
QListWidget#QueueGrid::item:hover {{ background: {c['hover']}; }}
QListWidget#QueueGrid::item:selected {{ background: {c['select']}; color: {c['fg']}; }}
QListWidget#Projects {{ background: transparent; border: none; }}
QListWidget#Projects::item {{ padding: 8px; border-radius: 6px; color: {c['fg']}; }}
QListWidget#Projects::item:hover {{ background: {c['hover']}; }}
QListWidget#Projects::item:selected {{ background: {c['select']}; color: {c['fg']}; }}
#LogView {{ font-family: "Cascadia Mono", Consolas, monospace; font-size: 12px; background: {c['panel']}; }}
#ChipIdle {{ color: {c['faint']}; }}
#ChipRun {{ color: {c['accent']}; font-weight: 600; }}
#ChipDone {{ color: {c['ok']}; }}
#ChipFail {{ color: {c['err']}; }}
#Error {{ color: {c['err']}; }}
#Ok {{ color: {c['ok']}; }}
"""
