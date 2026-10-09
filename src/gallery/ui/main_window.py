"""Main window: library folders, filter bar, thumbnail grid."""

import sqlite3

from PySide6.QtCore import QByteArray, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox, QDockWidget, QFileDialog, QInputDialog, QLineEdit, QMainWindow, QMenu, QMessageBox, QToolBar,
)

from .. import __version__, albums, db, library, models, renaming, scanner, search, sync
from .album_panel import AlbumPanel
from .duplicates_dialog import DuplicatesDialog
from .grid import MediaGrid, MediaModel
from .rename_dialog import RenameDialog
from .settings_dialog import SettingsDialog
from .sync_controller import SyncController
from .tag_panel import TagPanel
from .viewer import Viewer

FILTERS = [("All", None, False), ("Photos", "photo", False), ("Videos", "video", False), ("Unsorted", None, True)]
SORT_LABELS = [("Newest", "newest"), ("Oldest", "oldest"), ("Name", "name")]


class ScanThread(QThread):
    finished_scan = Signal(object)

    def __init__(self, db_path, roots):
        super().__init__()
        self.db_path, self.roots = db_path, roots

    def run(self):
        conn = db.connect(self.db_path)  # sqlite connections are per-thread
        errors = []
        try:
            for root in self.roots:
                try:
                    scanner.scan_root(conn, root)
                except OSError as e:
                    errors.append(f"{root}: {e}")
            sync.after_scan(conn)  # re-link externally moved files, attach held sync records
        finally:
            conn.close()
        self.finished_scan.emit(errors)


class MainWindow(QMainWindow):
    def __init__(self, conn: sqlite3.Connection, db_path, prompt_first_run: bool = True):
        super().__init__()
        self.conn, self.db_path = conn, db_path
        self.setWindowTitle("Gallery")
        self.resize(1200, 800)

        self.model = MediaModel(self)
        self.grid = MediaGrid()
        self.grid.setModel(self.model)
        self.grid.doubleClicked.connect(self.open_viewer)
        self.setCentralWidget(self.grid)

        self.tag_panel = TagPanel(conn)
        dock = QDockWidget("Tags", self)
        dock.setObjectName("tags-dock")  # object names are required for saveState()
        dock.setWidget(self.tag_panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self.grid.selectionModel().selectionChanged.connect(self.on_selection_changed)
        self.tag_panel.tags_changed.connect(self.on_tags_changed)

        self.album_filter: str | None = None
        self.album_panel = AlbumPanel(conn, self.roots)
        album_dock = QDockWidget("Albums", self)
        album_dock.setObjectName("albums-dock")
        album_dock.setWidget(self.album_panel)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, album_dock)
        self.album_panel.album_selected.connect(self.on_album_selected)
        self.album_panel.albums_changed.connect(self.refresh)
        self.album_panel.album_move_requested.connect(self.move_album)
        self.grid.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self.show_grid_menu)

        bar = QToolBar("Main")
        bar.setObjectName("main-toolbar")  # needed for saveState()
        bar.setMovable(False)
        self.addToolBar(bar)
        add = self._action("Add folder…", self.add_folder, QKeySequence.StandardKey.Open)
        rescan = self._action("Rescan", self.rescan, QKeySequence.StandardKey.Refresh)
        dupes = self._action("Duplicates…", self.open_duplicates, "Ctrl+D")
        rename = self._action("Rename selected files…", self.rename_selected, "F2")
        settings = self._action("Settings…", self.open_settings, "Ctrl+,")
        quit_ = self._action("Quit", self.close, QKeySequence.StandardKey.Quit)
        about = self._action("About Gallery", self.show_about)
        for a in (add, rescan, dupes):
            bar.addAction(a)
        file_menu = self.menuBar().addMenu("&File")
        file_menu.addActions([add, settings])
        file_menu.addSeparator()
        file_menu.addAction(quit_)
        tools_menu = self.menuBar().addMenu("&Tools")
        tools_menu.addActions([rename, rescan, dupes])
        bar.addSeparator()
        self.filter_box = QComboBox()
        self.filter_box.addItems([f[0] for f in FILTERS])
        self.sort_box = QComboBox()
        self.sort_box.addItems([s[0] for s in SORT_LABELS])
        bar.addWidget(self.filter_box)
        bar.addWidget(self.sort_box)
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText('Search:  #beach  #sun|sea  -#nsfw  type:video  after:2024-01-01  filename')
        self.search_box.setClearButtonEnabled(True)
        self.search_box.setMinimumWidth(420)
        self.search_box.setToolTip(search.__doc__)
        self.search_box.returnPressed.connect(self.refresh)
        self.search_box.textChanged.connect(lambda t: self.refresh() if not t else None)
        bar.addSeparator()
        bar.addWidget(self.search_box)
        self.filter_box.currentIndexChanged.connect(self.refresh)
        self.sort_box.currentIndexChanged.connect(self.refresh)

        self.scan_thread: ScanThread | None = None
        self._startup_sync_done = False
        self.sync = SyncController(self)
        self.menuBar().addMenu("&Help").addAction(about)
        self.tag_panel.tags_changed.connect(self.sync.schedule_export)
        self.album_panel.albums_changed.connect(self.sync.schedule_export)

        # Keyboard shortcuts for the two things done most: searching and tagging.
        QShortcut(QKeySequence.StandardKey.Find, self, activated=self._focus_search)
        QShortcut(QKeySequence("Ctrl+T"), self, activated=self.tag_panel.input.setFocus)

        self._restore_layout()
        self.refresh()
        self.rescan()
        if prompt_first_run and not self.roots():  # nothing to show until a folder is chosen
            QTimer.singleShot(0, self._first_run)

    def _action(self, text: str, slot, shortcut=None) -> QAction:
        action = QAction(text, self)
        action.triggered.connect(lambda _checked=False: slot())
        if shortcut is not None:
            action.setShortcut(QKeySequence(shortcut))
        return action

    def _focus_search(self):
        self.search_box.setFocus()
        self.search_box.selectAll()

    def _first_run(self):
        self.statusBar().showMessage("Choose your photo folder to open the library (toolbar: Add folder…).")
        self.add_folder()

    # -- window layout ----------------------------------------------------
    def _restore_layout(self):
        try:
            geometry = models.get_setting(self.conn, "window_geometry")
            state = models.get_setting(self.conn, "window_state")
            if geometry:
                self.restoreGeometry(QByteArray.fromHex(geometry.encode()))
            if state:
                self.restoreState(QByteArray.fromHex(state.encode()))
        except Exception:
            pass  # a bad saved layout must never stop the app from starting

    def _save_layout(self):
        models.set_setting(self.conn, "window_geometry", bytes(self.saveGeometry().toHex()).decode())
        models.set_setting(self.conn, "window_state", bytes(self.saveState().toHex()).decode())

    # -- library roots ----------------------------------------------------
    def roots(self) -> list[str]:
        return library.get_roots(self.conn)

    def add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose a photo folder")
        if not folder:
            return
        try:
            library.add_root(self.conn, folder)
        except library.LibraryError as e:
            QMessageBox.warning(self, "Cannot add folder", str(e))
            return
        self.rescan()

    def open_settings(self):
        if self.scan_thread and self.scan_thread.isRunning():
            QMessageBox.information(self, "Scan in progress", "Wait for the scan to finish, then open Settings.")
            return
        SettingsDialog(self.conn, self.db_path, self).exec()
        self.sync.reload_settings()
        self.album_panel.reload()
        self.tag_panel.set_selection([])
        self.rescan()  # library folders may have changed

    def show_about(self):
        QMessageBox.about(
            self,
            "About Gallery",
            f"<b>Gallery {__version__}</b><br>Tag, organise and search your photos and videos.<br><br>"
            "Everything stays on this computer: the app never connects to the internet. "
            "Tags move between machines only through the sync folder you choose.",
        )

    def rescan(self):
        roots = self.roots()
        if not roots or (self.scan_thread and self.scan_thread.isRunning()):
            return
        self.statusBar().showMessage("Scanning…")
        self.scan_thread = ScanThread(self.db_path, roots)
        self.scan_thread.finished_scan.connect(self._scan_done)
        self.scan_thread.start()

    def open_duplicates(self):
        if self.scan_thread and self.scan_thread.isRunning():
            QMessageBox.information(self, "Scan in progress", "Wait for the scan to finish, then look for duplicates.")
            return
        dialog = DuplicatesDialog(self.conn, self.db_path, self)
        dialog.changed.connect(self.sync.schedule_export)  # merged tags are worth publishing
        dialog.exec()
        self.album_panel.reload()  # files may have been removed: refresh counts and the grid
        self.tag_panel.set_selection([])

    def _scan_done(self, errors):
        self.statusBar().showMessage("Scan complete", 3000)
        if errors:
            QMessageBox.warning(self, "Scan problems", "\n".join(errors))
        self.album_panel.reload()  # pick up new folders; re-emits the selection, which refreshes the grid
        if not self._startup_sync_done:  # once per launch, after the first scan so paths are current
            self._startup_sync_done = True
            self.sync.startup_sync()

    # -- grid -------------------------------------------------------------
    def refresh(self):
        _, kind, unsorted = FILTERS[self.filter_box.currentIndex()]
        sort = SORT_LABELS[self.sort_box.currentIndex()][1]
        try:
            q = search.parse_query(self.search_box.text())
        except ValueError as e:
            self.statusBar().showMessage(str(e), 5000)
            return
        # The dropdown filter narrows the typed query; a type: term in the query wins.
        q.media_type = q.media_type or kind
        q.unsorted = unsorted
        q.album = q.album or self.album_filter
        self.model.set_rows(search.search(self.conn, q, sort))
        self.statusBar().showMessage(f"{self.model.rowCount()} items", 3000)

    def selected_ids(self) -> list[int]:
        return [self.model.row_data(i.row())["id"] for i in self.grid.selectionModel().selectedIndexes()]

    def on_album_selected(self, name):
        self.album_filter = name
        self.refresh()

    def show_grid_menu(self, pos):
        ids = self.selected_ids()
        if not ids:
            return
        rows = [self.model.row_data(i.row()) for i in self.grid.selectionModel().selectedIndexes()]
        roots = {r["root"] for r in rows}
        menu = QMenu(self)
        menu.addAction("Rename…", lambda _checked=False: self.rename_selected())
        menu.addSeparator()
        if len(roots) == 1:
            root = roots.pop()
            move_menu = menu.addMenu("Move to album")
            for a in albums.list_albums(self.conn):
                if a.root == root:
                    # `_checked` absorbs the bool that QAction.triggered passes to the callback
                    move_menu.addAction(a.path, lambda _checked=False, p=a.path: self.move_to_album(ids, root, p))
            move_menu.addSeparator()
            move_menu.addAction("New album…", lambda _checked=False: self.move_to_new_album(ids, root))
            if any(r["album"] for r in rows):
                menu.addAction(
                    "Move out of album (to library root)",
                    lambda _checked=False: self.move_to_album(ids, root, ""),
                )
        else:
            menu.addAction("Selection spans several library folders").setEnabled(False)
        menu.exec(self.grid.viewport().mapToGlobal(pos))

    def move_to_album(self, ids: list[int], root: str, album_path: str):
        """Albums are folders: this moves the files on disk."""
        if self.scan_thread and self.scan_thread.isRunning():
            QMessageBox.information(self, "Scan in progress", "Wait for the scan to finish, then try again.")
            return
        if len(ids) > 10:
            where = f"“{album_path}”" if album_path else "the library root"
            answer = QMessageBox.question(self, "Move files", f"Move {len(ids)} files into {where}? They are moved on disk.")
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            result = albums.move_to_album(self.conn, ids, root, album_path)
        except albums.AlbumError as e:
            QMessageBox.warning(self, "Cannot move files", str(e))
            return
        self.album_panel.reload()  # re-emits the selection, which refreshes the grid
        self.tag_panel.set_selection([])
        self.statusBar().showMessage(f"Moved {result.moved} file(s)", 5000)
        if result.moved:
            self.sync.schedule_export()
        if result.skipped:
            lines = [f"{name}: {why}" for name, why in result.skipped[:15]]
            if len(result.skipped) > 15:
                lines.append(f"…and {len(result.skipped) - 15} more")
            QMessageBox.warning(self, f"{len(result.skipped)} file(s) not moved", "\n".join(lines))

    def _scan_running(self) -> bool:
        return bool(self.scan_thread and self.scan_thread.isRunning())

    def rename_selected(self):
        """Mass rename: files become <name>1, <name>2, ... in the order shown in the grid."""
        rows = sorted(self.grid.selectionModel().selectedIndexes(), key=lambda i: i.row())
        ids = [self.model.row_data(i.row())["id"] for i in rows]
        if not ids:
            self.statusBar().showMessage("Select the files you want to rename first.", 4000)
            return
        if self._scan_running():
            QMessageBox.information(self, "Scan in progress", "Wait for the scan to finish, then rename.")
            return
        dialog = RenameDialog(self.conn, ids, self)
        if dialog.exec() != RenameDialog.DialogCode.Accepted:
            return
        try:
            count = renaming.apply_plan(self.conn, dialog.final_plan())
        except renaming.RenameError as e:
            QMessageBox.warning(self, "Nothing was renamed", str(e))
            return
        self.album_panel.reload()  # re-emits the selection, which refreshes the grid
        self.tag_panel.set_selection([])
        self.statusBar().showMessage(f"Renamed {count} file(s)", 5000)
        if count:
            self.sync.schedule_export()

    def move_album(self, album_id: int, dest_id):
        """An album was dragged onto another one (or onto "All media" for the top level)."""
        if self._scan_running():
            QMessageBox.information(self, "Scan in progress", "Wait for the scan to finish, then move the album.")
            return
        try:
            new_path = albums.move_album(self.conn, album_id, dest_id)
        except albums.AlbumError as e:
            QMessageBox.warning(self, "Cannot move album", str(e))
            return
        self.album_panel.reload(select=new_path)
        self.tag_panel.set_selection([])
        self.statusBar().showMessage(f"Moved album to “{new_path}”", 5000)
        self.sync.schedule_export()

    def move_to_new_album(self, ids: list[int], root: str):
        name, ok = QInputDialog.getText(self, "New album", "Folder name (use / for a nested album):")
        if not ok:
            return
        try:
            album = albums.create_album(self.conn, root, name)
        except albums.AlbumError as e:
            QMessageBox.warning(self, "Cannot create album", str(e))
            return
        self.move_to_album(ids, root, album.path)

    def on_selection_changed(self, *_):
        self.tag_panel.set_selection(self.selected_ids())

    def on_tags_changed(self):
        # In the Unsorted view, tagging removes items from the list.
        if FILTERS[self.filter_box.currentIndex()][2]:
            self.refresh()
            self.tag_panel.set_selection([])

    def open_viewer(self, index):
        rows = [self.model.row_data(i) for i in range(self.model.rowCount())]
        items = [(self.model.full_path(r), r["type"]) for r in rows]
        Viewer(items, index.row(), self).exec()

    def closeEvent(self, e):
        if self.scan_thread and self.scan_thread.isRunning():
            self.scan_thread.wait(5000)
        self.sync.flush()
        self._save_layout()
        super().closeEvent(e)
