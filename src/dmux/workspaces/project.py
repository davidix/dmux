"""Per-project workspace layouts stored under `.dmux/`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from dmux.paths import project_layout_path


def find_project_root(start: Path | None = None) -> Path | None:
    """Walk upward for `.git`, `pyproject.toml`, or `.dmux` marker."""
    cur = (start or Path.cwd()).resolve()
    for _ in range(64):
        if (cur / ".dmux").is_dir():
            return cur
        if (cur / ".git").exists():
            return cur
        if (cur / "pyproject.toml").is_file():
            return cur
        if cur.parent == cur:
            return None
        cur = cur.parent
    return None


def load_project_layout(project_root: Path) -> dict[str, Any] | None:
    path = project_layout_path(project_root)
    if not path.is_file():
        return None
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def save_project_layout(project_root: Path, data: dict[str, Any]) -> Path:
    path = project_layout_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
