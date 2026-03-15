"""Compatibility facade for command classes split into smaller modules."""
from __future__ import annotations

from app.commands.add_label_command import AddLabelCommand, AddLabelsCommand
from app.commands.delete_labels_command import DeleteLabelsCommand
from app.commands.modify_label_command import ModifyLabelCommand
from app.commands.toggle_label_mode_command import ToggleLabelModeCommand

__all__ = [
    "AddLabelCommand",
    "AddLabelsCommand",
    "DeleteLabelsCommand",
    "ModifyLabelCommand",
    "ToggleLabelModeCommand",
]
