"""Right-hand panel for tagging the current selection, plus the tag manager dialog."""

import sqlite3

from PySide6.QtCore import QStringListModel, Qt, Signal
from PySide6.QtWidgets import (
    QCompleter, QDialog, QDialogButtonBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from .. import tags


class TagPanel(QWidget):
    tags_changed = Signal()

    def __init__(self, conn: sqlite3.Connection, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.media_ids: list[int] = []

        self.header = QLabel("No selection")
        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Add tags (comma separated)…")
        self.completer = QCompleter(QStringListModel(self), self)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.input.setCompleter(self.completer)
        self.remove_btn = QPushButton("Remove selected tags")
        manage = QPushButton("Manage all tags…")

        lay = QVBoxLayout(self)
        for w in (self.header, self.list, self.input, self.remove_btn, manage):
            lay.addWidget(w)

        self.input.returnPressed.connect(self.add_from_input)
        self.remove_btn.clicked.connect(self.remove_selected)
        manage.clicked.connect(self.open_manager)
        self.set_selection([])

    def set_selection(self, media_ids: list[int]) -> None:
        self.media_ids = media_ids
        self.input.setEnabled(bool(media_ids))
        self.remove_btn.setEnabled(bool(media_ids))
        self.completer.model().setStringList([n for n, _ in tags.all_tags(self.conn)])
        self.list.clear()
        total = len(media_ids)
        self.header.setText("No selection" if not total else f"{total} item{'s' if total != 1 else ''} selected")
        for name, n in tags.tag_counts_for(self.conn, media_ids).items():
            label = name if total == 1 or n == total else f"{name}  ({n}/{total})"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.list.addItem(item)

    def add_from_input(self):
        names = tags.parse_names(self.input.text())
        if names and self.media_ids:
            tags.add_tags(self.conn, self.media_ids, names)
            self.input.clear()
            self.set_selection(self.media_ids)
            self.tags_changed.emit()

    def remove_selected(self):
        names = [i.data(Qt.ItemDataRole.UserRole) for i in self.list.selectedItems()]
        for name in names:
            tags.remove_tag(self.conn, self.media_ids, name)
        if names:
            self.set_selection(self.media_ids)
            self.tags_changed.emit()

    def open_manager(self):
        TagManager(self.conn, self).exec()
        self.set_selection(self.media_ids)
        self.tags_changed.emit()


class TagManager(QDialog):
    def __init__(self, conn: sqlite3.Connection, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.setWindowTitle("Manage tags")
        self.resize(360, 460)
        self.list = QListWidget()
        rename = QPushButton("Rename / merge…")
        delete = QPushButton("Delete tag")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        row = QHBoxLayout()
        row.addWidget(rename)
        row.addWidget(delete)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Renaming to an existing tag merges the two."))
        lay.addWidget(self.list, 1)
        lay.addLayout(row)
        lay.addWidget(buttons)
        rename.clicked.connect(self.rename)
        delete.clicked.connect(self.delete)
        buttons.rejected.connect(self.accept)
        self.reload()

    def reload(self):
        self.list.clear()
        for name, n in tags.all_tags(self.conn):
            item = QListWidgetItem(f"{name}  ({n})")
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.list.addItem(item)

    def _current(self) -> str | None:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def rename(self):
        old = self._current()
        if not old:
            return
        new, ok = QInputDialog.getText(self, "Rename tag", f"New name for “{old}”:", text=old)
        if ok and tags.normalize(new):
            tags.rename_tag(self.conn, old, new)
            self.reload()

    def delete(self):
        name = self._current()
        if not name:
            return
        ok = QMessageBox.question(self, "Delete tag", f"Remove “{name}” from every item?")
        if ok == QMessageBox.StandardButton.Yes:
            tags.delete_tag(self.conn, name)
            self.reload()
