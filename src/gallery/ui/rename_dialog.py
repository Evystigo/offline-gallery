"""Mass rename: choose a base name, see exactly what every file will be called, then confirm."""

import posixpath
import sqlite3

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QListWidget, QSpinBox, QVBoxLayout,
)

from .. import renaming

PREVIEW_LIMIT = 200


class RenameDialog(QDialog):
    def __init__(self, conn: sqlite3.Connection, media_ids: list[int], parent=None):
        super().__init__(parent)
        self.conn, self.media_ids = conn, media_ids
        n = len(media_ids)
        self.setWindowTitle(f"Rename {n} file{'s' if n != 1 else ''}")
        self.resize(560, 520)

        self.base = QLineEdit()
        self.base.setPlaceholderText("e.g. holiday  →  holiday1.jpg, holiday2.jpg, …")
        self.number = QCheckBox("Add a number after the name")
        self.number.setChecked(True)
        self.number.setEnabled(n == 1)  # several files always need numbers, or their names would clash
        self.start = QSpinBox()
        self.start.setRange(0, 999_999)
        self.start.setValue(1)
        self.digits = QSpinBox()
        self.digits.setRange(1, 6)
        self.digits.setToolTip("Minimum number of digits: 3 gives 001, 002, …")
        form = QFormLayout()
        form.addRow("New name:", self.base)
        form.addRow("", self.number)
        form.addRow("Start at:", self.start)
        form.addRow("Digits:", self.digits)

        self.preview = QListWidget()
        self.message = QLabel("")
        self.message.setWordWrap(True)
        self.message.setStyleSheet("color: #d44;")
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(QLabel("Preview (files keep their extension and stay in their album; numbered in the order shown in the grid):"))
        lay.addWidget(self.preview, 1)
        lay.addWidget(self.message)
        lay.addWidget(self.buttons)

        # Re-plan shortly after typing stops; planning checks the disk for every file.
        self.timer = QTimer(self, singleShot=True, interval=200)
        self.timer.timeout.connect(self.update_preview)
        self.base.textChanged.connect(lambda _t: self.timer.start())
        self.number.toggled.connect(lambda _c: self.timer.start())
        self.start.valueChanged.connect(lambda _v: self.timer.start())
        self.digits.valueChanged.connect(lambda _v: self.timer.start())
        self.plan = renaming.RenamePlan()
        self.update_preview()
        self.base.setFocus()

    def build_plan(self) -> renaming.RenamePlan:
        return renaming.build_plan(
            self.conn, self.media_ids, self.base.text(), self.start.value(), self.digits.value(), self.number.isChecked()
        )

    def update_preview(self):
        self.preview.clear()
        ok_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        if not self.base.text().strip():
            self.plan = renaming.RenamePlan()
            self.message.setText("")
            ok_button.setEnabled(False)
            return
        self.plan = self.build_plan()
        for it in self.plan.items[:PREVIEW_LIMIT]:
            self.preview.addItem(f"{posixpath.basename(it.old_rel)}   →   {posixpath.basename(it.new_rel)}")
        if len(self.plan.items) > PREVIEW_LIMIT:
            self.preview.addItem(f"…and {len(self.plan.items) - PREVIEW_LIMIT} more")
        notes = list(self.plan.problems[:4])
        if self.plan.unchanged:
            notes.append(f"{self.plan.unchanged} file(s) already have that name and are left alone.")
        self.message.setText("\n".join(notes))
        ok_button.setEnabled(self.plan.ok and bool(self.plan.items))
        ok_button.setText(f"Rename {len(self.plan.items)} file{'s' if len(self.plan.items) != 1 else ''}")

    def final_plan(self) -> renaming.RenamePlan:
        """The plan to apply, rebuilt now in case the disk changed while the dialog was open."""
        return self.build_plan()
