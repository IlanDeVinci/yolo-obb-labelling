"""Project management — unified project file with team, classes, and state."""
from __future__ import annotations
import json
import os
import random
import re
import hashlib
from dataclasses import dataclass, field, asdict
from pathlib import Path
from datetime import datetime
from typing import Optional

# Default projects directory (relative to app root)
PROJECTS_DIR_NAME = "projets"
LOCAL_STATE_DIR_NAME = ".local"
IMAGE_STATUS_DIR_NAME = "image-status"

_PERSONAL_STATE_KEYS = {
    "current_index",
    "current_split",
    "active_team_member",
    "image_completion",
    "use_obb",
    "model_path",
    "model_confidence",
}


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
    image_completion: dict[str, str] = field(default_factory=dict)  # image name -> "in_progress"|"completed"

    # Label mode
    use_obb: bool = True  # True = OBB, False = BBox

    # Model settings
    model_path: str = ""
    model_confidence: float = 0.7

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if not self.modified_at:
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

        # Randomize assignment order so redistribution does not follow
        # filename/order sequence every time.
        random.shuffle(unassigned)

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
    # Image completion state
    # ------------------------------------------------------------------

    def set_image_completion(self, image_name: str, status: str) -> None:
        status = status.strip().lower()
        if status in {"in_progress", "completed"}:
            self.image_completion[image_name] = status
        else:
            self.image_completion.pop(image_name, None)

    def get_image_completion(self, image_name: str) -> str:
        status = self.image_completion.get(image_name, "")
        if status in {"in_progress", "completed"}:
            return status
        return ""

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: Path, include_personal: bool = True) -> bool:
        """Save project to a JSON file. Returns True on success."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            data = asdict(self)
            if not include_personal:
                for key in _PERSONAL_STATE_KEYS:
                    data.pop(key, None)
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
                image_completion=data.get("image_completion", {}),
                use_obb=data.get("use_obb", True),
                model_path=data.get("model_path", ""),
                model_confidence=float(data.get("model_confidence", 0.7)),
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
        self._current_user_state: dict[str, object] = {}

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
        self._current_project.save(self._current_path, include_personal=False)
        self._current_user_state = {
            "current_index": self._current_project.current_index,
            "current_split": self._current_project.current_split,
            "active_team_member": self._current_project.active_team_member,
            "use_obb": self._current_project.use_obb,
            "model_path": self._current_project.model_path,
            "model_confidence": self._current_project.model_confidence,
        }
        self.save_user_state()

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
            normalized = self._normalize_dataset_folder(path)
            state = self._load_user_state(path.parent)
            if not state:
                # Migration fallback from legacy monolithic project json.
                state = {
                    "current_index": project.current_index,
                    "current_split": project.current_split,
                    "active_team_member": project.active_team_member,
                    "image_completion": dict(project.image_completion),
                    "use_obb": project.use_obb,
                    "model_path": project.model_path,
                    "model_confidence": project.model_confidence,
                }
            self._current_user_state = state
            self._apply_user_state_to_project(project, state)
            self._migrate_local_completion_to_shared(path.parent)
            project.image_completion = self._load_shared_image_completion(path.parent)
            self.save_user_state()
            if normalized:
                project.save(path, include_personal=False)
        return project

    def save_current(self) -> bool:
        """Save the current project. Returns True on success."""
        if self._current_project and self._current_path:
            self._normalize_dataset_folder(self._current_path)
            return self._current_project.save(self._current_path, include_personal=False)
        return False

    def save_as(self, path: Path) -> bool:
        """Save current project to a new path."""
        if self._current_project:
            self._current_path = path
            self._normalize_dataset_folder(path)
            return self._current_project.save(path, include_personal=False)
        return False

    def resolve_dataset_folder(self, project: Project | None = None) -> Path | None:
        """Resolve project.dataset_folder to an absolute path in project scope.

        dataset_folder is stored as a path relative to the project folder.
        """
        if project is None:
            project = self._current_project
        if project is None or self._current_path is None:
            return None

        project_folder = self._current_path.parent
        raw = str(project.dataset_folder or "").strip()
        if not raw:
            return None

        rel = Path(raw)
        if rel.is_absolute():
            try:
                rel = rel.resolve().relative_to(project_folder.resolve())
            except Exception:
                return None

        resolved = (project_folder / rel).resolve()
        try:
            resolved.relative_to(project_folder.resolve())
        except Exception:
            return None
        return resolved

    def save_user_state(self) -> bool:
        """Save per-user runtime state in a local sidecar file."""
        if not self._current_project or not self._current_path:
            return False
        payload = {
            "current_index": int(self._current_project.current_index),
            "current_split": str(self._current_project.current_split or "train"),
            "active_team_member": str(self._current_project.active_team_member or ""),
            "use_obb": bool(self._current_project.use_obb),
            "model_path": str(self._current_project.model_path or ""),
            "model_confidence": float(self._current_project.model_confidence),
        }
        self._current_user_state = payload
        try:
            state_path = self._user_state_path(self._current_path.parent)
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            return True
        except OSError:
            return False

    def close_project(self) -> None:
        """Close the current project."""
        self._current_project = None
        self._current_path = None
        self._current_user_state = {}

    def _normalize_dataset_folder(self, project_path: Path) -> bool:
        """Normalize dataset_folder to a project-local relative path.

        Returns True when the in-memory value changed.
        """
        project = self._current_project
        if project is None:
            return False

        project_folder = project_path.parent.resolve()
        raw = str(project.dataset_folder or "").strip()
        if not raw:
            return False

        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = (project_folder / candidate)

        try:
            candidate_resolved = candidate.resolve()
            rel = candidate_resolved.relative_to(project_folder)
        except Exception:
            fallback = Path("images")
            new_value = fallback.as_posix()
        else:
            new_value = rel.as_posix() if rel.as_posix() else "."

        if project.dataset_folder != new_value:
            project.dataset_folder = new_value
            return True
        return False

    def persist_image_completion(
        self,
        image_name: str,
        status: str,
        image_path: Path | None = None,
    ) -> bool:
        """Persist one image completion entry to a shared per-image JSON file."""
        if not self._current_project or not self._current_path:
            return False

        image_name = str(image_name or "").strip()
        normalized_status = str(status or "").strip().lower()
        if not image_name or normalized_status not in {"in_progress", "completed"}:
            return False

        self._current_project.set_image_completion(image_name, normalized_status)

        metadata = {
            "image_name": image_name,
            "status": normalized_status,
            "status_updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "status_updated_by": self._current_username(),
        }

        resolved = self._resolve_image_path_for_name(image_name, image_path)
        if resolved and resolved.exists():
            metadata["image_last_modified"] = datetime.fromtimestamp(
                resolved.stat().st_mtime
            ).astimezone().isoformat(timespec="seconds")

        try:
            status_path = self._image_status_file(self._current_path.parent, image_name)
            status_path.parent.mkdir(parents=True, exist_ok=True)
            status_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
            return True
        except OSError:
            return False

    def persist_all_image_completion(self, image_completion: dict[str, str]) -> int:
        """Persist many completion entries into shared per-image JSON files."""
        if not self._current_path:
            return 0

        count = 0
        for image_name, status in image_completion.items():
            if self.persist_image_completion(image_name, status):
                count += 1
        return count

    def prune_shared_image_completion(self, valid_image_names: set[str]) -> int:
        """Remove shared per-image status files for images not in the current project image set."""
        if not self._current_path:
            return 0

        status_dir = self._current_path.parent / IMAGE_STATUS_DIR_NAME
        if not status_dir.exists():
            return 0

        removed = 0
        for status_file in status_dir.glob("*.json"):
            try:
                data = json.loads(status_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            image_name = str(data.get("image_name", "")).strip()
            if image_name and image_name not in valid_image_names:
                try:
                    status_file.unlink()
                    removed += 1
                except OSError:
                    pass
        return removed

    def _migrate_local_completion_to_shared(self, project_folder: Path) -> None:
        """Move local/legacy completion statuses into shared per-image files."""
        if not self._current_project:
            return

        local_completion = self._current_user_state.get("image_completion")
        migrated = False
        if isinstance(local_completion, dict):
            for image_name, status in local_completion.items():
                if self.persist_image_completion(str(image_name), str(status)):
                    migrated = True

        if self._current_project.image_completion:
            for image_name, status in self._current_project.image_completion.items():
                if self.persist_image_completion(str(image_name), str(status)):
                    migrated = True

        if migrated:
            self._current_project.image_completion = self._load_shared_image_completion(project_folder)

        if isinstance(local_completion, dict) and local_completion:
            self._current_user_state["image_completion"] = {}
            try:
                state_path = self._user_state_path(project_folder)
                state_path.parent.mkdir(parents=True, exist_ok=True)
                state_path.write_text(
                    json.dumps(self._current_user_state, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            except OSError:
                pass

    def _load_shared_image_completion(self, project_folder: Path) -> dict[str, str]:
        """Read shared per-image completion metadata as an image->status map."""
        status_dir = project_folder / IMAGE_STATUS_DIR_NAME
        if not status_dir.exists():
            return {}

        completion: dict[str, tuple[str, str]] = {}
        for status_file in status_dir.glob("*.json"):
            try:
                data = json.loads(status_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(data, dict):
                continue

            image_name = str(data.get("image_name", "")).strip()
            status = str(data.get("status", "")).strip().lower()
            updated_at = str(data.get("status_updated_at", "")).strip()
            if not image_name or status not in {"in_progress", "completed"}:
                continue

            current = completion.get(image_name)
            if current is None or updated_at > current[1]:
                completion[image_name] = (status, updated_at)

        return {image_name: payload[0] for image_name, payload in completion.items()}

    @staticmethod
    def _current_username() -> str:
        return os.environ.get("USERNAME") or os.environ.get("USER") or "unknown"

    def _resolve_image_path_for_name(self, image_name: str, image_path: Path | None) -> Path | None:
        if image_path is not None:
            return image_path

        dataset_folder = self.resolve_dataset_folder(self._current_project)
        if dataset_folder:
            candidate = dataset_folder / image_name
            if candidate.exists():
                return candidate
        return None

    def _image_status_file(self, project_folder: Path, image_name: str) -> Path:
        stem = Path(image_name).stem
        safe_stem = re.sub(r"[^A-Za-z0-9._-]", "_", stem).strip("._") or "image"
        digest = hashlib.sha1(image_name.encode("utf-8")).hexdigest()[:10]
        file_name = f"{safe_stem}-{digest}.json"
        return project_folder / IMAGE_STATUS_DIR_NAME / file_name

    @staticmethod
    def _apply_user_state_to_project(project: Project, state: dict[str, object]) -> None:
        project.current_index = max(0, int(state.get("current_index", project.current_index)))
        project.current_split = str(state.get("current_split", project.current_split or "train"))
        project.active_team_member = str(state.get("active_team_member", project.active_team_member or ""))
        completion = state.get("image_completion")
        if isinstance(completion, dict):
            project.image_completion = {
                str(k): str(v)
                for k, v in completion.items()
                if str(v).strip().lower() in {"in_progress", "completed"}
            }
        project.use_obb = bool(state.get("use_obb", project.use_obb))
        project.model_path = str(state.get("model_path", project.model_path or ""))
        project.model_confidence = float(state.get("model_confidence", project.model_confidence))

    def _load_user_state(self, project_folder: Path) -> dict[str, object]:
        state_path = self._user_state_path(project_folder)
        if not state_path.exists():
            return {}
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return {}

    def _user_state_path(self, project_folder: Path) -> Path:
        user = os.environ.get("USERNAME") or os.environ.get("USER") or "default"
        safe_user = re.sub(r"[^A-Za-z0-9._-]", "_", user)
        return project_folder / LOCAL_STATE_DIR_NAME / f"user-state-{safe_user}.json"
