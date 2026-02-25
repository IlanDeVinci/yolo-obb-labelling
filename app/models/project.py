"""Project management — unified project file with team, classes, and state."""
from __future__ import annotations
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from datetime import datetime
from typing import Optional

# Default projects directory (relative to app root)
PROJECTS_DIR_NAME = "projets"


@dataclass
class Project:
    """A labeling project with its own team, classes, and dataset references.

    Projects are saved as JSON files in the 'projets/' folder.
    """

    # Project metadata
    name: str = "Nouveau Projet"
    created_at: str = ""
    modified_at: str = ""

    # Dataset configuration
    dataset_folder: str = ""      # Path to images/dataset root folder
    yaml_path: str = ""           # Path to dataset YAML (optional)
    class_names: list[str] = field(default_factory=list)

    # Team members (user-defined, not predefined)
    team_members: list[str] = field(default_factory=list)
    team_percentages: dict[str, float] = field(default_factory=dict)  # member -> percentage (0-100)
    team_assignments: dict[str, list[str]] = field(default_factory=dict)

    # Session state
    current_index: int = 0
    current_split: str = "train"
    active_team_member: str = ""

    # Label mode
    use_obb: bool = True  # True = OBB, False = BBox

    # Model settings
    model_path: str = ""
    model_confidence: float = 0.25

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        self.modified_at = datetime.now().isoformat()

    # ------------------------------------------------------------------
    # Team management
    # ------------------------------------------------------------------

    def add_team_member(self, name: str, percentage: float = 0.0) -> bool:
        """Add a new team member. Returns True if added, False if already exists."""
        name = name.strip()
        if name and name not in self.team_members:
            self.team_members.append(name)
            self.team_assignments[name] = []
            self.team_percentages[name] = percentage
            return True
        return False

    def set_member_percentage(self, name: str, percentage: float) -> None:
        """Set the percentage for a team member."""
        if name in self.team_members:
            self.team_percentages[name] = max(0.0, min(100.0, percentage))

    def get_member_percentage(self, name: str) -> float:
        """Get the percentage for a team member."""
        return self.team_percentages.get(name, 0.0)

    def remove_team_member(self, name: str) -> bool:
        """Remove a team member. Returns True if removed."""
        if name in self.team_members:
            self.team_members.remove(name)
            self.team_assignments.pop(name, None)
            self.team_percentages.pop(name, None)
            if self.active_team_member == name:
                self.active_team_member = ""
            return True
        return False

    def distribute_images(self, all_images: list[Path], redistribute_all: bool = False) -> None:
        """Distribute images among team members based on their percentages.

        Args:
            all_images: List of all image paths
            redistribute_all: If True, clear existing assignments and redistribute all images
        """
        if not self.team_members:
            return

        all_names = [img.name for img in all_images]
        all_names_set = set(all_names)

        # Initialize members if not present
        for member in self.team_members:
            if member not in self.team_assignments:
                self.team_assignments[member] = []
            if member not in self.team_percentages:
                self.team_percentages[member] = 0.0

        if redistribute_all:
            # Clear all assignments
            for member in self.team_members:
                self.team_assignments[member] = []
            unassigned = list(all_names)
        else:
            # Collect already assigned filenames
            assigned: set[str] = set()
            for filenames in self.team_assignments.values():
                assigned.update(filenames)

            # Remove assignments for images that no longer exist
            for member in self.team_members:
                self.team_assignments[member] = [
                    f for f in self.team_assignments.get(member, [])
                    if f in all_names_set
                ]

            # Find unassigned images
            unassigned = [name for name in all_names if name not in assigned]

        if not unassigned:
            return

        # Calculate distribution based on percentages
        total_percentage = sum(self.team_percentages.get(m, 0.0) for m in self.team_members)

        if total_percentage <= 0:
            # Equal distribution if no percentages set
            shares = {m: 1.0 / len(self.team_members) for m in self.team_members}
        else:
            # Normalize percentages
            shares = {m: self.team_percentages.get(m, 0.0) / total_percentage for m in self.team_members}

        # Calculate target counts
        total_to_assign = len(unassigned)
        targets = {}
        assigned_count = 0

        for i, member in enumerate(self.team_members):
            if i == len(self.team_members) - 1:
                # Last member gets the remainder to avoid rounding issues
                targets[member] = total_to_assign - assigned_count
            else:
                targets[member] = round(total_to_assign * shares[member])
                assigned_count += targets[member]

        # Distribute images according to targets
        idx = 0
        for member in self.team_members:
            count = targets[member]
            self.team_assignments[member].extend(unassigned[idx:idx + count])
            idx += count

    def get_member_images(self, member: str, all_images: list[Path]) -> list[Path]:
        """Return only images assigned to the given member."""
        assigned_names = set(self.team_assignments.get(member, []))
        return [img for img in all_images if img.name in assigned_names]

    def get_member_progress(self, member: str, all_images: list[Path],
                            has_labels_fn) -> tuple[int, int]:
        """Return (labeled_count, total_count) for a member."""
        member_images = self.get_member_images(member, all_images)
        labeled = sum(1 for img in member_images if has_labels_fn(img))
        return labeled, len(member_images)

    def is_distributed(self) -> bool:
        """True if images have been distributed."""
        return any(len(v) > 0 for v in self.team_assignments.values())

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: Path) -> bool:
        """Save project to a JSON file. Returns True on success."""
        self.modified_at = datetime.now().isoformat()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            data = asdict(self)
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            return True
        except OSError:
            return False

    @classmethod
    def load(cls, path: Path) -> Optional["Project"]:
        """Load project from a JSON file. Returns None on error."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(
                name=data.get("name", "Projet"),
                created_at=data.get("created_at", ""),
                modified_at=data.get("modified_at", ""),
                dataset_folder=data.get("dataset_folder", ""),
                yaml_path=data.get("yaml_path", ""),
                class_names=data.get("class_names", []),
                team_members=data.get("team_members", []),
                team_percentages=data.get("team_percentages", {}),
                team_assignments=data.get("team_assignments", {}),
                current_index=max(0, int(data.get("current_index", 0))),
                current_split=data.get("current_split", "train"),
                active_team_member=data.get("active_team_member", ""),
                use_obb=data.get("use_obb", True),
                model_path=data.get("model_path", ""),
                model_confidence=float(data.get("model_confidence", 0.25)),
            )
        except Exception:
            return None

    def has_meaningful_state(self) -> bool:
        """True if there is something worth restoring."""
        return bool(self.dataset_folder) or self.current_index > 0 or bool(self.class_names)


class ProjectManager:
    """Manages the projects directory and current project."""

    def __init__(self, app_root: Path | None = None):
        if app_root is None:
            # Default to the app directory
            app_root = Path(__file__).parent.parent.parent
        self._projects_dir = app_root / PROJECTS_DIR_NAME
        self._projects_dir.mkdir(parents=True, exist_ok=True)
        self._current_project: Project | None = None
        self._current_path: Path | None = None

    @property
    def projects_dir(self) -> Path:
        return self._projects_dir

    @property
    def current_project(self) -> Project | None:
        return self._current_project

    @property
    def current_path(self) -> Path | None:
        return self._current_path

    def list_projects(self) -> list[tuple[str, Path]]:
        """Return list of (project_name, path) tuples.

        Searches both directly in projects_dir and in subfolders.
        """
        projects = []

        # Search in root of projects dir
        for f in self._projects_dir.glob("*.json"):
            proj = Project.load(f)
            if proj:
                projects.append((proj.name, f))
            else:
                projects.append((f.stem, f))

        # Search in subfolders (new structure)
        for folder in self._projects_dir.iterdir():
            if folder.is_dir() and not folder.name.startswith('.'):
                for f in folder.glob("*.json"):
                    proj = Project.load(f)
                    if proj:
                        projects.append((proj.name, f))
                    else:
                        projects.append((f.stem, f))

        return sorted(projects, key=lambda x: x[0].lower())

    def create_project(self, name: str) -> Project:
        """Create a new project with the given name.

        Creates a subfolder in the projects directory with the project name,
        and saves the project JSON inside it.
        """
        self._current_project = Project(name=name)

        # Generate a safe folder name
        safe_name = "".join(c if c.isalnum() or c in " _-" else "_" for c in name)
        safe_name = safe_name.strip() or "projet"

        # Create project folder (ensure unique)
        project_folder = self._projects_dir / safe_name
        counter = 1
        while project_folder.exists():
            project_folder = self._projects_dir / f"{safe_name}_{counter}"
            counter += 1

        project_folder.mkdir(parents=True, exist_ok=True)

        # Save project JSON inside the folder
        self._current_path = project_folder / f"{safe_name}.json"
        self._current_project.save(self._current_path)

        return self._current_project

    def get_project_folder(self) -> Path | None:
        """Return the folder containing the current project file."""
        if self._current_path:
            return self._current_path.parent
        return None

    def open_project(self, path: Path) -> Project | None:
        """Open an existing project file."""
        project = Project.load(path)
        if project:
            self._current_project = project
            self._current_path = path
        return project

    def save_current(self) -> bool:
        """Save the current project. Returns True on success."""
        if self._current_project and self._current_path:
            return self._current_project.save(self._current_path)
        return False

    def save_as(self, path: Path) -> bool:
        """Save current project to a new path."""
        if self._current_project:
            self._current_path = path
            return self._current_project.save(path)
        return False

    def close_project(self) -> None:
        """Close the current project."""
        self._current_project = None
        self._current_path = None
