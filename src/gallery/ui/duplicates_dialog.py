"""Review dialog for duplicate and similar files. Nothing happens without the user choosing which copy to keep."""

import sqlite3
from datetime import datetime
from pathlib import Path

import send2trash
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QProgressBar, QPushButton, QSpinBox, QSplitter, QVBoxLayout, QWidget,
)

from .. import db, duplicates
from .grid import MediaGrid, MediaModel
from .viewer import Viewer


def human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


class IndexThread(QThread):
    progress = Signal(int, int, str)

    def __init__(self, db_path, similar: bool):
        super().__init__()
        self.db_path, self.similar = db_path, similar
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        conn = db.connect(self.db_path)  # sqlite connections are per-thread
        try:
            emit = lambda i, n, name: self.progress.emit(i, n, name)
            duplicates.index_exact(conn, emit, lambda: self._cancel)
            if self.similar and not self._cancel:
                duplicates.index_signatures(conn, emit, lambda: self._cancel)
        finally:
            conn.close()


class DuplicatesDialog(QDialog):
    changed = Signal()  # files were removed or moved: the main window should refresh

    def __init__(self, conn: sqlite3.Connection, db_path, parent=None, trash=send2trash.send2trash):
        super().__init__(parent)
        self.conn, self.db_path, self.trash = conn, db_path, trash
        self.groups: list[duplicates.Group] = []
        self.thread: IndexThread | None = None
        self.setWindowTitle("Find duplicates")
        self.resize(1100, 700)

        self.mode = QComboBox()
        self.mode.addItems(["Exact duplicates only", "Exact and similar"])
        self.mode.setCurrentIndex(1)
        self.distance = QSpinBox()
        self.distance.setRange(0, duplicates.MAX_DISTANCE)
        self.distance.setValue(duplicates.DEFAULT_DISTANCE)
        self.distance.setToolTip("How different two pictures may look and still be grouped. 0 = nearly identical.")
        self.scan_btn = QPushButton("Find duplicates")
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.hide()
        self.progress = QProgressBar()
        self.progress.hide()
        self.status = QLabel("Fingerprints are computed once and cached, so later runs are fast.")

        top = QHBoxLayout()
        for w in (QLabel("Compare:"), self.mode, QLabel("Looseness:"), self.distance, self.scan_btn, self.cancel_btn):
            top.addWidget(w)
        top.addStretch(1)

        self.group_list = QListWidget()
        self.model = MediaModel(self)
        self.grid = MediaGrid()
        self.grid.setModel(self.model)
        self.grid.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.details = QLabel("")
        self.details.setWordWrap(True)
        self.keep_btn = QPushButton("Keep selected file, recycle the others")
        self.ignore_btn = QPushButton("These are not duplicates")
        buttons = QHBoxLayout()
        buttons.addWidget(self.keep_btn)
        buttons.addWidget(self.ignore_btn)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(QLabel("Select the copy to keep (the largest is preselected). Double-click to view."))
        rl.addWidget(self.grid, 1)
        rl.addWidget(self.details)
        rl.addLayout(buttons)
        split = QSplitter()
        split.addWidget(self.group_list)
        split.addWidget(right)
        split.setStretchFactor(1, 1)

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.progress)
        lay.addWidget(split, 1)
        lay.addWidget(self.status)

        self.mode.currentIndexChanged.connect(lambda _i: self.distance.setEnabled(self.mode.currentIndex() == 1))
        self.scan_btn.clicked.connect(self.scan)
        self.cancel_btn.clicked.connect(self.cancel)
        self.group_list.currentRowChanged.connect(self.show_group)
        self.grid.selectionModel().selectionChanged.connect(self.update_details)
        self.grid.doubleClicked.connect(self.open_viewer)
        self.keep_btn.clicked.connect(self.keep_selected)
        self.ignore_btn.clicked.connect(self.ignore_current)
        self._set_actions_enabled(False)

    # -- scanning -----------------------------------------------------------
    def similar(self) -> bool:
        return self.mode.currentIndex() == 1

    def scan(self):
        if self.thread and self.thread.isRunning():
            return
        self.scan_btn.setEnabled(False)
        self.cancel_btn.show()
        self.progress.show()
        self.progress.setRange(0, 0)
        self.status.setText("Reading files…")
        self.thread = IndexThread(self.db_path, self.similar())
        self.thread.progress.connect(self._on_progress)
        self.thread.finished.connect(self._scan_finished)
        self.thread.start()

    def cancel(self):
        if self.thread:
            self.thread.cancel()

    def _on_progress(self, i: int, n: int, name: str):
        self.progress.setRange(0, max(n, 1))
        self.progress.setValue(i)
        if name:
            self.status.setText(f"Fingerprinting {i + 1} of {n}: {name}")

    def _scan_finished(self):
        self.cancel_btn.hide()
        self.progress.hide()
        self.scan_btn.setEnabled(True)
        self.load_groups()

    def done(self, r):
        if self.thread and self.thread.isRunning():
            self.thread.cancel()
            self.thread.wait(10000)
        super().done(r)

    # -- groups -------------------------------------------------------------
    def load_groups(self, keep_row: int = 0):
        self.groups = duplicates.find_groups(self.conn, self.similar(), self.distance.value())
        self.group_list.blockSignals(True)
        self.group_list.clear()
        for n, g in enumerate(self.groups, 1):
            label = "identical" if g.kind == "exact" else "similar"
            self.group_list.addItem(QListWidgetItem(f"Group {n}: {len(g.items)} files, {label}"))
        self.group_list.blockSignals(False)
        total = sum(len(g.items) - 1 for g in self.groups)
        self.status.setText(
            f"{len(self.groups)} group(s); {total} extra file(s)." if self.groups else "No duplicates found."
        )
        if self.groups:
            self.group_list.setCurrentRow(min(keep_row, len(self.groups) - 1))
        else:
            self.model.set_rows([])
            self.details.setText("")
        self._set_actions_enabled(bool(self.groups))

    def current_group(self) -> duplicates.Group | None:
        row = self.group_list.currentRow()
        return self.groups[row] if 0 <= row < len(self.groups) else None

    def show_group(self, _row: int):
        g = self.current_group()
        self.model.set_rows(g.items if g else [])
        if g:
            self.grid.setCurrentIndex(self.model.index(0))  # items are sorted largest first

    def update_details(self, *_):
        idx = self.grid.currentIndex()
        g = self.current_group()
        if not (idx.isValid() and g):
            self.details.setText("")
            return
        r = self.model.row_data(idx.row())
        when = datetime.fromtimestamp(r["mtime"]).strftime("%Y-%m-%d %H:%M")
        note = "" if g.kind == "exact" else "  (similar, not byte-identical: check before removing)"
        self.details.setText(f"{r['rel_path']}\n{human_size(r['size'])}, modified {when}{note}")

    def open_viewer(self, index):
        g = self.current_group()
        if g:
            items = [(Path(r["root"]) / r["rel_path"], r["type"]) for r in g.items]
            Viewer(items, index.row(), self).exec()

    def _set_actions_enabled(self, on: bool):
        self.keep_btn.setEnabled(on)
        self.ignore_btn.setEnabled(on)

    # -- actions ------------------------------------------------------------
    def keep_selected(self):
        g, idx = self.current_group(), self.grid.currentIndex()
        if not (g and idx.isValid()):
            return
        keep = self.model.row_data(idx.row())
        others = [i for i in g.ids if i != keep["id"]]
        answer = QMessageBox.question(
            self,
            "Remove duplicates",
            f"Keep “{keep['rel_path']}” and move {len(others)} other file(s) to the Recycle Bin / Trash?\n\n"
            "Their tags are added to the file you keep. You can restore the files from the Recycle Bin.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        result = duplicates.keep_one(self.conn, keep["id"], others, g.kind, trash=self.trash)
        row = self.group_list.currentRow()
        self.load_groups(keep_row=row)
        self.changed.emit()
        if result.skipped:
            lines = [f"{name}: {why}" for name, why in result.skipped[:15]]
            QMessageBox.warning(self, f"{len(result.skipped)} file(s) not removed", "\n".join(lines))

    def ignore_current(self):
        g = self.current_group()
        if g:
            duplicates.ignore_group(self.conn, g.ids)
            self.load_groups(keep_row=self.group_list.currentRow())
