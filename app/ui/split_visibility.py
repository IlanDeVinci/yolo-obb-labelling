from __future__ import annotations

from pathlib import Path
from typing import Callable


def visible_split_images(
    images: list[Path],
    *,
    active_team_member: str,
    is_distributed: bool,
    get_member_images: Callable[[str, list[Path]], list[Path]] | None,
) -> list[Path]:
    if not active_team_member or not is_distributed or get_member_images is None:
        return list(images)
    return list(get_member_images(active_team_member, list(images)))
