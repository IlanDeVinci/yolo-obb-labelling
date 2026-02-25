"""Dialogs for project management — create, open, and manage projects."""
from __future__ import annotations
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QListWidget,
    QListWidgetItem,
    QFileDialog,
    QMessageBox,
    QGroupBox,
    QInputDialog,
    QDialogButtonBox,
    QWidget,
    QTabWidget,
)

from app.models.project import Project, ProjectManager


class NewProjectDialog(QDialog):
    """Dialog for creating a new project."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Nouveau Projet")
        self.setMinimumWidth(400)

        self.project_name = ""

        layout = QVBoxLayout(self)

        # Project name
        layout.addWidget(QLabel("Nom du projet:"))
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("Mon Projet")
        self._name_edit.textChanged.connect(self._validate)
        layout.addWidget(self._name_edit)

        # Buttons
        btn_layout = QHBoxLayout()
        self._btn_create = QPushButton("Creer")
        self._btn_create.setEnabled(False)
        self._btn_create.clicked.connect(self._on_create)
        btn_layout.addWidget(self._btn_create)

        btn_cancel = QPushButton("Annuler")
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)

        layout.addLayout(btn_layout)

    def _validate(self) -> None:
        name = self._name_edit.text().strip()
        self._btn_create.setEnabled(bool(name))

    def _on_create(self) -> None:
        self.project_name = self._name_edit.text().strip()
        self.accept()


class OpenProjectDialog(QDialog):
    """Dialog for opening an existing project."""

    def __init__(self, project_manager: ProjectManager, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ouvrir un Projet")
        self.setMinimumSize(450, 350)

        self._project_manager = project_manager
        self.selected_path: Path | None = None

        layout = QVBoxLayout(self)

        # Project list
        layout.addWidget(QLabel("Projets disponibles:"))
        self._list = QListWidget()
        self._list.itemDoubleClicked.connect(self._on_open)
        self._list.currentRowChanged.connect(self._on_selection_changed)
        layout.addWidget(self._list)

        # Buttons
        btn_layout = QHBoxLayout()

        self._btn_open = QPushButton("Ouvrir")
        self._btn_open.setEnabled(False)
        self._btn_open.clicked.connect(self._on_open)
        btn_layout.addWidget(self._btn_open)

        btn_browse = QPushButton("Parcourir...")
        btn_browse.clicked.connect(self._on_browse)
        btn_layout.addWidget(btn_browse)

        btn_delete = QPushButton("Supprimer")
        btn_delete.clicked.connect(self._on_delete)
        btn_layout.addWidget(btn_delete)

        btn_cancel = QPushButton("Annuler")
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)

        layout.addLayout(btn_layout)

        self._refresh_list()

    def _refresh_list(self) -> None:
        self._list.clear()
        for name, path in self._project_manager.list_projects():
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, path)
            self._list.addItem(item)

    def _on_selection_changed(self, row: int) -> None:
        self._btn_open.setEnabled(row >= 0)

    def _on_open(self) -> None:
        item = self._list.currentItem()
        if item:
            self.selected_path = item.data(Qt.ItemDataRole.UserRole)
            self.accept()

    def _on_browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Ouvrir un projet",
            str(self._project_manager.projects_dir),
            "Fichiers projet (*.json)",
        )
        if path:
            self.selected_path = Path(path)
            self.accept()

    def _on_delete(self) -> None:
        item = self._list.currentItem()
        if not item:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        reply = QMessageBox.question(
            self,
            "Supprimer le projet",
            f"Voulez-vous vraiment supprimer le projet '{item.text()}'?\n\n"
            "Cette action ne supprimera pas les images ni les labels.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                Path(path).unlink()
                self._refresh_list()
            except OSError as e:
                QMessageBox.warning(self, "Erreur", f"Impossible de supprimer: {e}")


class TeamManagerDialog(QDialog):
    """Dialog for managing team members within a project."""

    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Gestion des membres")
        self.setMinimumSize(420, 360)

        self._project = project

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("<b>Membres de l'equipe</b>"))

        self._members_list = QListWidget()
        layout.addWidget(self._members_list, stretch=1)

        member_btn_layout = QHBoxLayout()

        btn_add = QPushButton("Ajouter un membre")
        btn_add.clicked.connect(self._on_add_member)
        member_btn_layout.addWidget(btn_add)

        btn_remove = QPushButton("Supprimer le membre selectionne")
        btn_remove.clicked.connect(self._on_remove_selected_member)
        member_btn_layout.addWidget(btn_remove)

        btn_advanced = QPushButton("Options avancees...")
        btn_advanced.setToolTip("Configurer la repartition des pourcentages")
        btn_advanced.clicked.connect(self._open_advanced_distribution)
        member_btn_layout.addWidget(btn_advanced)

        layout.addLayout(member_btn_layout)

        # Dialog buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._refresh_list()

    def _on_accept(self) -> None:
        self.accept()

    def _refresh_list(self) -> None:
        self._members_list.clear()
        for member in self._project.team_members:
            self._members_list.addItem(member)

    def _on_add_member(self) -> None:
        name, ok = QInputDialog.getText(
            self,
            "Ajouter un membre",
            "Nom du nouveau membre:",
        )
        if ok and name.strip():
            if self._project.add_team_member(name.strip(), 0.0):
                self._refresh_list()
            else:
                QMessageBox.warning(self, "Erreur", "Ce membre existe deja.")

    def _on_remove_selected_member(self) -> None:
        item = self._members_list.currentItem()
        if item is None:
            return
        self._on_remove_member(item.text())

    def _on_remove_member(self, name: str) -> None:
        reply = QMessageBox.question(
            self,
            "Retirer le membre",
            f"Voulez-vous vraiment retirer '{name}' de l'equipe?\n\n"
            "Les images qui lui sont assignees seront desassignees.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._project.remove_team_member(name)
            self._refresh_list()

    def _open_advanced_distribution(self) -> None:
        if not self._project.team_members:
            QMessageBox.information(self, "Pas de membres", "Ajoutez au moins un membre.")
            return
        dlg = TeamDistributionDialog(self._project, self)
        dlg.exec()


class TeamDistributionDialog(QDialog):
    """Dialog for editing team image distribution percentages."""

    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Parametres avances - Repartition")
        self.setMinimumSize(500, 420)

        self._project = project
        self._percentage_edits: dict[str, QLineEdit] = {}

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("<b>Repartition des images (%)</b>"))

        header_layout = QHBoxLayout()
        header_layout.addWidget(QLabel("Nom"), stretch=3)
        header_layout.addWidget(QLabel("Pourcentage"), stretch=1)
        layout.addLayout(header_layout)

        self._members_widget = QWidget()
        self._members_layout = QVBoxLayout(self._members_widget)
        self._members_layout.setContentsMargins(0, 0, 0, 0)
        self._members_layout.setSpacing(4)

        scroll = QWidget()
        scroll_layout = QVBoxLayout(scroll)
        scroll_layout.addWidget(self._members_widget)
        scroll_layout.addStretch()
        layout.addWidget(scroll, stretch=1)

        self._lbl_total = QLabel("Total: 0%")
        self._lbl_total.setStyleSheet("font-weight: bold;")
        layout.addWidget(self._lbl_total)

        btn_equal = QPushButton("Repartir egalement")
        btn_equal.clicked.connect(self._on_equal_distribution)
        layout.addWidget(btn_equal)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._refresh_list()

    def _on_accept(self) -> None:
        for member, edit in self._percentage_edits.items():
            try:
                percentage = float(edit.text().replace(",", ".").replace("%", ""))
                self._project.set_member_percentage(member, percentage)
            except ValueError:
                pass
        self.accept()

    def _refresh_list(self) -> None:
        while self._members_layout.count():
            child = self._members_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        self._percentage_edits.clear()

        for member in self._project.team_members:
            row = QHBoxLayout()

            lbl_name = QLabel(member)
            row.addWidget(lbl_name, stretch=3)

            percentage = self._project.get_member_percentage(member)
            edit = QLineEdit(f"{percentage:.0f}")
            edit.setFixedWidth(60)
            edit.setAlignment(Qt.AlignmentFlag.AlignRight)
            edit.textChanged.connect(self._update_total)
            row.addWidget(edit)
            row.addWidget(QLabel("%"))

            self._percentage_edits[member] = edit

            row_widget = QWidget()
            row_widget.setLayout(row)
            self._members_layout.addWidget(row_widget)

        self._update_total()

    def _update_total(self) -> None:
        total = 0.0
        for edit in self._percentage_edits.values():
            try:
                total += float(edit.text().replace(",", ".").replace("%", ""))
            except ValueError:
                pass

        color = "green" if abs(total - 100) < 0.1 else "orange" if total > 0 else "gray"
        self._lbl_total.setText(f"Total: {total:.0f}%")
        self._lbl_total.setStyleSheet(f"font-weight: bold; color: {color};")

    def _on_equal_distribution(self) -> None:
        if not self._project.team_members:
            return

        equal_pct = 100.0 / len(self._project.team_members)
        for edit in self._percentage_edits.values():
            edit.setText(f"{equal_pct:.1f}")


class ProjectSettingsDialog(QDialog):
    """Dialog for project settings (classes, dataset, etc.)."""

    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Parametres - {project.name}")
        self.setMinimumSize(500, 400)

        self._project = project

        layout = QVBoxLayout(self)

        # Tabs
        tabs = QTabWidget()
        layout.addWidget(tabs)

        # General tab
        general_tab = QWidget()
        general_layout = QVBoxLayout(general_tab)

        general_layout.addWidget(QLabel("Nom du projet:"))
        self._name_edit = QLineEdit(project.name)
        general_layout.addWidget(self._name_edit)

        general_layout.addWidget(QLabel("Dossier dataset:"))
        folder_layout = QHBoxLayout()
        self._folder_edit = QLineEdit(project.dataset_folder)
        self._folder_edit.setReadOnly(True)
        folder_layout.addWidget(self._folder_edit)
        btn_browse = QPushButton("...")
        btn_browse.setFixedWidth(30)
        btn_browse.clicked.connect(self._browse_folder)
        folder_layout.addWidget(btn_browse)
        general_layout.addLayout(folder_layout)

        general_layout.addStretch()
        tabs.addTab(general_tab, "General")

        # Classes tab
        classes_tab = QWidget()
        classes_layout = QVBoxLayout(classes_tab)

        classes_layout.addWidget(QLabel("Classes (une par ligne):"))
        self._classes_edit = QLineEdit()
        self._classes_edit.setText(", ".join(project.class_names))
        self._classes_edit.setPlaceholderText("classe1, classe2, classe3")
        classes_layout.addWidget(self._classes_edit)

        classes_layout.addStretch()
        tabs.addTab(classes_tab, "Classes")

        # Dialog buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            "Selectionner le dossier dataset",
            self._folder_edit.text() or "",
        )
        if folder:
            self._folder_edit.setText(folder)

    def _on_accept(self) -> None:
        self._project.name = self._name_edit.text().strip() or self._project.name
        self._project.dataset_folder = self._folder_edit.text()

        # Parse classes
        classes_text = self._classes_edit.text()
        if classes_text:
            self._project.class_names = [
                c.strip() for c in classes_text.split(",") if c.strip()
            ]

        self.accept()
