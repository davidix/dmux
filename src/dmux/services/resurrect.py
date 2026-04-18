"""Integration with ``tmux-plugins/tmux-resurrect`` (TPM).

dmux can capture a "rich" snapshot that goes beyond the structural layout we
store in SQLite — it shells out to the `tmux-resurrect` plugin's own
``scripts/save.sh`` (which writes per-pane processes, optional pane contents
and vim/neovim sessions to ``$HOME/.local/share/tmux/resurrect/``) and remembers
the resulting file path in the snapshot metadata. The matching restore step
points the plugin's ``last`` symlink at that file and runs ``scripts/restore.sh``.

Everything here is a no-op when the plugin isn't installed; callers should
inspect :func:`is_installed` before promising the user a "rich" save will work.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from dmux.services.plugin_manager import (
    list_configured_plugins,
    tpm_plugins_root,
)

PLUGIN_SPEC = "tmux-plugins/tmux-resurrect"
CONTINUUM_SPEC = "tmux-plugins/tmux-continuum"

_PLUGIN_DIR_NAME = "tmux-resurrect"
_SAVE_SCRIPT = "scripts/save.sh"
_RESTORE_SCRIPT = "scripts/restore.sh"
# Newer tmux-resurrect defaults to the XDG path; the legacy ~/.tmux/resurrect
# layout still exists in the wild and is honoured if the new dir doesn't yet.
_DEFAULT_SAVE_SUBDIR_NEW = ".local/share/tmux/resurrect"
_DEFAULT_SAVE_SUBDIR_LEGACY = ".tmux/resurrect"
# tmux-resurrect names files like ``tmux_resurrect_20250414T235959.txt``.
_SAVE_FILE_RE = re.compile(r"^tmux_resurrect_\d{8}T\d{6}\.txt$")


def plugin_dir() -> Path:
    """Filesystem path tmux-resurrect would be cloned into by TPM."""
    return tpm_plugins_root() / _PLUGIN_DIR_NAME


def is_installed() -> bool:
    """True iff TPM has cloned tmux-resurrect and the save script is on disk."""
    p = plugin_dir()
    return p.is_dir() and (p / _SAVE_SCRIPT).is_file() and (p / _RESTORE_SCRIPT).is_file()


def is_configured() -> bool:
    """True iff ``plugins.tmux`` lists tmux-resurrect (independent of disk install)."""
    return PLUGIN_SPEC in list_configured_plugins()


def _tmux_base_cmd(socket_path: str | None) -> list[str]:
    cmd: list[str] = ["tmux"]
    if socket_path:
        cmd.extend(["-S", socket_path])
    return cmd


def _resurrect_dir_option(socket_path: str | None) -> str | None:
    """Read ``@resurrect-dir`` from the live tmux server, expanded; empty → None."""
    cmd = [*_tmux_base_cmd(socket_path), "show-option", "-gqv", "@resurrect-dir"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    raw = (proc.stdout or "").strip()
    if not raw:
        return None
    return os.path.expanduser(os.path.expandvars(raw))


def save_dir(socket_path: str | None = None) -> Path:
    """Directory tmux-resurrect uses for its save files (XDG default; legacy fallback)."""
    explicit = _resurrect_dir_option(socket_path)
    if explicit:
        return Path(explicit)
    home = Path.home()
    new_dir = home / _DEFAULT_SAVE_SUBDIR_NEW
    legacy_dir = home / _DEFAULT_SAVE_SUBDIR_LEGACY
    if new_dir.is_dir():
        return new_dir
    if legacy_dir.is_dir():
        return legacy_dir
    return new_dir


def list_save_files(socket_path: str | None = None) -> list[Path]:
    """All known resurrect save files, oldest first (mtime order)."""
    d = save_dir(socket_path)
    if not d.is_dir():
        return []
    files: list[Path] = []
    for p in d.iterdir():
        if not p.is_file():
            continue
        if _SAVE_FILE_RE.match(p.name):
            files.append(p)
    files.sort(key=lambda p: p.stat().st_mtime)
    return files


def latest_save_path(socket_path: str | None = None) -> Path | None:
    """The newest save file (resolving the ``last`` symlink when present)."""
    d = save_dir(socket_path)
    last = d / "last"
    try:
        if last.is_symlink() or last.exists():
            target = last.resolve()
            if target.is_file():
                return target
    except OSError:
        pass
    files = list_save_files(socket_path)
    return files[-1] if files else None


class ResurrectError(Exception):
    """Raised when a resurrect save/restore call fails (with stderr context)."""


def save(socket_path: str | None = None) -> Path:
    """Trigger ``scripts/save.sh`` via ``tmux run-shell`` and return the new file.

    ``run-shell`` ensures the script inherits the ``TMUX`` env var pointing at
    *this* server (so it doesn't accidentally save the wrong server's state).
    Raises :class:`ResurrectError` if the plugin is missing, the script exits
    non-zero, or no new file appears within the timeout.
    """
    if not is_installed():
        raise ResurrectError(
            "tmux-resurrect is not installed (clone via TPM: open Plugins → Install)."
        )
    script = plugin_dir() / _SAVE_SCRIPT
    cmd = [*_tmux_base_cmd(socket_path), "run-shell", str(script)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except OSError as e:
        raise ResurrectError(f"Cannot exec tmux for resurrect save: {e}") from e
    except subprocess.TimeoutExpired as e:
        raise ResurrectError("tmux-resurrect save timed out (>120s)") from e
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "tmux run-shell failed").strip()
        raise ResurrectError(f"resurrect save failed: {msg}")
    latest = latest_save_path(socket_path)
    if latest is None or not latest.is_file():
        raise ResurrectError(
            f"resurrect save produced no file in {save_dir(socket_path)}"
        )
    return latest


def restore(file: Path | None = None, socket_path: str | None = None) -> None:
    """Run ``scripts/restore.sh``; if ``file`` is given, point ``last`` at it first."""
    if not is_installed():
        raise ResurrectError(
            "tmux-resurrect is not installed (clone via TPM: open Plugins → Install)."
        )
    if file is not None:
        if not file.is_file():
            raise ResurrectError(f"resurrect file not found: {file}")
        target = save_dir(socket_path) / "last"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink() or target.exists():
                target.unlink()
            target.symlink_to(file.resolve())
        except OSError as e:
            raise ResurrectError(
                f"could not point {target} at {file}: {e}"
            ) from e
    script = plugin_dir() / _RESTORE_SCRIPT
    cmd = [*_tmux_base_cmd(socket_path), "run-shell", str(script)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
    except OSError as e:
        raise ResurrectError(f"Cannot exec tmux for resurrect restore: {e}") from e
    except subprocess.TimeoutExpired as e:
        raise ResurrectError("tmux-resurrect restore timed out (>240s)") from e
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "tmux run-shell failed").strip()
        raise ResurrectError(f"resurrect restore failed: {msg}")


def status(socket_path: str | None = None) -> dict[str, object]:
    """Snapshot of resurrect state for the UI (installed/configured/saves dir)."""
    saves = list_save_files(socket_path)
    latest = saves[-1] if saves else None
    return {
        "spec": PLUGIN_SPEC,
        "configured": is_configured(),
        "installed": is_installed(),
        "save_dir": str(save_dir(socket_path)),
        "latest_save_path": str(latest) if latest else None,
        "save_count": len(saves),
        "continuum_configured": CONTINUUM_SPEC in list_configured_plugins(),
    }
