"""Vector icons drawn by FreeSub itself (24x24 SVG), coloured for the current theme.

No icon font is used: a missing font made Windows show other characters instead of icons.
"""
import weakref

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from . import theme

S = 'fill="none" stroke="{c}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"'
F = 'fill="{c}"'

PATHS = {
    "play": f'<path {F} d="M8 5.2v13.6c0 .8.9 1.3 1.6.9l10.4-6.8c.6-.4.6-1.3 0-1.7L9.6 4.3C8.9 3.9 8 4.4 8 5.2z"/>',
    "pause": f'<rect {F} x="6" y="5" width="4.2" height="14" rx="1.2"/><rect {F} x="13.8" y="5" width="4.2" height="14" rx="1.2"/>',
    "stop": f'<rect {F} x="6" y="6" width="12" height="12" rx="2"/>',
    "prev": f'<rect {F} x="5" y="5.5" width="2.4" height="13" rx="1"/><path {F} d="M19 6.3v11.4c0 .8-.9 1.2-1.5.8L9.4 12.8a1 1 0 0 1 0-1.6l8.1-5.7c.6-.4 1.5 0 1.5.8z"/>',
    "next": f'<rect {F} x="16.6" y="5.5" width="2.4" height="13" rx="1"/><path {F} d="M5 6.3v11.4c0 .8.9 1.2 1.5.8l8.1-5.7a1 1 0 0 0 0-1.6L6.5 5.5C5.9 5.1 5 5.5 5 6.3z"/>',
    "back5": f'<path {F} d="M11.5 6.6v10.8c0 .7-.8 1.1-1.4.7L3.4 12.7a.9.9 0 0 1 0-1.4l6.7-5.4c.6-.4 1.4 0 1.4.7zM20.5 6.6v10.8c0 .7-.8 1.1-1.4.7l-6.7-5.4a.9.9 0 0 1 0-1.4l6.7-5.4c.6-.4 1.4 0 1.4.7z"/>',
    "fwd5": f'<path {F} d="M12.5 6.6v10.8c0 .7.8 1.1 1.4.7l6.7-5.4a.9.9 0 0 0 0-1.4l-6.7-5.4c-.6-.4-1.4 0-1.4.7zM3.5 6.6v10.8c0 .7.8 1.1 1.4.7l6.7-5.4a.9.9 0 0 0 0-1.4L4.9 5.9c-.6-.4-1.4 0-1.4.7z"/>',
    "volume": f'<path {F} d="M3.5 9.5v5h3.7l4.8 4V5.5l-4.8 4z"/><path {S} d="M15.5 9a4.2 4.2 0 0 1 0 6M18.3 6.3a8 8 0 0 1 0 11.4"/>',
    "mute": f'<path {F} d="M3.5 9.5v5h3.7l4.8 4V5.5l-4.8 4z"/><path {S} d="M15.5 9.5l5 5M20.5 9.5l-5 5"/>',
    "zoom_in": f'<circle {S} cx="10.5" cy="10.5" r="6.2"/><path {S} d="M15.2 15.2L20 20M8 10.5h5M10.5 8v5"/>',
    "zoom_out": f'<circle {S} cx="10.5" cy="10.5" r="6.2"/><path {S} d="M15.2 15.2L20 20M8 10.5h5"/>',
    "fit": f'<path {S} d="M4 9V5a1 1 0 0 1 1-1h4M15 4h4a1 1 0 0 1 1 1v4M20 15v4a1 1 0 0 1-1 1h-4M9 20H5a1 1 0 0 1-1-1v-4"/>',
    "cc": f'<rect {S} x="3" y="5.5" width="18" height="13" rx="2.5"/><path {S} d="M10.6 10.2a2.3 2.3 0 1 0 0 3.6M16.9 10.2a2.3 2.3 0 1 0 0 3.6"/>',
    "settings": f'<path {S} d="M19.20 10.27 L21.51 10.72 L21.51 13.28 L19.20 13.73 L18.31 15.87 L19.63 17.82 L17.82 19.63 L15.87 18.31 L13.73 19.20 L13.28 21.51 L10.72 21.51 L10.27 19.20 L8.13 18.31 L6.18 19.63 L4.37 17.82 L5.69 15.87 L4.80 13.73 L2.49 13.28 L2.49 10.72 L4.80 10.27 L5.69 8.13 L4.37 6.18 L6.18 4.37 L8.13 5.69 L10.27 4.80 L10.72 2.49 L13.28 2.49 L13.73 4.80 L15.87 5.69 L17.82 4.37 L19.63 6.18 L18.31 8.13Z"/><circle {S} cx="12" cy="12" r="3.2"/>',
    "sun": f'<circle {S} cx="12" cy="12" r="4"/><path {S} d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4"/>',
    "moon": f'<path {S} d="M20 14.6A8.2 8.2 0 0 1 9.4 4a8.2 8.2 0 1 0 10.6 10.6z"/>',
    "log": f'<path {S} d="M5 7h14M5 12h14M5 17h9"/>',
    "delete": f'<path {S} d="M4.5 7h15M9.5 7V5h5v2M6.8 7l.9 12.2c.1.5.5.8 1 .8h6.6c.5 0 .9-.3 1-.8L17.2 7M10 11v5M14 11v5"/>',
    "clear": f'<path {S} d="M4 7h11M4 12h7M4 17h7M15 13l5 5M20 13l-5 5"/>',
    "video": f'<rect {S} x="3" y="6" width="13" height="12" rx="2"/><path {S} d="M16 10.3l4.4-2.6c.3-.2.6 0 .6.4v7.8c0 .4-.3.6-.6.4L16 13.7"/>',
    "translate": f'<path {S} d="M3.5 5h9.5v7.5H8l-3 2.8v-2.8H3.5zM11 10.5h9.5V18H19v2.8L16 18h-5z"/>',
    "mic": f'<rect {S} x="9" y="3" width="6" height="11" rx="3"/><path {S} d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M8.5 21h7"/>',
    "export": f'<path {S} d="M12 15V4M7.5 8.5L12 4l4.5 4.5M4 14v5a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-5"/>',
    "check": f'<path {S} d="M5 12.5l4.5 4.5L19 7"/>',
    "voice_time": f'<circle {S} cx="10" cy="12" r="6.5"/><path {S} d="M10 8.5V12l2.3 1.6"/><path {S} d="M18.5 8.5a5 5 0 0 1 0 7M21 6.5a8.5 8.5 0 0 1 0 11"/>',
    "redo": f'<path {S} d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3"/><path {S} d="M19.8 4.5v4h-4"/>',
    "wrap": f'<path {S} d="M4 6.5h16M4 12h12.5a3 3 0 0 1 0 6H12M4 17.5h4.5M14 15.5L11.5 18l2.5 2.5"/>',
    "stack": f'<path {S} d="M12 4l8.5 4.5L12 13 3.5 8.5z"/><path {S} d="M3.5 12.5L12 17l8.5-4.5M3.5 16.5L12 21l8.5-4.5"/>',
    "font": f'<path {S} d="M5 19l5.5-14h1L17 19M7.4 13.5h7.2M17.5 19h2.5M4 19h2.5"/>',
    "cross": f'<path {S} d="M7 7l10 10M17 7L7 17"/>',
    "win_min": '<path fill="none" stroke="{c}" stroke-width="1" d="M6.5 12.5h11"/>',
    "win_max": '<rect fill="none" stroke="{c}" stroke-width="1" x="6.5" y="6.5" width="11" height="11" rx="1"/>',
    "win_restore": '<rect fill="none" stroke="{c}" stroke-width="1" x="6.5" y="8.5" width="9" height="9" rx="1"/>'
                   '<path fill="none" stroke="{c}" stroke-width="1" d="M8.5 8.5V7.5a1 1 0 0 1 1-1h7a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1h-1"/>',
    "win_close": '<path fill="none" stroke="{c}" stroke-width="1" d="M7 7l10 10M17 7L7 17"/>',
    "dot": f'<circle {F} cx="12" cy="12" r="4.5"/>',
    "square": f'<rect {F} x="7.5" y="7.5" width="9" height="9" rx="1.5"/>',
    "ring": f'<circle {S} cx="12" cy="12" r="4.5"/>',
    "chevron_right": f'<path {S} d="M9.5 6l6 6-6 6"/>',
    "chevron_down": f'<path {S} d="M6 9.5l6 6 6-6"/>',
    "folder": f'<path {S} d="M3.5 7a1.5 1.5 0 0 1 1.5-1.5h4l2 2h8a1.5 1.5 0 0 1 1.5 1.5v8.5A1.5 1.5 0 0 1 19 19H5a1.5 1.5 0 0 1-1.5-1.5z"/>',
    "add": f'<path {S} d="M12 5v14M5 12h14"/>',
    "list": f'<path {S} d="M9 6.5h11M9 12h11M9 17.5h11"/><circle {F} cx="5" cy="6.5" r="1.3"/><circle {F} cx="5" cy="12" r="1.3"/><circle {F} cx="5" cy="17.5" r="1.3"/>',
    "file_add": f'<path {S} d="M13 3.5H7A1.5 1.5 0 0 0 5.5 5v14A1.5 1.5 0 0 0 7 20.5h5M13 3.5l5 5V12M13 3.5v5h5M17 14.5v6.5M13.75 17.75h6.5"/>',
    "folder_add": f'<path {S} d="M11.5 19H5a1.5 1.5 0 0 1-1.5-1.5V7A1.5 1.5 0 0 1 5 5.5h4l2 2h8a1.5 1.5 0 0 1 1.5 1.5v3M17.5 14v7M14 17.5h7"/>',
    "grid": f'<rect {S} x="4" y="4" width="6.5" height="6.5" rx="1.5"/><rect {S} x="13.5" y="4" width="6.5" height="6.5" rx="1.5"/><rect {S} x="4" y="13.5" width="6.5" height="6.5" rx="1.5"/><rect {S} x="13.5" y="13.5" width="6.5" height="6.5" rx="1.5"/>',
    "search": f'<circle {S} cx="10.5" cy="10.5" r="6"/><path {S} d="M15 15l5 5"/>',
    "area": f'<path {S} d="M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3"/><path {S} d="M7.5 14.5h9M9 11.5h6"/>',
    "import": f'<path {S} d="M12 4v11M7.5 10.5L12 15l4.5-4.5M4 14v5a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-5"/>',
    "folder_open": f'<path {S} d="M3.5 18.5V6.5A1.5 1.5 0 0 1 5 5h4l2 2h7a1.5 1.5 0 0 1 1.5 1.5V10M3.5 18.5l2.6-7.2a1.5 1.5 0 0 1 1.4-1h12.9a1 1 0 0 1 .9 1.3l-2.3 6.5a1.5 1.5 0 0 1-1.4 1z"/>',
    "film": f'<rect {S} x="3.5" y="4.5" width="17" height="15" rx="2"/><path {S} d="M7.5 4.5v15M16.5 4.5v15M3.5 9h4M3.5 15h4M16.5 9h4M16.5 15h4"/>',
    "key": f'<circle {S} cx="8" cy="15" r="4"/><path {S} d="M11 12l8.5-8.5M16 7l2.5 2.5M14 9l2 2"/>',
}


def pixmap(key: str, color: str, size: int = 20, ratio: float = 2.0) -> QPixmap:
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">{PATHS.get(key, "")}</svg>'
    svg = svg.replace("{c}", color)
    r = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    px = int(size * ratio)
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    r.render(p, QRectF(0, 0, px, px))
    p.end()
    pm.setDevicePixelRatio(ratio)
    return pm


def icon(key: str, size: int = 20, color: str | None = None, checked_color: str | None = None) -> QIcon:
    c = theme.colors()
    ic = QIcon()
    ic.addPixmap(pixmap(key, color or c["fg"], size), QIcon.Normal, QIcon.Off)
    ic.addPixmap(pixmap(key, checked_color or c["accent"], size), QIcon.Normal, QIcon.On)
    ic.addPixmap(pixmap(key, c["faint"], size), QIcon.Disabled, QIcon.Off)
    ic.addPixmap(pixmap(key, c["faint"], size), QIcon.Disabled, QIcon.On)
    return ic


# widgets that must be repainted when the theme changes
_LIVE = weakref.WeakSet()


def track(widget):
    _LIVE.add(widget)
    return widget


def refresh_all():
    for w in list(_LIVE):
        try:
            w.refresh_icon()
        except RuntimeError:          # the Qt object is gone
            pass


def size(n: int) -> QSize:
    return QSize(n, n)
