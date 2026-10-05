"""Full-size viewer for photos (zoom/pan) and videos (playback controls)."""

from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QKeyEvent, QPixmap, QWheelEvent
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QDialog, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel,
    QPushButton, QSlider, QStackedWidget, QVBoxLayout, QWidget,
)

from .. import thumbnails  # noqa: F401  (registers HEIC support with Pillow)


class _ImageView(QGraphicsView):
    def __init__(self):
        super().__init__()
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._item: QGraphicsPixmapItem | None = None
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)

    def show_image(self, path: Path) -> bool:
        pix = _load_pixmap(path)
        self._scene.clear()
        self._item = None
        if pix.isNull():
            return False
        self._item = self._scene.addPixmap(pix)
        self._scene.setSceneRect(self._item.boundingRect())
        self.resetTransform()
        self.fitInView(self._item, Qt.AspectRatioMode.KeepAspectRatio)
        return True

    def wheelEvent(self, e: QWheelEvent):
        factor = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._item is not None and self.transform().isIdentity():
            self.fitInView(self._item, Qt.AspectRatioMode.KeepAspectRatio)


def _load_pixmap(path: Path) -> QPixmap:
    pix = QPixmap(str(path))
    if not pix.isNull():
        return pix
    # Qt can't read it (e.g. HEIC): decode with Pillow instead.
    try:
        from PIL import Image, ImageOps
        from PySide6.QtGui import QImage

        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im).convert("RGBA")
            img = QImage(im.tobytes("raw", "RGBA"), im.width, im.height, QImage.Format.Format_RGBA8888)
            return QPixmap.fromImage(img.copy())
    except Exception:
        return QPixmap()


class _VideoView(QWidget):
    def __init__(self):
        super().__init__()
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.video = QVideoWidget()
        self.player.setVideoOutput(self.video)
        self.play_btn = QPushButton("Pause")
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.error = QLabel()
        self.error.setStyleSheet("color: #e66;")

        controls = QHBoxLayout()
        controls.addWidget(self.play_btn)
        controls.addWidget(self.slider, 1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.video, 1)
        lay.addWidget(self.error)
        lay.addLayout(controls)

        self.play_btn.clicked.connect(self.toggle)
        self.slider.sliderMoved.connect(self.player.setPosition)
        self.player.durationChanged.connect(lambda d: self.slider.setRange(0, d))
        self.player.positionChanged.connect(lambda p: None if self.slider.isSliderDown() else self.slider.setValue(p))
        self.player.errorOccurred.connect(lambda _e, msg: self.error.setText(f"Cannot play video: {msg}"))

    def play(self, path: Path):
        self.error.clear()
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.player.play()
        self.play_btn.setText("Pause")

    def stop(self):
        self.player.stop()
        self.player.setSource(QUrl())

    def toggle(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.play_btn.setText("Play")
        else:
            self.player.play()
            self.play_btn.setText("Pause")


class Viewer(QDialog):
    """Browse a list of (path, type) with Left/Right; Esc closes."""

    index_changed = Signal(int)

    def __init__(self, items: list[tuple[Path, str]], start: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Viewer")
        self.resize(1100, 750)
        self.items = items
        self.index = start
        self.image_view = _ImageView()
        self.video_view = _VideoView()
        self.stack = QStackedWidget()
        self.stack.addWidget(self.image_view)
        self.stack.addWidget(self.video_view)
        self.message = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.stack, 1)
        lay.addWidget(self.message)
        self._show()

    def _show(self):
        path, kind = self.items[self.index]
        self.video_view.stop()
        self.setWindowTitle(f"{path.name}  ({self.index + 1}/{len(self.items)})")
        self.message.clear()
        if kind == "video":
            self.stack.setCurrentWidget(self.video_view)
            self.video_view.play(path)
        else:
            self.stack.setCurrentWidget(self.image_view)
            if not self.image_view.show_image(path):
                self.message.setText(f"Cannot open {path.name}")
        self.index_changed.emit(self.index)

    def step(self, delta: int):
        n = self.index + delta
        if 0 <= n < len(self.items):
            self.index = n
            self._show()

    def keyPressEvent(self, e: QKeyEvent):
        key = e.key()
        if key == Qt.Key.Key_Right:
            self.step(1)
        elif key == Qt.Key.Key_Left:
            self.step(-1)
        elif key == Qt.Key.Key_Space and self.stack.currentWidget() is self.video_view:
            self.video_view.toggle()
        else:
            super().keyPressEvent(e)

    def done(self, r):
        self.video_view.stop()
        super().done(r)
