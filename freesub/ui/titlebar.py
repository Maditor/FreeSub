"""FreeSub's own title bar (like Photoshop or the Office apps): the command bar sits in the window's title bar.

Windows: the window keeps its native frame styles (so snapping, Aero Shake, the minimize / maximize
animations, the shadow and Alt+Space still work) but its whole area is client area (WM_NCCALCSIZE),
and WM_NCHITTEST tells Windows where the edges (resize) and the empty parts of our bar (move, double-click to
maximize) are. Other systems: a frameless window moved with startSystemMove().
Settings → General → "Use the Windows title bar" turns all of this off.
"""
import ctypes
import logging
import math
import os

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QToolButton, QWidget

from . import icons, theme

log = logging.getLogger(__name__)
WIN = os.name == "nt"

WM_NCCALCSIZE, WM_NCHITTEST, WM_NCACTIVATE = 0x0083, 0x0084, 0x0086
WVR_REDRAW = 0x0300                                 # repaint the whole client area after a size change
HTCLIENT, HTCAPTION = 1, 2
HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT, HTTOPRIGHT, HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 10, 11, 12, 13, 14, 15, 16, 17
GWL_STYLE = -16
WS_CAPTION, WS_THICKFRAME, WS_MINIMIZEBOX, WS_MAXIMIZEBOX, WS_SYSMENU = 0x00C00000, 0x00040000, 0x00020000, \
    0x00010000, 0x00080000
SW_MAXIMIZE, SW_MINIMIZE, SW_RESTORE = 3, 6, 9
SWP_FLAGS = 0x0001 | 0x0002 | 0x0004 | 0x0020      # NOSIZE | NOMOVE | NOZORDER | FRAMECHANGED


class WinButton(QToolButton):
    """Minimize / maximize / close, sized and coloured like the Windows 11 caption buttons."""

    def __init__(self, key, tip, close=False):
        super().__init__()
        self.key, self.close = key, close
        self.setToolTip(tip)
        self.setFixedSize(46, 40)           # the bar's full height, flush with the window's top-right corner
        self.setFocusPolicy(Qt.NoFocus)
        self.setIconSize(icons.size(16))
        self.setObjectName("WinClose" if close else "WinButton")
        icons.track(self)
        self.refresh_icon()

    def set_key(self, key):
        self.key = key
        self.refresh_icon()

    def refresh_icon(self):
        c = theme.colors()
        ic = icons.icon(self.key, 16, color=c["fg"])
        if self.close:                                   # white cross on the red hover
            from PySide6.QtGui import QIcon
            ic.addPixmap(icons.pixmap(self.key, "#ffffff", 16), QIcon.Active, QIcon.Off)
        self.setIcon(ic)


class WindowButtons(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window_ = window
        self.frameless = None                 # set by Frameless: on Windows the buttons ask Windows directly
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.b_min = WinButton("win_min", "Minimize")
        self.b_max = WinButton("win_max", "Maximize")
        self.b_close = WinButton("win_close", "Close", close=True)
        for b in (self.b_min, self.b_max, self.b_close):
            lay.addWidget(b, 0, Qt.AlignTop)
        self.setFixedSize(46 * 3, 40)       # never squeezed, whatever the window size or state
        self.b_min.clicked.connect(self.minimize)
        self.b_max.clicked.connect(self.toggle)
        self.b_close.clicked.connect(window.close)

    def minimize(self):
        if not (self.frameless and self.frameless.show_native(SW_MINIMIZE)):
            self.window_.showMinimized()

    def toggle(self):
        w = self.window_
        maxed = self.frameless.zoomed() if self.frameless else w.isMaximized()
        if not (self.frameless and self.frameless.show_native(SW_RESTORE if maxed else SW_MAXIMIZE)):
            w.showNormal() if maxed else w.showMaximized()

    def sync(self):
        m = self.frameless.zoomed() if self.frameless else self.window_.isMaximized()
        self.b_max.set_key("win_restore" if m else "win_max")
        self.b_max.setToolTip("Restore down" if m else "Maximize")


class Frameless:
    """Gives the window's title bar to `bar` (a widget along the top of the window).

    On Windows the client area is always the whole window rectangle (no non-client area at all, in every
    state). That keeps Qt's idea of the window frame at zero, so Qt and Windows never disagree about the
    window's size after maximize / restore. A maximized window hangs over the screen edges by its (invisible)
    frame; instead of shrinking the client area for that, the window's contents get a matching margin.
    """

    def __init__(self, window, bar, buttons: WindowButtons):
        self.w, self.bar, self.buttons = window, bar, buttons
        self.ok = False
        self._pad = (0, 0, 0, 0)
        buttons.frameless = self
        window.setWindowFlags(window.windowFlags() | Qt.FramelessWindowHint)
        if not WIN:
            self._move = _MoveFilter(window, bar)
            bar.installEventFilter(self._move)
        self._state = _StateFilter(self, window)
        window.installEventFilter(self._state)

    # ---- Windows
    def native_ready(self):
        """After the window exists: native frame styles back on (snap, animations), frame recalculated."""
        if not WIN:
            return
        try:
            u = ctypes.windll.user32
            hwnd = int(self.w.winId())
            style = u.GetWindowLongW(hwnd, GWL_STYLE)
            u.SetWindowLongW(hwnd, GWL_STYLE, style | WS_CAPTION | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX
                             | WS_SYSMENU)

            class MARGINS(ctypes.Structure):
                _fields_ = [("l", ctypes.c_int), ("r", ctypes.c_int), ("t", ctypes.c_int), ("b", ctypes.c_int)]
            ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(MARGINS(0, 0, 1, 0)))   # shadow
            self.ok = True
            u.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_FLAGS)
            self.update_padding()
            self.buttons.sync()
        except Exception as e:  # noqa: BLE001
            log.warning("Own title bar: %s", e)

    def _hwnd(self):
        return int(self.w.winId())

    def zoomed(self) -> bool:
        """Maximized, as Windows sees it right now (Qt's isMaximized() can lag behind)."""
        if WIN and self.ok:
            try:
                return bool(ctypes.windll.user32.IsZoomed(self._hwnd()))
            except Exception:  # noqa: BLE001
                pass
        return self.w.isMaximized()

    def show_native(self, cmd) -> bool:
        """Minimize / maximize / restore through Windows itself (with its animations); False if not possible."""
        if not (WIN and self.ok):
            return False
        try:
            ctypes.windll.user32.ShowWindow(self._hwnd(), cmd)
            return True
        except Exception:  # noqa: BLE001
            return False

    def update_padding(self):
        """Margin around the contents = how far a maximized window hangs over its screen's work area."""
        pad = (0, 0, 0, 0)
        if WIN and self.ok and self.zoomed():
            try:
                from ctypes import wintypes
                hwnd = self._hwnd()
                r = wintypes.RECT()
                ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
                work = self._work_area(hwnd)
                if work is not None:
                    dpr = self.w.devicePixelRatioF() or 1.0
                    over = (work.left - r.left, work.top - r.top, r.right - work.right, r.bottom - work.bottom)
                    pad = tuple(max(0, math.ceil(v / dpr)) for v in over)
            except Exception:  # noqa: BLE001
                pad = (0, 0, 0, 0)
        if pad != self._pad:
            self._pad = pad
            self.w.setContentsMargins(*pad)
        self.buttons.sync()

    def refresh(self):
        """Moved to another screen (maybe another scale): frame recomputed, margin and paint updated."""
        if WIN and self.ok:
            try:
                ctypes.windll.user32.SetWindowPos(self._hwnd(), 0, 0, 0, 0, 0, SWP_FLAGS)
            except Exception:  # noqa: BLE001
                pass
        self.update_padding()
        self.w.update()

    @staticmethod
    def _work_area(hwnd):
        """The work area (screen minus taskbar) of the monitor the window is on, or None."""
        from ctypes import wintypes

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                        ("dwFlags", wintypes.DWORD)]
        u = ctypes.windll.user32
        u.MonitorFromWindow.restype = ctypes.c_void_p
        mon = u.MonitorFromWindow(wintypes.HWND(hwnd), 2)          # MONITOR_DEFAULTTONEAREST
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if not mon or not u.GetMonitorInfoW(ctypes.c_void_p(mon), ctypes.byref(mi)):
            return None
        return mi.rcWork

    def native(self, event_type, message):
        """(handled, result) for the window's nativeEvent, or None to let Qt handle it."""
        if not (WIN and self.ok) or event_type != b"windows_generic_MSG":
            return None
        from ctypes import wintypes
        msg = wintypes.MSG.from_address(int(message))
        if msg.message == WM_NCCALCSIZE:
            # client area = the whole window, always (no grey Windows title bar, no frame Qt could miscount)
            return True, (WVR_REDRAW if msg.wParam else 0)
        if msg.message == WM_NCACTIVATE:
            return True, 1                       # do not let Windows paint its title bar when focus changes
        if msg.message == WM_NCHITTEST:
            x = ctypes.c_short(msg.lParam & 0xFFFF).value
            y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
            r = wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(msg.hWnd, ctypes.byref(r))
            dpr = self.w.devicePixelRatioF() or 1.0
            edge = int(6 * dpr)
            if not self.zoomed():
                left, right = x < r.left + edge, x >= r.right - edge
                top, bottom = y < r.top + edge, y >= r.bottom - edge
                hit = {(True, False, True, False): HTTOPLEFT, (False, True, True, False): HTTOPRIGHT,
                       (True, False, False, True): HTBOTTOMLEFT, (False, True, False, True): HTBOTTOMRIGHT,
                       (True, False, False, False): HTLEFT, (False, True, False, False): HTRIGHT,
                       (False, False, True, False): HTTOP, (False, False, False, True): HTBOTTOM}.get(
                    (left, right, top, bottom))
                if hit:
                    return True, hit
            local = QPoint(int((x - r.left) / dpr), int((y - r.top) / dpr))
            if self.is_caption(local):
                return True, HTCAPTION
            return True, HTCLIENT
        return None

    def is_caption(self, pt: QPoint) -> bool:
        """True on the empty parts of the bar (and its plain labels): drag / double-click there."""
        bar_pos = self.bar.mapTo(self.w, QPoint(0, 0))
        if not (bar_pos.y() <= pt.y() < bar_pos.y() + self.bar.height()):
            return False
        child = self.w.childAt(pt)
        return child is None or child is self.bar or (isinstance(child, QLabel) and child.parent() is self.bar)


class _StateFilter(QObject):
    def __init__(self, fl, parent):
        super().__init__(parent)
        self.fl = fl

    def eventFilter(self, obj, e):
        t = e.type()
        if t in (QEvent.WindowStateChange, QEvent.Resize, QEvent.Move):
            self.fl.update_padding()
            if t == QEvent.WindowStateChange:      # Windows may finish placing the window a moment later
                QTimer.singleShot(0, self.fl.update_padding)
        elif t == QEvent.ScreenChangeInternal:    # moved to another screen (maybe another scale)
            QTimer.singleShot(0, self.fl.refresh)
        return False


class _MoveFilter(QObject):
    """Other systems: drag the bar to move the window, double-click it to maximize."""

    def __init__(self, window, bar):
        super().__init__(window)
        self.w, self.bar = window, bar

    def eventFilter(self, obj, e):
        if e.type() == QEvent.MouseButtonPress and e.button() == Qt.LeftButton:
            h = self.w.windowHandle()
            if h is not None:
                h.startSystemMove()
            return False
        if e.type() == QEvent.MouseButtonDblClick:
            self.w.showNormal() if self.w.isMaximized() else self.w.showMaximized()
        return False
