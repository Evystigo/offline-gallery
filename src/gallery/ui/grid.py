"""Thumbnail grid: a list model that loads thumbnails lazily on a thread pool."""

from pathlib import Path

from PySide6.QtCore import QAbstractListModel, QModelIndex, QObject, QPoint, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap, QPolygon
from PySide6.QtWidgets import QAbstractItemView, QListView

from .. import thumbnails

ROW_ROLE = Qt.ItemDataRole.UserRole


class _Signals(QObject):
    done = Signal(int, str)  # media id, thumbnail path ("" on failure)


class _ThumbJob(QRunnable):
    def __init__(self, media_id: int, source: Path, kind: str, mtime: float, signals: _Signals):
        super().__init__()
        self.media_id, self.source, self.kind, self.mtime, self.signals = media_id, source, kind, mtime, signals

    def run(self):
        path = thumbnails.get_thumbnail(self.source, self.kind, self.mtime)
        self.signals.done.emit(self.media_id, str(path) if path else "")


_placeholders: dict[str, QIcon] = {}


def _placeholder(kind: str) -> QIcon:
    """A plain tile: a play triangle for videos, a blank frame for unreadable photos."""
    if kind not in _placeholders:
        pix = QPixmap(128, 128)
        pix.fill(QColor(60, 60, 64))
        if kind == "video":
            p = QPainter(pix)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setBrush(QColor(230, 230, 230))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPolygon(QPolygon([QPoint(48, 36), QPoint(48, 92), QPoint(96, 64)]))
            p.end()
        _placeholders[kind] = QIcon(pix)
    return _placeholders[kind]


class MediaModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list = []
        self._icons: dict[int, QIcon] = {}
        self._requested: set[int] = set()
        self._row_of: dict[int, int] = {}
        self._signals = _Signals()
        self._signals.done.connect(self._on_thumb)
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(4)

    def set_rows(self, rows) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self._row_of = {r["id"]: i for i, r in enumerate(self._rows)}
        self.endResetModel()

    def row_data(self, row: int):
        return self._rows[row]

    def full_path(self, row) -> Path:
        return Path(row["root"]) / row["rel_path"]

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return row["filename"]
        if role == Qt.ItemDataRole.ToolTipRole:
            return row["rel_path"]
        if role == ROW_ROLE:
            return row["id"]
        if role == Qt.ItemDataRole.DecorationRole:
            icon = self._icons.get(row["id"])
            if icon is None:
                self._request(row)
                return None
            return icon
        return None

    def _request(self, row) -> None:
        if row["id"] in self._requested:
            return
        self._requested.add(row["id"])
        self._pool.start(_ThumbJob(row["id"], self.full_path(row), row["type"], row["mtime"], self._signals))

    def _on_thumb(self, media_id: int, path: str) -> None:
        i = self._row_of.get(media_id)
        image = QImage(path) if path else QImage()
        if not image.isNull():
            self._icons[media_id] = QIcon(QPixmap.fromImage(image))
        else:
            # No thumbnail (e.g. no ffmpeg for a video): show a placeholder, and don't retry every repaint.
            kind = self._rows[i]["type"] if i is not None and i < len(self._rows) else "photo"
            self._icons[media_id] = _placeholder(kind)
        if i is not None:
            idx = self.index(i)
            self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DecorationRole])


class MediaGrid(QListView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setUniformItemSizes(True)
        self.setIconSize(QSize(thumbnails.THUMB_SIZE // 2 + 32, thumbnails.THUMB_SIZE // 2 + 32))
        self.setGridSize(QSize(176, 176))
        self.setSpacing(4)
        self.setWordWrap(True)
        self.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
