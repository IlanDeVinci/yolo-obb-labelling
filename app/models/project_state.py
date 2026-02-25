"""Project session state — persists folder, classes and image index as JSON."""
from __future__ import annotations
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

_STATE_FILENAME = ".yolo_obb_project.json"


@dataclass
class ProjectState:
    """Lightweight session snapshot written next to the images folder."""

    folder: str = ""          # absolute path to the images / dataset root folder
    yaml_path: str = ""       # absolute path to the dataset YAML (empty if plain folder)
    class_names: list[str] = field(default_factory=list)
    current_index: int = 0    # index of the last viewed image
    split: str = "train"      # active split when the session was saved
    team_member: str = ""     # active team member name (empty = show all)

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def save(self, path: Path) -> None:
        """Write state to *path* as indented JSON.  Silently swallows OS errors."""
        try:
            path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        except OSError:
            pass

    @classmethod
    def load(cls, path: Path) -> "ProjectState":
        """Load from *path*.  Returns a default (empty) state on any error."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(
                folder=data.get("folder", ""),
                yaml_path=data.get("yaml_path", ""),
                class_names=data.get("class_names", []),
                current_index=max(0, int(data.get("current_index", 0))),
                split=data.get("split", "train"),
                team_member=data.get("team_member", ""),
            )
        except Exception:  # noqa: BLE001
            return cls()

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    @classmethod
    def state_file(cls, folder: Path) -> Path:
        """Return the canonical path for the state file inside *folder*."""
        return folder / _STATE_FILENAME

    def has_meaningful_state(self) -> bool:
        """True if there is something worth offering to restore."""
        return self.current_index > 0 or bool(self.class_names)
