"""Left-hand album tree. Albums are real folders: creating one makes a directory."""

import json
import sqlite3
from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QInputDialog, QMenu, QMessageBox, QPushButton, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from .. import albums, models

PATH_ROLE = Qt.ItemDataRole.UserRole  # album path; None for the "All media" row
ID_ROLE = Qt.ItemDataRole.UserRole + 1
EXPANDED_KEY = "album_tree_expanded"  # setting: JSON list of album ids whose subfolders are shown


class _AlbumTree(QTreeWidget):
    """Albums can be dragged onto another album. Qt's own drop handling would reorder the rows, so the
    drop is turned into a signal and the real work (moving the folder) happens elsewhere."""

    album_dropped = Signal(int, object)  # dragged album id, target album id (None = "All media" = top level)

    def __init__(self):
        super().__init__()
        self.setHeaderHidden(True)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)

    def dropEvent(self, event):
        target = self.itemAt(event.position().toPoint())
        dragged = self.selectedItems()
        event.ignore()  # never let Qt reorder or copy rows
        if event.source() is self:
            self.emit_drop(target, dragged)

    def emit_drop(self, target, dragged) -> None:
        """Turn "dragged items were dropped on target" into a move request (ignoring pointless drops)."""
        if target is None or not dragged:
            return
        source_id = dragged[0].data(0, ID_ROLE)
        target_id = target.data(0, ID_ROLE)
        if source_id is not None and source_id != target_id:
            self.album_dropped.emit(source_id, target_id)


class AlbumPanel(QWidget):
    album_selected = Signal(object)  # album path, or None for everything
    albums_changed = Signal()
    album_move_requested = Signal(int, object)  # album id, destination album id (None = top level)

    def __init__(self, conn: sqlite3.Connection, get_roots: Callable[[], list[str]], parent=None):
        super().__init__(parent)
        self.conn, self.get_roots = conn, get_roots
        self.list = _AlbumTree()
        self.list.setToolTip(
            "Drag an album onto another album to move it inside.\nDrop it on “All media” to move it to the top level.\n"
            "Click the arrow (or press ←/→) to collapse or expand an album's subfolders."
        )
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        try:
            self._expanded = set(json.loads(models.get_setting(conn, EXPANDED_KEY, "[]")))
        except (ValueError, TypeError):
            self._expanded = set()
        new, rename, move, delete = QPushButton("New"), QPushButton("Rename"), QPushButton("Move…"), QPushButton("Delete")
        move.setToolTip("Move the selected album into another album (or drag and drop it).")
        delete.setToolTip("Deletes an empty album folder. Albums that contain files are refused.")
        row = QHBoxLayout()
        for b in (new, rename, move, delete):
            row.addWidget(b)
        lay = QVBoxLayout(self)
        lay.addWidget(self.list, 1)
        lay.addLayout(row)

        new.clicked.connect(self.new_album)
        rename.clicked.connect(self.rename_album)
        move.clicked.connect(self.move_album)
        delete.clicked.connect(self.delete_album)
        self.list.album_dropped.connect(self.album_move_requested)
        self.list.currentItemChanged.connect(lambda cur, _prev: cur and self.album_selected.emit(cur.data(0, PATH_ROLE)))
        self.list.itemExpanded.connect(lambda item: self._set_expanded([item], True))
        self.list.itemCollapsed.connect(self._on_collapsed)
        self.list.customContextMenuRequested.connect(self._context_menu)
        self.reload()

    def reload(self, select: str | None = None) -> None:
        current = select if select is not None else self.current_path()
        self.list.blockSignals(True)  # also silences itemExpanded, so rebuilding does not rewrite the setting
        self.list.clear()
        all_item = QTreeWidgetItem(["All media"])
        all_item.setData(0, PATH_ROLE, None)
        all_item.setFlags(all_item.flags() & ~Qt.ItemFlag.ItemIsDragEnabled)  # a drop target only
        self.list.addTopLevelItem(all_item)
        target = all_item
        by_path: dict[tuple[str, str], QTreeWidgetItem] = {}  # parents sort before their subfolders
        for a in albums.list_albums(self.conn):
            item = QTreeWidgetItem([f"{a.name}  ({a.count})"])
            item.setToolTip(0, a.path)
            item.setData(0, PATH_ROLE, a.path)
            item.setData(0, ID_ROLE, a.id)
            parent = by_path.get((a.root, a.path.rsplit("/", 1)[0].lower())) if "/" in a.path else None
            if parent is not None:
                parent.addChild(item)
            else:
                self.list.addTopLevelItem(item)
            by_path[(a.root, a.path.lower())] = item
            if current and a.path.lower() == current.lower():
                target = item
        for item in by_path.values():
            item.setExpanded(item.data(0, ID_ROLE) in self._expanded)
        self.list.setCurrentItem(target)
        self.list.blockSignals(False)
        self._reveal(target)
        self.album_selected.emit(target.data(0, PATH_ROLE))

    def _reveal(self, item: QTreeWidgetItem) -> None:
        """Expand the parents of a selected nested album so it is visible."""
        parents = []
        while (item := item.parent()) is not None:
            parents.append(item)
        self.list.blockSignals(True)
        for p in parents:
            p.setExpanded(True)
        self.list.blockSignals(False)
        self._set_expanded(parents, True)
        if self.list.currentItem():
            self.list.scrollToItem(self.list.currentItem())

    def _on_collapsed(self, item: QTreeWidgetItem) -> None:
        """Collapsing hides the subfolders; if the selected album was one of them, select the collapsed album."""
        cur = self.list.currentItem()
        while cur is not None and cur is not item:
            cur = cur.parent()
        if cur is item and self.list.currentItem() is not item:
            self.list.setCurrentItem(item)
        self._set_expanded([item], False)

    def _set_expanded(self, items: list[QTreeWidgetItem], expanded: bool) -> None:
        ids = {i.data(0, ID_ROLE) for i in items} - {None}
        before = set(self._expanded)
        if expanded:
            self._expanded |= ids
        else:
            self._expanded -= ids
        if self._expanded != before:
            models.set_setting(self.conn, EXPANDED_KEY, json.dumps(sorted(self._expanded)))

    def _all_items(self) -> list[QTreeWidgetItem]:
        out, stack = [], [self.list.topLevelItem(i) for i in range(self.list.topLevelItemCount())]
        while stack:
            item = stack.pop()
            out.append(item)
            stack.extend(item.child(i) for i in range(item.childCount()))
        return out

    def expand_all(self, expanded: bool = True) -> None:
        items = [i for i in self._all_items() if i.childCount()]
        if not expanded:
            # Collapsing would hide a selected subfolder; select its top-level album instead.
            cur = self.list.currentItem()
            while cur is not None and cur.parent() is not None:
                cur = cur.parent()
            if cur is not None and cur is not self.list.currentItem():
                self.list.setCurrentItem(cur)
        self.list.blockSignals(True)
        for i in items:
            i.setExpanded(expanded)
        self.list.blockSignals(False)
        self._set_expanded(items, expanded)

    def _context_menu(self, pos) -> None:
        menu = QMenu(self)
        menu.addAction("Expand all", lambda: self.expand_all(True))
        menu.addAction("Collapse all", lambda: self.expand_all(False))
        menu.exec(self.list.viewport().mapToGlobal(pos))

    def current_path(self) -> str | None:
        item = self.list.currentItem()
        return item.data(0, PATH_ROLE) if item else None

    def _current_id(self) -> int | None:
        item = self.list.currentItem()
        return item.data(0, ID_ROLE) if item else None

    def pick_root(self) -> str | None:
        roots = self.get_roots()
        if not roots:
            QMessageBox.information(self, "No library folder", "Add a library folder first (toolbar: Add folder…).")
            return None
        if len(roots) == 1:
            return roots[0]
        root, ok = QInputDialog.getItem(self, "Library folder", "Create the album in:", roots, 0, False)
        return root if ok else None

    def new_album(self):
        root = self.pick_root()
        if root is None:
            return
        name, ok = QInputDialog.getText(self, "New album", "Folder name (use / for a nested album):")
        if not ok:
            return
        try:
            album = albums.create_album(self.conn, root, name)
        except albums.AlbumError as e:
            QMessageBox.warning(self, "Cannot create album", str(e))
            return
        self.reload(select=album.path)
        self.albums_changed.emit()

    def rename_album(self):
        album_id, path = self._current_id(), self.current_path()
        if album_id is None:
            return
        name, ok = QInputDialog.getText(self, "Rename album", "New folder name:", text=path.rsplit("/", 1)[-1])
        if not ok:
            return
        try:
            new_path = albums.rename_album(self.conn, album_id, name)
        except albums.AlbumError as e:
            QMessageBox.warning(self, "Cannot rename album", str(e))
            return
        self.reload(select=new_path)
        self.albums_changed.emit()

    def move_album(self):
        """Pick a destination from a list (the keyboard/no-drag alternative to dragging)."""
        album_id, path = self._current_id(), self.current_path()
        if album_id is None:
            return
        root = self.conn.execute("SELECT root FROM folders WHERE id = ?", (album_id,)).fetchone()["root"]
        top = "(top level of the library folder)"
        choices = {top: None}
        for a in albums.list_albums(self.conn):
            inside_itself = a.path.lower() == path.lower() or a.path.lower().startswith(path.lower() + "/")
            if a.root == root and not inside_itself:
                choices[a.path] = a.id
        label, ok = QInputDialog.getItem(self, "Move album", f"Move “{path}” into:", list(choices), 0, False)
        if ok:
            self.album_move_requested.emit(album_id, choices[label])

    def delete_album(self):
        album_id, path = self._current_id(), self.current_path()
        if album_id is None:
            return
        ok = QMessageBox.question(self, "Delete album folder", f"Delete the empty folder “{path}”?")
        if ok != QMessageBox.StandardButton.Yes:
            return
        try:
            albums.delete_album(self.conn, album_id)
        except albums.AlbumError as e:
            QMessageBox.warning(self, "Cannot delete album", str(e))
            return
        self.reload(select="")
        self.albums_changed.emit()
