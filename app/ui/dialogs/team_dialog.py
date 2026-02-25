"""Dialog for selecting team member and viewing team progress."""
from __future__ import annotations
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QGroupBox,
    QProgressBar,
    QRadioButton,
    QButtonGroup,
    QMessageBox,
    QWidget,
)

from app.models.team_manager import TEAM_MEMBERS, TeamManager


class TeamDialog(QDialog):
    """Let the user pick their name and see team progress."""

    def __init__(
        self,
        team_mgr: TeamManager,
        all_images: list[Path],
        has_labels_fn,
        current_member: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Equipe de labellisation")
        self.setMinimumWidth(420)
        self._team_mgr = team_mgr
        self._all_images = all_images
        self._has_labels_fn = has_labels_fn
        self._selected_member: str | None = current_member

        layout = QVBoxLayout(self)

        # --- Member selection ---
        select_group = QGroupBox("Qui es-tu ?")
        select_layout = QVBoxLayout(select_group)
        self._btn_group = QButtonGroup(self)
        self._radios: dict[str, QRadioButton] = {}

        for member in TEAM_MEMBERS:
            radio = QRadioButton(member)
            radio.setStyleSheet("font-size: 14px; padding: 4px;")
            if member == current_member:
                radio.setChecked(True)
            self._radios[member] = radio
            self._btn_group.addButton(radio)
            select_layout.addWidget(radio)

        layout.addWidget(select_group)

        # --- Progress ---
        progress_group = QGroupBox("Progression")
        progress_layout = QVBoxLayout(progress_group)

        self._progress_bars: dict[str, QProgressBar] = {}
        self._progress_labels: dict[str, QLabel] = {}

        for member in TEAM_MEMBERS:
            row = QHBoxLayout()
            name_lbl = QLabel(f"{member}:")
            name_lbl.setFixedWidth(90)
            name_lbl.setStyleSheet("font-weight: bold;")
            row.addWidget(name_lbl)

            bar = QProgressBar()
            bar.setMinimum(0)
            bar.setTextVisible(True)
            bar.setStyleSheet("""
                QProgressBar {
                    border: 1px solid #555;
                    border-radius: 3px;
                    background: #2a2a2a;
                    text-align: center;
                    color: #ddd;
                }
                QProgressBar::chunk {
                    background: #2a82da;
                    border-radius: 2px;
                }
            """)
            row.addWidget(bar)

            count_lbl = QLabel("")
            count_lbl.setFixedWidth(70)
            count_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(count_lbl)

            self._progress_bars[member] = bar
            self._progress_labels[member] = count_lbl
            progress_layout.addLayout(row)

        layout.addWidget(progress_group)

        # --- Total ---
        self._total_label = QLabel()
        self._total_label.setStyleSheet("font-size: 13px; color: #aaa; padding: 4px;")
        layout.addWidget(self._total_label)

        # --- Buttons ---
        btn_layout = QHBoxLayout()

        self._distribute_btn = QPushButton("Redistribuer les images")
        self._distribute_btn.setToolTip("Redistribuer equitablement les images non assignees")
        self._distribute_btn.clicked.connect(self._distribute)
        btn_layout.addWidget(self._distribute_btn)

        btn_layout.addStretch()

        self._show_all_btn = QPushButton("Voir toutes les images")
        self._show_all_btn.setToolTip("Desactiver le filtre par membre")
        self._show_all_btn.clicked.connect(self._show_all)
        btn_layout.addWidget(self._show_all_btn)

        ok_btn = QPushButton("OK")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._accept)
        btn_layout.addWidget(ok_btn)

        layout.addLayout(btn_layout)

        self._refresh_progress()

    @property
    def selected_member(self) -> str | None:
        return self._selected_member

    def _refresh_progress(self) -> None:
        total_labeled = 0
        total_assigned = 0

        for member in TEAM_MEMBERS:
            labeled, total = self._team_mgr.get_member_progress(
                member, self._all_images, self._has_labels_fn
            )
            bar = self._progress_bars[member]
            bar.setMaximum(max(total, 1))
            bar.setValue(labeled)
            self._progress_labels[member].setText(f"{labeled}/{total}")
            total_labeled += labeled
            total_assigned += total

        not_assigned = len(self._all_images) - total_assigned
        self._total_label.setText(
            f"Total: {total_labeled}/{len(self._all_images)} images labellisees"
            + (f"  ({not_assigned} non assignees)" if not_assigned > 0 else "")
        )

    def _distribute(self) -> None:
        self._team_mgr.distribute_images(self._all_images)
        self._refresh_progress()

    def _show_all(self) -> None:
        self._selected_member = None
        self.accept()

    def _accept(self) -> None:
        checked = self._btn_group.checkedButton()
        if checked:
            self._selected_member = checked.text()
        self.accept()
