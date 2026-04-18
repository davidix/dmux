"""XDG-style data and config paths for dmux."""

from __future__ import annotations

import os
import warnings
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


def dmux_package_dir() -> Path:
    """Directory containing this package (``…/site-packages/dmux`` or ``…/src/dmux``)."""
    return Path(__file__).resolve().parent


def resolve_dmux_web_dir() -> Path:
    """Where ``web/templates`` and ``web/static`` live for the Flask UI.

    Set ``DMUX_WEB_ROOT`` to your **git checkout** (repo root) or to ``…/src/dmux`` so the
    running server uses your working copy instead of whatever was bundled in the installed wheel.
    """
    pkg = dmux_package_dir()
    bundled = pkg / "web"
    override = os.environ.get("DMUX_WEB_ROOT", "").strip()
    if not override:
        return bundled
    base = Path(override).expanduser().resolve()
    candidates: list[Path] = []
    if (base / "templates" / "index.html").is_file():
        candidates.append(base)
    candidates.extend(
        (
            base / "web",
            base / "src" / "dmux" / "web",
        )
    )
    for w in candidates:
        if (w / "templates" / "index.html").is_file():
            return w
    warnings.warn(
        f"DMUX_WEB_ROOT={override!r} is not a dmux web tree (missing web/templates/index.html); "
        f"using bundled UI under {bundled}",
        UserWarning,
        stacklevel=2,
    )
    return bundled
