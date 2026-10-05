"""Settings: library folders, sync folder, thumbnail cache, and which helper tools were found."""

import sqlite3
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QListWidget,
    QMessageBox, QPushButton, QVBoxLayout,
)

from .. import db, hashing, library, models, thumbnails
from .duplicates_dialog import human_size


def cache_stats(directory: Path) -> tuple[int, int]:
    """(file count, total bytes) of the thumbnail cache."""
    files = [p for p in directory.glob("*") if p.is_file()]
    return len(files), sum(p.stat().st_size for p in files)


def clear_cache(directory: Path) -> int:
    """Delete cached thumbnails (they are regenerated on demand). Returns how many were removed."""
    removed = 0
    for p in directory.glob("*.jpg"):  # only files this app created
        try:
            p.unlink()
            removed += 1
        except OSError:
            pass
    return removed


class SettingsDialog(QDialog):
    def __init__(self, conn: sqlite3.Connection, db_path, parent=None):
        super().__init__(parent)
        self.conn, self.db_path = conn, db_path
        self.setWindowTitle("Settings")
        self.resize(560, 560)

        # Library folders
        self.roots = QListWidget()
        add, remove = QPushButton("Add folder…"), QPushButton("Remove")
        remove.setToolTip("Stops using the folder. Your files are not touched and tags are kept.")
        add.clicked.connect(self.add_root)
        remove.clicked.connect(self.remove_root)
        row = QHBoxLayout()
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch(1)
        lib_box = QGroupBox("Library folders")
        lib_lay = QVBoxLayout(lib_box)
        lib_lay.addWidget(self.roots)
        lib_lay.addLayout(row)

        # Sync
        self.sync_label = QLabel()
        self.sync_label.setWordWrap(True)
        choose, use_lib, clear = QPushButton("Choose…"), QPushButton("Use .gallery-sync in library"), QPushButton("Turn off")
        choose.clicked.connect(self.choose_sync)
        use_lib.clicked.connect(self.use_library_sync)
        clear.clicked.connect(self.clear_sync)
        self.apply_moves = QCheckBox("Apply album moves made on other machines")
        self.apply_moves.setChecked(models.get_setting(conn, "apply_moves", "1") == "1")
        self.apply_moves.toggled.connect(lambda on: models.set_setting(conn, "apply_moves", "1" if on else "0"))
        srow = QHBoxLayout()
        for b in (choose, use_lib, clear):
            srow.addWidget(b)
        sync_box = QGroupBox("Sync between machines")
        sync_lay = QVBoxLayout(sync_box)
        sync_lay.addWidget(self.sync_label)
        sync_lay.addLayout(srow)
        sync_lay.addWidget(self.apply_moves)

        # Thumbnails
        self.cache_label = QLabel()
        clear_btn = QPushButton("Clear thumbnail cache")
        clear_btn.clicked.connect(self.clear_thumbnails)
        cache_box = QGroupBox("Thumbnails")
        cache_lay = QHBoxLayout(cache_box)
        cache_lay.addWidget(self.cache_label, 1)
        cache_lay.addWidget(clear_btn)

        # About this install
        info = QFormLayout()
        info.addRow("Data folder:", self._selectable(str(Path(db_path).parent)))
        info.addRow("ffmpeg:", self._selectable(thumbnails.find_ffmpeg() or "not found: video thumbnails and video duplicate detection are unavailable"))
        info.addRow("ffprobe:", self._selectable(hashing.find_ffprobe() or "not found: video duplicate detection is unavailable"))
        tools_box = QGroupBox("This installation")
        tools_box.setLayout(info)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.accept)
        lay = QVBoxLayout(self)
        for w in (lib_box, sync_box, cache_box, tools_box, buttons):
            lay.addWidget(w)
        self.reload()

    @staticmethod
    def _selectable(text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return label

    def reload(self):
        self.roots.clear()
        self.roots.addItems(library.get_roots(self.conn))
        folder = models.get_setting(self.conn, "sync_folder")
        self.sync_label.setText(f"Sync folder: {folder}" if folder else "Sync is off. Choose a folder that your sync service (e.g. Proton Drive) shares.")
        n, size = cache_stats(thumbnails.cache_dir())
        self.cache_label.setText(f"{n} cached thumbnails, {human_size(size)}")

    # -- library -------------------------------------------------------------
    def add_root(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose a photo folder")
        if not folder:
            return
        try:
            library.add_root(self.conn, folder)
        except library.LibraryError as e:
            QMessageBox.warning(self, "Cannot add folder", str(e))
        self.reload()

    def remove_root(self):
        item = self.roots.currentItem()
        if item is None:
            return
        answer = QMessageBox.question(
            self, "Remove library folder",
            f"Stop using “{item.text()}”?\n\nYour files stay where they are. Its photos disappear from the "
            "gallery, and their tags are kept in case you add the folder again.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            library.remove_root(self.conn, item.text())
            self.reload()

    # -- sync ------------------------------------------------------------------
    def choose_sync(self):
        start = (library.get_roots(self.conn) or [""])[0]
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder that your sync service shares", start)
        if folder:
            models.set_setting(self.conn, "sync_folder", str(Path(folder).resolve()))
            self.reload()

    def use_library_sync(self):
        roots = library.get_roots(self.conn)
        if not roots:
            QMessageBox.information(self, "No library folder", "Add a library folder first.")
            return
        folder = Path(roots[0]) / ".gallery-sync"
        try:
            folder.mkdir(exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, "Cannot create folder", str(e))
            return
        models.set_setting(self.conn, "sync_folder", str(folder))
        self.reload()

    def clear_sync(self):
        with self.conn:
            self.conn.execute("DELETE FROM settings WHERE key = 'sync_folder'")
        self.reload()

    # -- thumbnails ----------------------------------------------------------------
    def clear_thumbnails(self):
        clear_cache(thumbnails.cache_dir())
        self.reload()
