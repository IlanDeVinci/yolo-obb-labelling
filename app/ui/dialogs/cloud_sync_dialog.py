"""Dialogs for cloud sync configuration and live status."""

from __future__ import annotations

import json

from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QPlainTextEdit,
    QVBoxLayout,
)


class CloudSyncSettingsDialog(QDialog):
    def __init__(self, initial: dict[str, object], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Cloud Sync Settings")
        self.setMinimumWidth(500)

        layout = QVBoxLayout(self)

        self._enabled = QCheckBox("Enable cloud sync for this app")
        self._enabled.setChecked(bool(initial.get("enabled", False)))
        layout.addWidget(self._enabled)

        form = QFormLayout()
        self._server = QLineEdit(str(initial.get("server_url", "")))
        self._project_id = QLineEdit(str(initial.get("project_id", "")))
        self._project_password = QLineEdit(str(initial.get("project_password", "")))
        self._project_password.setEchoMode(QLineEdit.EchoMode.Password)
        self._username = QLineEdit(str(initial.get("username", "")))
        self._user_password = QLineEdit(str(initial.get("user_password", "")))
        self._user_password.setEchoMode(QLineEdit.EchoMode.Password)
        self._poll = QLineEdit(str(initial.get("poll_seconds", "1.2")))

        form.addRow("Server URL", self._server)
        form.addRow("Project ID", self._project_id)
        form.addRow("Project Password", self._project_password)
        form.addRow("Username", self._username)
        form.addRow("User Password", self._user_password)
        form.addRow("Poll Seconds", self._poll)
        layout.addLayout(form)

        note = QLabel(
            "Each collaborator should use the same Project ID + Project Password, "
            "but their own Username + User Password."
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> dict[str, object]:
        try:
            poll = float(self._poll.text().strip() or "1.2")
        except ValueError:
            poll = 1.2

        return {
            "enabled": self._enabled.isChecked(),
            "server_url": self._server.text().strip(),
            "project_id": self._project_id.text().strip(),
            "project_password": self._project_password.text(),
            "username": self._username.text().strip(),
            "user_password": self._user_password.text(),
            "poll_seconds": max(0.5, poll),
        }


class CloudSyncStatusDialog(QDialog):
    def __init__(self, status_provider, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Cloud Sync Status")
        self.setMinimumSize(700, 460)
        self._status_provider = status_provider

        layout = QVBoxLayout(self)

        toolbar = QHBoxLayout()
        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.clicked.connect(self.refresh)
        toolbar.addWidget(self._refresh_btn)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        layout.addWidget(self._text, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        self.refresh()

    def refresh(self) -> None:
        status = self._status_provider() if self._status_provider else {}
        self._text.setPlainText(json.dumps(status, indent=2, ensure_ascii=False))
