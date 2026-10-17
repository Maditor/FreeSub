"""FreeSub - make, translate and narrate subtitles for videos. https://github.com/Maditor/FreeSub

    python main.py                         open the app
    python main.py --cli export PATH...    run the saved recipe on videos / folders, no window
"""
import argparse
import logging
import os
import sys


def run_cli(paths, redo=False):
    from freesub.core import ffmpeg
    from freesub.core.jobs import Cancelled, Control
    from freesub.steps import pipeline

    videos = []
    for p in paths:
        if os.path.isdir(p):
            for root, _d, files in os.walk(p):
                if not root.lower().endswith(".freesub"):
                    videos += [os.path.join(root, f) for f in sorted(files) if f.lower().endswith(ffmpeg.MEDIA_EXTS)]
        elif os.path.isfile(p):
            videos.append(p)
    if not videos:
        print("No video or audio files found.")
        return 1
    last = {"t": ""}

    def prog(frac, text):
        if text and text != last["t"]:
            last["t"] = text
            print(("\r" + text)[:110].ljust(110), end="", flush=True)

    control = Control(on_progress=prog, on_status=lambda t: print("\n" + t, flush=True))
    failed = 0
    for i, v in enumerate(videos, 1):
        print(f"\n[{i}/{len(videos)}] {v}")
        try:
            proj = pipeline.run_video(v, None, control, force=redo)
            outs = proj.meta.get("outputs", {})
            for k in ("subs.src.srt", "subs.tr.srt", "video"):
                if outs.get(k):
                    print("\n  ->", outs[k])
        except (Cancelled, KeyboardInterrupt):
            print("\nStopped.")
            return 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("\n  FAILED:", e)
    return 1 if failed else 0


def run_check():
    from freesub.core import ffmpeg, gpu, models
    print("ffmpeg :", ffmpeg.find("ffmpeg") or "NOT FOUND", "|", ffmpeg.version())
    print("ffprobe:", ffmpeg.find("ffprobe") or "NOT FOUND")
    n = gpu.cuda_count()
    print("CUDA   :", f"{n} device(s)" if n else "none", "|", gpu.nvidia_name() or "no NVIDIA card found")
    try:
        import onnxruntime as ort
        print("ONNX   :", ", ".join(ort.get_available_providers()))
    except Exception as e:  # noqa: BLE001
        print("ONNX   :", e)
    from freesub.tts import ipa_vi, voices
    print("espeak :", "ok (English words read the Vietnamese way)" if ipa_vi.available() else "missing (run setup.bat)")
    print("Piper  :", "installed" if voices.piper_installed() else "missing",
          "| Vietnamese voice", "downloaded" if voices.piper_ready("vi") else "not downloaded")
    from freesub.core import dxgi
    from freesub.steps import separate
    cards = dxgi.adapters()
    print("DirectML:", ", ".join(f"#{g['id']} {g['name']} ({g['mb']} MB)" for g in cards) or "no card listed",
          "| voice separation uses", f"#{dxgi.best_id()}" if cards else "the CPU")
    from freesub.steps import ocr
    print("OCR    :", "ok (RapidOCR)" if ocr.available() else "missing (run setup.bat)")
    print("Voice separation model:", "downloaded" if separate.model_ready() else "not downloaded (64 MB, on first use)")
    from freesub.steps import speakers
    from freesub.tts import kokoro
    print("Kokoro voices (optional, third-party):", "downloaded" if kokoro.ready() else
          f"not downloaded ({kokoro.SIZE}, on request)", "| text reader:", "ok" if kokoro.g2p_installed() else "missing")
    print("Speaker model:", "downloaded" if speakers.model_ready() else "not downloaded (28 MB, on first use)")
    for name in models.WHISPER:
        print(f"Whisper {name:20s}", "downloaded" if models.whisper_ready(name) else "-")
    return 0


def _splash(app):
    """A small card with the logo while the window is being built (the first start compiles everything)."""
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
        from PySide6.QtWidgets import QSplashScreen

        from freesub import i18n
        from freesub.config import settings
        dark = settings()["theme"] != "light"
        ratio = app.primaryScreen().devicePixelRatio() if app.primaryScreen() else 1.0
        w, h = 360, 140
        pm = QPixmap(int(w * ratio), int(h * ratio))
        pm.setDevicePixelRatio(ratio)
        pm.fill(QColor("#202020" if dark else "#f3f3f3"))
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        icon = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "icon.png")
        if os.path.isfile(icon):
            p.drawPixmap(28, 38, QPixmap(icon).scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        fg = QColor("#ffffff" if dark else "#1b1b1b")
        p.setPen(fg)
        f = QFont("Segoe UI Variable Display", 20)
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        p.drawText(110, 66, "FreeSub")
        p.setPen(QColor("#8a8a8a"))
        p.setFont(QFont("Segoe UI", 10))
        p.drawText(112, 92, i18n.tr("Loading…"))
        p.end()
        s = QSplashScreen(pm)
        s.show()
        app.processEvents()
        return s
    except Exception:  # noqa: BLE001
        return None


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--list-gpus":        # child process of core.dxgi
        from freesub.core import dxgi
        dxgi.write_listing(sys.argv[2])
        return
    ap = argparse.ArgumentParser(prog="FreeSub")
    ap.add_argument("--cli", choices=["export"], help="run the saved recipe (the Export button) without a window")
    ap.add_argument("--redo", action="store_true", help="redo steps that are already done")
    ap.add_argument("--get-ffmpeg", action="store_true", help="download ffmpeg next to the app (setup.bat)")
    ap.add_argument("--say", metavar="TEXT", help="print how a Vietnamese sentence will be read")
    ap.add_argument("--check", action="store_true", help="print what FreeSub can use (GPU, ffmpeg, models)")
    ap.add_argument("paths", nargs="*")
    args = ap.parse_args()

    if args.get_ffmpeg:
        from freesub.core import ffmpeg_setup
        sys.exit(ffmpeg_setup.main())
    if args.say:
        from freesub.tts.pronounce import pronouncer
        spoken, changes = pronouncer().respell_text(args.say, for_display=True)
        print(spoken)
        for w, r, src in changes:
            print(f"  {w:20s} -> {r:24s} ({src})")
        sys.exit(0)
    if args.check:
        sys.exit(run_check())

    from freesub import log as logsetup
    path = logsetup.setup()
    logging.getLogger("freesub").info("FreeSub starting, log: %s", path)

    if args.cli:
        sys.exit(run_cli(args.paths, args.redo))

    if os.name == "nt":
        try:   # own taskbar icon instead of python.exe's
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Maditor.FreeSub")
        except Exception:  # noqa: BLE001
            pass
    import time
    t0 = time.perf_counter()
    from PySide6.QtWidgets import QApplication

    from freesub.config import APP_NAME, settings, ui_language

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("Maditor")
    app.setStyle("Fusion")
    from freesub import i18n
    i18n.install(ui_language())         # before any window is made: the Qt text calls go through it
    splash = _splash(app)                # something on screen at once while the rest loads
    from freesub.ui import theme
    from freesub.ui import wheel
    from freesub.ui.main_window import MainWindow
    wheel.install(app)                  # the wheel scrolls panels; it never changes a setting
    theme.apply(app, settings()["theme"])
    win = MainWindow()
    from freesub.ui.watchdog import Watchdog
    win._watchdog = Watchdog(win)       # a hang writes where it hangs to the log

    def follow_windows(_scheme):
        if settings()["theme"] == "system":
            win.apply_theme()
    try:
        app.styleHints().colorSchemeChanged.connect(follow_windows)
    except Exception:  # noqa: BLE001
        pass
    win.show()
    if splash is not None:
        splash.finish(win)
    logging.getLogger("freesub").info("Window ready in %.1f s", time.perf_counter() - t0)
    if args.paths:
        win.source.add_paths(args.paths)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
