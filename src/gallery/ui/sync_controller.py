"""Sync menu and behaviour: startup sync, debounced export after edits, manual export/import."""

import sqlite3
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from .. import models, sync

EXPORT_DELAY_MS = 5000


class SyncController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.w = window
        self.timer = QTimer(self, singleShot=True, interval=EXPORT_DELAY_MS)
        self.timer.timeout.connect(self.export_now)

        menu = window.menuBar().addMenu("&Sync")
        self._add(menu, "Sync now", self.sync_now)
        menu.addSeparator()
        self._add(menu, "Use “.gallery-sync” inside the library folder (recommended)", self.use_library_folder)
        self._add(menu, "Choose sync folder…", self.choose_folder)
        self.moves_action = self._add(menu, "Apply album moves from other machines", self.toggle_moves)
        self.moves_action.setCheckable(True)
        self.moves_action.setChecked(self.apply_moves())
        menu.addSeparator()
        self._add(menu, "Export to a folder…", self.manual_export)
        self._add(menu, "Import from file(s)…", self.manual_import)
        self.status_action = menu.addAction("")
        self.status_action.setEnabled(False)
        self._update_status_text()

    def _add(self, menu, text, slot) -> QAction:
        action = QAction(text, self)
        action.triggered.connect(lambda _checked=False: slot())
        menu.addAction(action)
        return action

    # -- settings ---------------------------------------------------------
    def folder(self) -> str | None:
        return models.get_setting(self.w.conn, "sync_folder") or None

    def apply_moves(self) -> bool:
        return models.get_setting(self.w.conn, "apply_moves", "1") == "1"

    def reload_settings(self):
        """Re-read settings that the Settings dialog may have changed."""
        self.moves_action.setChecked(self.apply_moves())
        self._update_status_text()

    def toggle_moves(self):
        models.set_setting(self.w.conn, "apply_moves", "1" if self.moves_action.isChecked() else "0")

    def _set_folder(self, folder: Path):
        models.set_setting(self.w.conn, "sync_folder", str(folder))
        self._update_status_text()
        self.sync_now()

    def _update_status_text(self):
        f = self.folder()
        self.status_action.setText(f"Sync folder: {f}" if f else "Sync folder: not set")

    def choose_folder(self):
        start = (self.w.roots() or [""])[0]
        folder = QFileDialog.getExistingDirectory(self.w, "Choose a folder that your sync service shares", start)
        if folder:
            self._set_folder(Path(folder))

    def use_library_folder(self):
        roots = self.w.roots()
        if not roots:
            QMessageBox.information(self.w, "No library folder", "Add a library folder first.")
            return
        folder = Path(roots[0]) / ".gallery-sync"
        try:
            folder.mkdir(exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self.w, "Cannot create folder", str(e))
            return
        self._set_folder(folder)

    # -- automatic behaviour --------------------------------------------------
    def startup_sync(self):
        if self.folder():
            self.sync_now(auto=True)

    def schedule_export(self):
        """Call after any tag/album edit; the file is written once edits pause."""
        if self.folder():
            self.timer.start()

    def flush(self):
        """Write immediately if an export is pending (used when closing)."""
        if self.timer.isActive():
            self.timer.stop()
            self.export_now()

    def export_now(self):
        folder = self.folder()
        if not folder:
            return
        try:
            sync.write_export(self.w.conn, folder)
        except OSError as e:
            self.w.statusBar().showMessage(f"Could not write sync file: {e}", 8000)

    # -- actions ----------------------------------------------------------------
    def _scan_running(self) -> bool:
        t = self.w.scan_thread
        return bool(t and t.isRunning())

    def sync_now(self, auto: bool = False):
        folder = self.folder()
        if not folder:
            if not auto:
                QMessageBox.information(self.w, "Sync folder not set", "Choose a sync folder from the Sync menu first.")
            return
        if self._scan_running():
            if not auto:
                QMessageBox.information(self.w, "Scan in progress", "Wait for the scan to finish, then sync.")
            return
        self.timer.stop()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            result = sync.sync_now(self.w.conn, folder, self.apply_moves())
        except (OSError, sqlite3.Error) as e:
            QMessageBox.warning(self.w, "Sync failed", str(e))
            return
        finally:
            QApplication.restoreOverrideCursor()
        self._after_import(result, "Sync")

    def manual_export(self):
        folder = QFileDialog.getExistingDirectory(self.w, "Export this machine's tags and moves to…")
        if not folder:
            return
        try:
            path = sync.write_export(self.w.conn, folder)
        except OSError as e:
            QMessageBox.warning(self.w, "Export failed", str(e))
            return
        self.w.statusBar().showMessage(f"Exported to {path}", 8000)

    def manual_import(self):
        names, _ = QFileDialog.getOpenFileNames(self.w, "Import sync file(s)", "", "Gallery sync files (*.json)")
        if not names:
            return
        if self._scan_running():
            QMessageBox.information(self.w, "Scan in progress", "Wait for the scan to finish, then import.")
            return
        conn = self.w.conn
        files, errors = sync.load_files(conn, [Path(n) for n in names])
        if errors:
            QMessageBox.warning(self.w, "Some files were skipped", "\n".join(errors))
        if not files:
            QMessageBox.information(self.w, "Nothing to import", "None of the chosen files came from another machine.")
            return
        preview = sync.import_files(conn, files, self.apply_moves(), dry_run=True)
        answer = QMessageBox.question(
            self.w, "Import", f"This import would apply: {preview.summary()}.\n\nContinue?"
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._after_import(sync.import_files(conn, files, self.apply_moves()), "Import")
            self.schedule_export()

    def _after_import(self, result: sync.ImportResult, label: str):
        self.w.album_panel.reload()  # re-emits the selection, which refreshes the grid
        self.w.tag_panel.set_selection([])
        self.w.statusBar().showMessage(f"{label}: {result.summary()}", 8000)
        problems = result.errors + result.moves_skipped
        if problems:
            lines = problems[:15] + ([f"…and {len(problems) - 15} more"] if len(problems) > 15 else [])
            QMessageBox.warning(self.w, f"{label} finished with notes", "\n".join(lines))
