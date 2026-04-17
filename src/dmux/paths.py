"""XDG-style data and config paths for dmux."""

from __future__ import annotations

import os
from pathlib import Path


def data_home() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    if base:
        return Path(base).expanduser()
    return Path.home() / ".local" / "share"


def config_home() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base).expanduser()
    return Path.home() / ".config"


def dmux_data_dir() -> Path:
    return data_home() / "dmux"


def dmux_config_dir() -> Path:
    return config_home() / "dmux"


def database_path() -> Path:
    return dmux_data_dir() / "dmux.db"


def autosave_pid_path() -> Path:
    return dmux_data_dir() / "autosave.pid"


def project_marker_name() -> str:
    return ".dmux"


def project_layout_path(project_root: Path) -> Path:
    return project_root / project_marker_name() / "layout.json"
