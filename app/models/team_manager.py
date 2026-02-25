"""Team management — divide images among labelers and track progress."""
from __future__ import annotations
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

TEAM_MEMBERS = ["Auguste", "Samuel", "Ilan", "Alexandre"]
_TEAM_FILENAME = ".yolo_obb_team.json"


@dataclass
class TeamAssignment:
    """Stores per-project team image assignments."""

    assignments: dict[str, list[str]] = field(default_factory=dict)
    # member name → list of image filenames

    def save(self, path: Path) -> None:
        try:
            path.write_text(json.dumps(self.assignments, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    @classmethod
    def load(cls, path: Path) -> "TeamAssignment":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(assignments=data)
        except Exception:
            return cls()


class TeamManager:
    """Manages team assignments for a project folder."""

    def __init__(self) -> None:
        self._assignment = TeamAssignment()
        self._team_file: Path | None = None
        self._active_member: str | None = None

    @property
    def active_member(self) -> str | None:
        return self._active_member

    @active_member.setter
    def active_member(self, name: str | None) -> None:
        self._active_member = name

    @property
    def assignments(self) -> dict[str, list[str]]:
        return self._assignment.assignments

    def load_for_project(self, project_root: Path) -> None:
        """Load existing team assignments or initialize empty."""
        self._team_file = project_root / _TEAM_FILENAME
        if self._team_file.exists():
            self._assignment = TeamAssignment.load(self._team_file)
        else:
            self._assignment = TeamAssignment()

    def save(self) -> None:
        if self._team_file:
            self._assignment.save(self._team_file)

    def distribute_images(self, all_images: list[Path]) -> None:
        """Evenly distribute images among team members.

        Preserves existing assignments if possible, only redistributes
        unassigned images.
        """
        # Collect all currently assigned filenames
        assigned: set[str] = set()
        for filenames in self._assignment.assignments.values():
            assigned.update(filenames)

        # Find unassigned images
        all_names = [img.name for img in all_images]
        unassigned = [name for name in all_names if name not in assigned]

        # Initialize members if not present
        for member in TEAM_MEMBERS:
            if member not in self._assignment.assignments:
                self._assignment.assignments[member] = []

        # Remove assignments for images that no longer exist
        all_names_set = set(all_names)
        for member in TEAM_MEMBERS:
            self._assignment.assignments[member] = [
                f for f in self._assignment.assignments[member]
                if f in all_names_set
            ]

        # Distribute unassigned images round-robin by current load (least loaded first)
        for name in unassigned:
            # Find member with fewest images
            least_loaded = min(
                TEAM_MEMBERS,
                key=lambda m: len(self._assignment.assignments.get(m, [])),
            )
            self._assignment.assignments[least_loaded].append(name)

        self.save()

    def get_member_images(self, member: str, all_images: list[Path]) -> list[Path]:
        """Return only images assigned to the given member."""
        assigned_names = set(self._assignment.assignments.get(member, []))
        return [img for img in all_images if img.name in assigned_names]

    def get_member_progress(self, member: str, all_images: list[Path],
                            has_labels_fn) -> tuple[int, int]:
        """Return (labeled_count, total_count) for a member."""
        member_images = self.get_member_images(member, all_images)
        labeled = sum(1 for img in member_images if has_labels_fn(img))
        return labeled, len(member_images)

    def is_distributed(self) -> bool:
        """True if images have been distributed."""
        return any(len(v) > 0 for v in self._assignment.assignments.values())
