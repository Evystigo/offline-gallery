import logging
import logging.handlers
import os
import sys
import tempfile
import traceback

from . import __version__


def _setup_logging() -> logging.Logger:
    """Log to a local file only. A packaged (windowed) app has no console to show errors in."""
    from . import db

    log = logging.getLogger("gallery")
    log.setLevel(logging.INFO)
    try:
        handler = logging.handlers.RotatingFileHandler(
            db.app_data_dir() / "gallery.log", maxBytes=500_000, backupCount=2, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(handler)
    except OSError:
        pass
    return log


def _install_excepthook(log: logging.Logger) -> None:
    def hook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        log.error("Unhandled exception:\n%s", text)
        sys.__excepthook__(exc_type, exc, tb)
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox

            if QApplication.instance():
                QMessageBox.critical(None, "Gallery hit a problem", f"{exc}\n\nDetails were written to gallery.log in the app data folder.")
        except Exception:
            pass

    sys.excepthook = hook


def selftest(out_file: str | None = None) -> int:
    """Check that everything a packaged build needs is present. Touches no real user data.
    A windowed .exe has no console, so the report can also be written to `out_file`."""
    import io

    lines: list[str] = []

    def report(text: str) -> None:
        lines.append(text)
        print(text)
        if out_file:
            with open(out_file, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")

    tmp = tempfile.mkdtemp(prefix="gallery-selftest-")
    os.environ["APPDATA"] = os.environ["XDG_DATA_HOME"] = tmp
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PIL import Image
    from PySide6.QtMultimedia import QMediaPlayer  # noqa: F401  (video playback backend)
    from PySide6.QtWidgets import QApplication

    import send2trash  # noqa: F401

    from . import db, hashing, thumbnails
    from .ui.main_window import MainWindow

    buf = io.BytesIO()  # HEIC encode + decode proves pillow-heif's native library is bundled
    Image.new("RGB", (32, 32), "red").save(buf, format="HEIF")
    buf.seek(0)
    assert Image.open(buf).size == (32, 32), "HEIC round trip failed"

    app = QApplication([])
    path = db.app_data_dir() / "selftest.db"
    window = MainWindow(db.connect(path), path, prompt_first_run=False)  # a dialog would block a headless check
    window.show()
    app.processEvents()
    window.close()
    report(f"selftest OK  gallery {__version__}  python {sys.version.split()[0]}")
    report(f"ffmpeg: {thumbnails.find_ffmpeg() or 'not found'}  ffprobe: {hashing.find_ffprobe() or 'not found'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    if "--version" in argv:
        print(f"Gallery {__version__}")
        return 0
    if "--selftest" in argv:
        i = argv.index("--selftest")
        return selftest(argv[i + 1] if i + 1 < len(argv) else None)

    from PySide6.QtWidgets import QApplication

    from . import db, library
    from .ui.main_window import MainWindow

    log = _setup_logging()
    _install_excepthook(log)
    app = QApplication(argv)
    app.setApplicationName("Gallery")
    app.setApplicationVersion(__version__)
    db_path = db.app_data_dir() / "gallery.db"
    conn = db.connect(db_path)
    library.forget_roots(conn)  # the media folder is chosen again every time the app opens
    window = MainWindow(conn, db_path)
    window.show()
    log.info("Gallery %s started", __version__)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
