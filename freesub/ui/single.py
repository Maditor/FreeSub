"""One FreeSub window at a time.

The first FreeSub listens on a local pipe. A second start (a double click too many, or a video dropped on
FreeSub.exe) finds it, hands over its video paths and quits; the first window comes to the front and opens
those videos.
"""
import getpass
import json
import logging
import os

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

log = logging.getLogger(__name__)


def _name() -> str:
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = "user"
    return "FreeSub-one-window-" + "".join(c for c in user if c.isalnum())[:32]


def hand_over(paths) -> bool:
    """True if a FreeSub is already open: it got the paths and this start should quit."""
    sock = QLocalSocket()
    sock.connectToServer(_name())
    if not sock.waitForConnected(500):
        return False
    if os.name == "nt":
        try:                                    # let the open FreeSub come to the front (Windows' rule)
            import ctypes
            ctypes.windll.user32.AllowSetForegroundWindow(-1)
        except Exception:  # noqa: BLE001
            pass
    sock.write(json.dumps([os.path.abspath(p) for p in paths]).encode("utf-8") + b"\n")
    sock.flush()
    sock.waitForBytesWritten(1000)
    sock.disconnectFromServer()
    return True


class Listener(QObject):
    """In the first FreeSub: wakes the window when another start hands over."""
    wake = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.server = QLocalServer(self)
        QLocalServer.removeServer(_name())      # a pipe left by a FreeSub that crashed (not on Windows)
        if not self.server.listen(_name()):
            log.warning("One-window check not available: %s", self.server.errorString())
        self.server.newConnection.connect(self._connected)

    def _connected(self):
        while self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            sock.readyRead.connect(lambda s=sock: self._read(s))
            sock.disconnected.connect(sock.deleteLater)

    def _read(self, sock):
        data = bytes(sock.readAll()).decode("utf-8", "replace").strip()
        try:
            paths = [p for p in json.loads(data or "[]") if isinstance(p, str)]
        except ValueError:
            paths = []
        self.wake.emit(paths)

    def close(self):
        """Before a restart: the new FreeSub must not find this one."""
        self.server.close()


def bring_to_front(win):
    if win.isMinimized():
        win.showNormal()
    win.show()
    win.raise_()
    win.activateWindow()
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.user32.SetForegroundWindow(int(win.winId()))
        except Exception:  # noqa: BLE001
            pass
