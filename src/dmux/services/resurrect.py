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

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

_log = logging.getLogger(__name__)

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
# Suffix for the per-snapshot copy of ``pane_contents.tar.gz`` we keep beside
# each save so old snapshots can replay their pane history (the plugin itself
# only keeps a single archive, overwritten by every save).
_CONTENTS_SUFFIX = ".contents.tar.gz"
# Filename the plugin reads/writes for the (single, latest) pane-contents
# archive. Centralised so save() and restore() agree on what to copy/swap.
_CONTENTS_ARCHIVE_NAME = "pane_contents.tar.gz"


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


def contents_archive_path(socket_path: str | None = None) -> Path:
    """The single live ``pane_contents.tar.gz`` the plugin reads/writes."""
    return save_dir(socket_path) / _CONTENTS_ARCHIVE_NAME


def per_snapshot_contents_path(save_file: Path) -> Path:
    """Sibling file holding the snapshot's own copy of the contents archive."""
    return save_file.with_name(save_file.name + _CONTENTS_SUFFIX)


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


# tmux-resurrect's save format is tab-separated, one record per line. We only
# parse the fields we need for a summary; everything else is left to the
# plugin's restore.sh. Reference: tmux-plugins/tmux-resurrect/scripts/save.sh.
_NON_SHELL_COMMANDS = {
    "zsh", "bash", "sh", "fish", "ksh", "dash", "tcsh", "csh",
    "-zsh", "-bash", "-sh", "-fish",
}


def parse_save_file(path: Path, *, max_bytes: int = 2_000_000) -> dict[str, object]:
    """Extract a lightweight summary from a tmux-resurrect save file.

    Returns ``{session_count, window_count, pane_count, session_names,
    commands, last_session, current_session, parse_error}``. Reads at most
    ``max_bytes`` from the file (resurrect snapshots are usually a few KB; the
    cap stops a pathological file from blocking the UI).
    """
    summary: dict[str, object] = {
        "session_count": 0,
        "window_count": 0,
        "pane_count": 0,
        "session_names": [],
        "commands": [],
        "last_session": None,
        "current_session": None,
    }
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            data = fh.read(max_bytes + 1)
    except OSError as e:
        summary["parse_error"] = f"could not read file: {e}"
        return summary
    truncated = len(data) > max_bytes
    if truncated:
        data = data[:max_bytes]
        # Drop any half-line at the end so we don't misparse it.
        nl = data.rfind("\n")
        if nl >= 0:
            data = data[:nl]
        summary["truncated"] = True

    sessions: list[str] = []
    seen_sessions: set[str] = set()
    windows: set[tuple[str, str]] = set()
    pane_count = 0
    cmds: list[str] = []
    seen_cmds: set[str] = set()

    for line in data.splitlines():
        if not line:
            continue
        parts = line.split("\t")
        kind = parts[0]
        if kind == "pane" and len(parts) >= 10:
            session = parts[1]
            if session and session not in seen_sessions:
                seen_sessions.add(session)
                sessions.append(session)
            pane_count += 1
            cmd = parts[9].strip() if len(parts) > 9 else ""
            if cmd and cmd not in _NON_SHELL_COMMANDS and cmd not in seen_cmds:
                seen_cmds.add(cmd)
                cmds.append(cmd)
        elif kind == "window" and len(parts) >= 3:
            session = parts[1]
            wi = parts[2]
            if session and session not in seen_sessions:
                seen_sessions.add(session)
                sessions.append(session)
            windows.add((session, wi))
        elif kind == "state" and len(parts) >= 3:
            summary["current_session"] = parts[1] or None
            summary["last_session"] = parts[2] or None

    summary["session_count"] = len(sessions)
    summary["window_count"] = len(windows)
    summary["pane_count"] = pane_count
    summary["session_names"] = sessions
    summary["commands"] = cmds
    return summary


def list_save_files_detailed(socket_path: str | None = None) -> list[dict[str, object]]:
    """Per-file metadata for the UI; newest first.

    Returns ``[{name, path, mtime, size, is_last, summary}]``. ``is_last``
    flags whichever file the plugin's ``last`` symlink resolves to (i.e.
    what the next plain ``scripts/restore.sh`` invocation would replay).
    ``summary`` is the parsed counts/session-names from
    :func:`parse_save_file`.
    """
    files = list_save_files(socket_path)
    if not files:
        return []
    last_target: Path | None = None
    last_link = save_dir(socket_path) / "last"
    try:
        if last_link.is_symlink() or last_link.exists():
            resolved = last_link.resolve()
            if resolved.is_file():
                last_target = resolved
    except OSError:
        last_target = None
    out: list[dict[str, object]] = []
    for p in reversed(files):  # newest first for the UI
        try:
            st = p.stat()
        except OSError:
            continue
        resolved_p: Path
        try:
            resolved_p = p.resolve()
        except OSError:
            resolved_p = p
        contents_p = per_snapshot_contents_path(resolved_p)
        contents_size = 0
        has_contents = False
        try:
            if contents_p.is_file():
                has_contents = True
                contents_size = int(contents_p.stat().st_size)
        except OSError:
            has_contents = False
        out.append({
            "name": p.name,
            "path": str(resolved_p),
            "mtime": int(st.st_mtime),
            "size": int(st.st_size),
            "is_last": bool(last_target is not None and resolved_p == last_target),
            "summary": parse_save_file(p),
            "has_contents_archive": has_contents,
            "contents_archive_size": contents_size,
        })
    return out


def delete_save_file(file: Path, socket_path: str | None = None) -> None:
    """Remove a resurrect save file from disk, refusing anything outside ``save_dir``.

    Also drops the ``last`` symlink when it points at the file we're removing,
    so the plugin doesn't try to restore from a broken link next time.
    """
    target = file
    try:
        resolved = target.resolve()
    except OSError as e:
        raise ResurrectError(f"could not resolve {target}: {e}") from e
    base = save_dir(socket_path).resolve()
    try:
        resolved.relative_to(base)
    except ValueError as e:
        raise ResurrectError(
            f"refusing to delete {resolved}: not inside resurrect save dir {base}"
        ) from e
    if not _SAVE_FILE_RE.match(resolved.name):
        raise ResurrectError(
            f"refusing to delete {resolved.name}: not a tmux-resurrect save file"
        )
    if not resolved.is_file():
        raise ResurrectError(f"resurrect file not found: {resolved}")
    last_link = base / "last"
    try:
        if last_link.is_symlink() and last_link.resolve() == resolved:
            last_link.unlink()
    except OSError:
        # Stale or unreadable symlink: best-effort cleanup, ignore.
        pass
    try:
        resolved.unlink()
    except OSError as e:
        raise ResurrectError(f"could not delete {resolved}: {e}") from e
    # Drop the sibling per-snapshot pane-contents archive too if we made one.
    sibling = per_snapshot_contents_path(resolved)
    try:
        if sibling.is_file():
            sibling.unlink()
    except OSError as e:
        _log.warning("could not delete %s: %s", sibling, e)


def adopt_existing_contents_archive(socket_path: str | None = None) -> Path | None:
    """One-shot migration: stash the live archive next to the latest save.

    tmux-resurrect snapshots created before per-snapshot stashing was
    introduced have no sibling ``.contents.tar.gz``. The live archive only
    matches the *latest* save (it's overwritten on every save), so the most
    we can recover is "give the latest snapshot its contents". Returns the
    path we stashed to, or ``None`` when there was nothing to do.
    """
    latest = latest_save_path(socket_path)
    if latest is None:
        return None
    dst = per_snapshot_contents_path(latest)
    if dst.is_file():
        return None
    src = contents_archive_path(socket_path)
    if not src.is_file():
        return None
    try:
        shutil.copy2(src, dst)
    except OSError as e:
        _log.warning("could not adopt pane-contents archive into %s: %s", dst, e)
        return None
    return dst


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
    # tmux-resurrect keeps a single ``pane_contents.tar.gz`` and overwrites it
    # on every save, which means restoring an *older* snapshot finds no
    # matching pane-content files and silently skips replaying history. We
    # snapshot the archive next to the save file so each snapshot can carry
    # its own pane contents.
    _stash_contents_archive_for(latest, socket_path=socket_path)
    return latest


def _stash_contents_archive_for(save_file: Path, *, socket_path: str | None) -> None:
    """Copy the live ``pane_contents.tar.gz`` next to ``save_file``.

    Best-effort: if the plugin wasn't configured to capture pane contents the
    archive simply doesn't exist; we just skip. Any copy error is logged but
    never raised — the structural save itself already succeeded.
    """
    src = contents_archive_path(socket_path)
    if not src.is_file():
        return
    dst = per_snapshot_contents_path(save_file)
    try:
        shutil.copy2(src, dst)
    except OSError as e:
        _log.warning("could not stash pane-contents archive to %s: %s", dst, e)


def restore(file: Path | None = None, socket_path: str | None = None) -> None:
    """Run ``scripts/restore.sh``; if ``file`` is given, point ``last`` at it first.

    When restoring a specific ``file``, also swap that snapshot's
    ``<file>.contents.tar.gz`` (if present) into the live
    ``pane_contents.tar.gz`` slot so the plugin replays the *matching* pane
    history. The pre-existing live archive is restored afterwards (best
    effort) so a later ``prefix + Ctrl-r`` still hits the most recent save.
    """
    if not is_installed():
        raise ResurrectError(
            "tmux-resurrect is not installed (clone via TPM: open Plugins → Install)."
        )
    backup_archive: Path | None = None
    swapped_archive = False
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
        backup_archive, swapped_archive = _swap_in_contents_archive_for(
            file, socket_path=socket_path
        )
    script = plugin_dir() / _RESTORE_SCRIPT
    cmd = [*_tmux_base_cmd(socket_path), "run-shell", str(script)]
    try:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
        except OSError as e:
            raise ResurrectError(f"Cannot exec tmux for resurrect restore: {e}") from e
        except subprocess.TimeoutExpired as e:
            raise ResurrectError("tmux-resurrect restore timed out (>240s)") from e
        if proc.returncode != 0:
            # `tmux run-shell` returns the script's exit code, but tmux-resurrect's
            # restore.sh ends in calls (display-message / switch-client) that fail
            # benignly when there's no attached client. Quote the actual bytes so
            # the UI can show the user what tmux said.
            stdout = (proc.stdout or "").strip()
            stderr = (proc.stderr or "").strip()
            msg = stderr or stdout or "tmux run-shell failed"
            raise ResurrectError(
                f"resurrect restore failed (tmux exit {proc.returncode}): {msg}"
            )
    finally:
        if swapped_archive:
            _restore_contents_archive(
                backup_archive, socket_path=socket_path
            )


def _swap_in_contents_archive_for(
    save_file: Path, *, socket_path: str | None
) -> tuple[Path | None, bool]:
    """Make ``pane_contents.tar.gz`` match ``save_file``.

    Returns ``(backup_path, swapped)``. ``backup_path`` is where we moved the
    pre-existing live archive (so we can put it back after restore), or
    ``None`` if there was nothing to back up. ``swapped`` is True iff we
    actually changed the live archive — caller uses it to decide whether to
    call :func:`_restore_contents_archive`.
    """
    per_snapshot = per_snapshot_contents_path(save_file)
    if not per_snapshot.is_file():
        # No archive for this snapshot. We deliberately leave the live
        # archive alone: at worst restore.sh skips the cat step (same as
        # before), at best it happens to match (e.g. user is restoring the
        # latest snapshot and never had per-snapshot stashing).
        return None, False
    live = contents_archive_path(socket_path)
    backup: Path | None = None
    if live.is_file():
        backup = live.with_suffix(live.suffix + ".dmux-bak")
        try:
            if backup.exists():
                backup.unlink()
            shutil.move(str(live), str(backup))
        except OSError as e:
            _log.warning("could not back up %s: %s", live, e)
            backup = None
    try:
        shutil.copy2(per_snapshot, live)
    except OSError as e:
        # Couldn't put the per-snapshot archive in place; try to roll back.
        if backup is not None and backup.is_file():
            try:
                shutil.move(str(backup), str(live))
            except OSError:
                pass
        raise ResurrectError(
            f"could not stage pane-contents archive for {save_file.name}: {e}"
        ) from e
    return backup, True


def _restore_contents_archive(
    backup: Path | None, *, socket_path: str | None
) -> None:
    """Put the previously live ``pane_contents.tar.gz`` back, if we backed one up."""
    live = contents_archive_path(socket_path)
    if backup is None:
        # Nothing to restore — but our swapped-in archive is still in `live`,
        # which is fine: it represents the most recently restored snapshot.
        return
    try:
        if live.is_file():
            live.unlink()
        shutil.move(str(backup), str(live))
    except OSError as e:
        _log.warning("could not restore live pane-contents archive: %s", e)


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
