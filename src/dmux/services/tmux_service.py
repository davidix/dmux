"""libtmux-backed service for sessions, windows, panes, and layouts."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Literal

import libtmux
from libtmux import Pane, Server, Session, Window
from libtmux.constants import PaneDirection
from libtmux.exc import LibTmuxException

from dmux.exceptions import (
    DmuxError,
    PaneNotFoundError,
    SessionExistsError,
    SessionNotFoundError,
    WindowNotFoundError,
)
from dmux.schemas import (
    PaneDTO,
    SessionDTO,
    Snapshot,
    SnapshotPane,
    SnapshotSession,
    SnapshotWindow,
    WindowDTO,
)

LayoutKind = Literal["grid", "vertical", "horizontal", "main-horizontal", "main-vertical"]

def _txt(v: object | None, default: str = "") -> str:
    return str(v) if v is not None else default


def _dim(v: object | None) -> int:
    if v is None:
        return 0
    if isinstance(v, int):
        return v
    return int(str(v))


_LAYOUT_MAP: dict[str, str] = {
    "grid": "tiled",
    "vertical": "even-vertical",
    "horizontal": "even-horizontal",
    "main-horizontal": "main-horizontal",
    "main-vertical": "main-vertical",
}

# Common shell prompt suffixes. ``capture-pane -p`` records prompt + typed
# command on the same physical row, so we look for the rightmost suffix on a
# line and treat whatever follows as the command. Misses true multi-line
# input (zsh secondary `> ` continuation) but handles the 95% case.
_PROMPT_SUFFIXES: tuple[str, ...] = ("$ ", "% ", "# ", "› ", "❯ ", "» ", "➜ ")
_MAX_HISTORY_LINE_LEN: int = 1000


def _extract_command_lines(scrollback: str) -> list[str]:
    """Best-effort: return lines that look like commands typed at a prompt.

    Strategy: for each line, locate the rightmost prompt suffix; whatever
    follows is the candidate command. Drops empty candidates, things that are
    obviously not commands (longer than ``_MAX_HISTORY_LINE_LEN`` chars or
    starting with non-printable junk), and exact duplicates of the immediately
    preceding entry.
    """
    out: list[str] = []
    for raw in scrollback.splitlines():
        line = raw.rstrip("\r")
        if not line.strip():
            continue
        idx = -1
        for suffix in _PROMPT_SUFFIXES:
            j = line.rfind(suffix)
            if j > idx:
                idx = j + len(suffix) - 1  # position of trailing space
        if idx < 0:
            continue
        cand = line[idx + 1:].strip()
        if not cand:
            continue
        if len(cand) > _MAX_HISTORY_LINE_LEN:
            continue
        if not cand[0].isprintable():
            continue
        if out and out[-1] == cand:
            continue
        out.append(cand)
    return out


class TmuxService:
    """Thin, typed wrapper around libtmux with stable DTOs."""

    def __init__(self, socket_path: str | None = None) -> None:
        self._socket_path = socket_path
        self._server: Server | None = None

    def server(self) -> Server:
        if self._server is None:
            if self._socket_path:
                self._server = libtmux.Server(socket_path=self._socket_path)
            else:
                self._server = libtmux.Server()
            # 256-colour mode for CLI tmux invocations and libtmux (#hex / palette).
            self._server.colors = 256
        return self._server

    def _tmux_cli_base(self) -> list[str]:
        """Prefix: ``tmux -2 [-S socket]`` (``-2`` = 256 colours; matches common terminals)."""
        tmux_bin = shutil.which("tmux")
        if not tmux_bin:
            raise DmuxError("tmux executable not found in PATH")
        cmd: list[str] = [tmux_bin, "-2"]
        if self._socket_path:
            cmd.extend(["-S", self._socket_path])
        return cmd

    def _tmux_cli_run(self, argv: list[str]) -> None:
        cmd = self._tmux_cli_base() + argv
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
        if r.returncode != 0:
            msg = (r.stderr or r.stdout or "tmux command failed").strip()
            raise DmuxError(msg)

    def refresh(self) -> None:
        self._server = None

    def list_sessions(self) -> tuple[SessionDTO, ...]:
        return tuple(self._session_to_dto(s) for s in self.server().sessions)

    def get_session(self, name: str) -> Session:
        for session in self.server().sessions:
            if session.session_name == name:
                return session
        raise SessionNotFoundError(name)

    def session_exists(self, name: str) -> bool:
        return self.server().has_session(name)

    def new_session(
        self,
        name: str,
        *,
        cwd: str | None = None,
        window_name: str | None = None,
    ) -> Session:
        if self.session_exists(name):
            raise SessionExistsError(name)
        return self.server().new_session(
            session_name=name,
            attach=False,
            start_directory=cwd,
            window_name=window_name,
        )

    def kill_session(self, name: str) -> None:
        session = self.get_session(name)
        try:
            session.kill()
        except LibTmuxException as e:
            raise DmuxError(str(e).strip() or "kill-session failed") from e

    def kill_window(self, session_name: str, window_index: int) -> None:
        window = self._get_window(session_name, window_index)
        try:
            window.kill()
        except LibTmuxException as e:
            raise DmuxError(str(e).strip() or "kill-window failed") from e

    def kill_pane(self, pane_id: str) -> None:
        pane = self._find_pane(pane_id)
        try:
            pane.kill()
        except LibTmuxException as e:
            raise DmuxError(str(e).strip() or "kill-pane failed") from e

    def set_pane_style(
        self,
        pane_id: str,
        *,
        foreground: str | None = None,
        background: str | None = None,
    ) -> None:
        """Apply per-pane colours via ``set-option -p … window-style`` / ``window-active-style``.

        ``select-pane -P`` is unreliable with some libtmux call paths; subprocess matches the CLI.
        Terminal font cannot be set per pane (not a tmux feature).
        """
        self._find_pane(pane_id)

        def _token(kind: str, raw: str | None) -> str:
            if raw is None:
                return f"{kind}=default"
            s = raw.strip()
            if not s:
                return f"{kind}=default"
            if "," in s:
                raise DmuxError(
                    "Pane colours cannot contain commas (tmux style syntax). "
                    "Use #rrggbb / #rgb, a named colour, or colour0–255 — not rgb(r,g,b)."
                )
            return f"{kind}={s}"

        style = f"{_token('fg', foreground)},{_token('bg', background)}"
        for opt in ("window-style", "window-active-style"):
            self._tmux_cli_run(
                ["set-option", "-p", "-t", pane_id, opt, style],
            )

    def rename_session(self, old_name: str, new_name: str) -> None:
        session = self.get_session(old_name)
        session.rename_session(new_name)

    def attach_session(self, name: str) -> None:
        self.get_session(name)
        cmd = ["tmux", "attach-session", "-t", name]
        if self._socket_path:
            cmd[1:1] = ["-S", self._socket_path]
        os.execvp("tmux", cmd)

    def switch_client_session(self, name: str) -> None:
        session = self.get_session(name)
        session.switch_client()

    def new_window(
        self,
        session_name: str,
        *,
        name: str | None = None,
        cwd: str | None = None,
    ) -> Window:
        session = self.get_session(session_name)
        return session.new_window(window_name=name, start_directory=cwd)

    def select_window(self, session_name: str, index: int) -> None:
        session = self.get_session(session_name)
        if index < 0 or index >= len(session.windows):
            raise WindowNotFoundError(f"{session_name}:{index}")
        session.windows[index].select()

    def select_pane(self, session_name: str, window_index: int, pane_index: int) -> None:
        window = self._get_window(session_name, window_index)
        if pane_index < 0 or pane_index >= len(window.panes):
            raise PaneNotFoundError(f"{session_name}:{window_index}:{pane_index}")
        window.panes[pane_index].select()

    def select_pane_by_id(self, pane_id: str) -> None:
        self._find_pane(pane_id).select()

    def split_pane(
        self,
        pane_id: str,
        *,
        vertical: bool = True,
        cwd: str | None = None,
    ) -> None:
        """Split pane: vertical=True is tmux -v (stacked); False is -h (side by side)."""
        pane = self._find_pane(pane_id)
        direction = PaneDirection.Below if vertical else PaneDirection.Right
        pane.split(attach=False, start_directory=cwd, direction=direction)

    def apply_layout(self, session_name: str, window_index: int, kind: LayoutKind) -> None:
        window = self._get_window(session_name, window_index)
        layout = _LAYOUT_MAP.get(kind, kind)
        window.select_layout(layout)

    def apply_layout_active_window(self, kind: LayoutKind) -> None:
        """Apply layout to the active window of an attached session (requires TMUX env)."""
        if not os.environ.get("TMUX"):
            raise DmuxError("Not running inside tmux; specify session and window explicitly.")
        layout = _LAYOUT_MAP.get(kind, kind)
        server = self.server()
        for session in server.sessions:
            if session.session_attached == "1":
                session.active_window.select_layout(layout)
                return
        raise DmuxError("No attached tmux session found for this client.")

    # ------------------------------------------------------------------
    # Snapshot capture
    # ------------------------------------------------------------------

    # Window options worth restoring (kept narrow on purpose so we don't write back
    # noise like `window-active-style` which is per-pane and pushed via select-pane).
    _SNAPSHOTTED_WINDOW_OPTIONS: tuple[str, ...] = (
        "synchronize-panes",
        "remain-on-exit",
        "main-pane-height",
        "main-pane-width",
        "automatic-rename",
        "allow-rename",
    )

    # Shells we never re-launch; their presence means the pane is "idle at a prompt".
    _SHELL_BINARIES: frozenset[str] = frozenset({
        "bash", "-bash", "zsh", "-zsh", "fish", "-fish", "sh", "-sh", "dash", "ksh",
        "tcsh", "csh", "nu", "xonsh", "elvish", "ash",
    })

    def capture_snapshot(
        self,
        label: str = "default",
        *,
        include_scrollback: bool = False,
        scrollback_lines: int = 2000,
        include_history: bool = False,
        history_lines: int = 200,
        use_resurrect: bool = False,
    ) -> Snapshot:
        """Capture the current tmux topology with optional rich pane state.

        Always captured per-pane (cheap, derived from the existing format strings):
            ``command``, ``cmdline`` (via ``ps``), ``pid``, ``title``, ``style_fg/bg``.
        Always captured per-window: a small whitelist of options
            (``synchronize-panes``, ``remain-on-exit``, ``main-pane-*``, …).

        Opt-in:
            ``include_scrollback`` stores up to ``scrollback_lines`` lines of
            ``capture-pane -peJ`` output (raw text, newest at the bottom).
            ``include_history`` keeps up to ``history_lines`` lines that look like
            commands (heuristic: a line that follows a prompt-ish suffix on the
            previous line). Useful for "what was I doing" reminders on restore.
            ``use_resurrect`` shells out to ``tmux-plugins/tmux-resurrect``'s
            ``scripts/save.sh`` (via ``tmux run-shell``) so the snapshot also
            captures per-pane processes, vim/neovim sessions, and (with the
            plugin's options) shell history. The resulting file path is
            stored in ``meta["resurrect_file"]`` so a later restore can
            point ``last`` at the correct file.
        """
        import time

        # Trigger resurrect FIRST so its capture sees the user's tmux state
        # exactly as it was before we start any read-side queries.
        resurrect_file: str | None = None
        if use_resurrect:
            from dmux.services import resurrect as _resurrect

            try:
                resurrect_file = str(
                    _resurrect.save(socket_path=self._socket_path).resolve()
                )
            except _resurrect.ResurrectError as e:
                # Bubble up — the caller asked for "save everything" and we
                # couldn't deliver. Better to fail loudly than to write a
                # snapshot that silently drops the rich payload.
                raise DmuxError(str(e)) from e

        sessions_out: list[SnapshotSession] = []
        for session in self.server().sessions:
            wins: list[SnapshotWindow] = []
            for wi, window in enumerate(session.windows):
                panes: list[SnapshotPane] = []
                for pi, pane in enumerate(window.panes):
                    panes.append(
                        self._snapshot_pane(
                            pi,
                            pane,
                            window,
                            include_scrollback=include_scrollback,
                            scrollback_lines=scrollback_lines,
                            include_history=include_history,
                            history_lines=history_lines,
                        )
                    )
                wins.append(
                    SnapshotWindow(
                        index=wi,
                        name=_txt(window.name, "window"),
                        layout_name=window.window_layout,
                        active=(window == session.active_window),
                        panes=tuple(panes),
                        options=self._snapshot_window_options(window),
                    )
                )
            sessions_out.append(
                SnapshotSession(name=_txt(session.session_name, "session"), windows=tuple(wins))
            )
        meta: dict[str, Any] = {
            "version": 2,
            "include_scrollback": bool(include_scrollback),
            "include_history": bool(include_history),
        }
        if resurrect_file:
            meta["resurrect_file"] = resurrect_file
            meta["use_resurrect"] = True
        return Snapshot(
            label=label,
            created_unix=time.time(),
            sessions=tuple(sessions_out),
            meta=meta,
        )

    def _snapshot_pane(
        self,
        pi: int,
        pane: Pane,
        window: Window,
        *,
        include_scrollback: bool,
        scrollback_lines: int,
        include_history: bool,
        history_lines: int,
    ) -> SnapshotPane:
        pid_raw = getattr(pane, "pane_pid", None)
        try:
            pid = int(str(pid_raw)) if pid_raw is not None else 0
        except (TypeError, ValueError):
            pid = 0
        cmdline: tuple[str, ...] = self._ps_cmdline(pid) if pid else ()
        fg, bg = self._read_pane_style(pane)

        scroll_text = ""
        history: tuple[str, ...] = ()
        if include_scrollback or include_history:
            scroll_text = self._capture_pane_text(
                _txt(pane.pane_id, ""), max_lines=max(scrollback_lines, history_lines, 0)
            )
        if include_history and scroll_text:
            history = tuple(_extract_command_lines(scroll_text)[-max(history_lines, 0):])
        if not include_scrollback:
            scroll_text = ""
        elif scrollback_lines > 0 and scroll_text:
            scroll_text = "\n".join(scroll_text.splitlines()[-scrollback_lines:])

        return SnapshotPane(
            index=pi,
            cwd=_txt(pane.pane_current_path, "."),
            width=_dim(pane.pane_width),
            height=_dim(pane.pane_height),
            active=(pane == window.active_pane),
            command=_txt(getattr(pane, "pane_current_command", ""), ""),
            cmdline=cmdline,
            pid=pid,
            title=_txt(pane.pane_title, ""),
            style_fg=fg,
            style_bg=bg,
            scrollback=scroll_text,
            history=history,
        )

    def _snapshot_window_options(self, window: Window) -> dict[str, str]:
        out: dict[str, str] = {}
        for opt in self._SNAPSHOTTED_WINDOW_OPTIONS:
            v = self._show_window_option_safe(window, opt)
            if v:
                out[opt] = v
        return out

    def _ps_cmdline(self, pid: int) -> tuple[str, ...]:
        if pid <= 0:
            return ()
        try:
            r = subprocess.run(
                ["ps", "-o", "args=", "-p", str(pid)],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ()
        if r.returncode != 0:
            return ()
        line = (r.stdout or "").strip().splitlines()
        if not line:
            return ()
        # Best-effort tokenisation; quoted args lose their quotes (intentional —
        # we only use this for display + opt-in re-launch where shells re-quote).
        return tuple(line[0].split())

    def _read_pane_style(self, pane: Pane) -> tuple[str, str]:
        """Best-effort read of pane fg/bg via ``show-options -p`` ``window-active-style``.

        Returns ``("", "")`` when the option is unset; restoring then leaves the
        pane on the global default. Format mirrors ``set_pane_style``'s tokens
        (``fg=… ,bg=…``).
        """
        target = _txt(pane.pane_id, "")
        if not target:
            return ("", "")
        try:
            cmd = self._tmux_cli_base() + [
                "show-options", "-p", "-vt", target, "window-active-style",
            ]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=4, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return ("", "")
        if r.returncode != 0:
            return ("", "")
        raw = (r.stdout or "").strip()
        if not raw or raw == "default":
            return ("", "")
        fg = bg = ""
        for token in (t.strip() for t in raw.split(",")):
            if token.startswith("fg="):
                v = token[3:].strip()
                fg = "" if v == "default" else v
            elif token.startswith("bg="):
                v = token[3:].strip()
                bg = "" if v == "default" else v
        return (fg, bg)

    def _capture_pane_text(self, pane_id: str, *, max_lines: int) -> str:
        """``capture-pane -peJ`` with ``-S -<n>`` start; safe to call on missing panes."""
        if not pane_id:
            return ""
        n = max(int(max_lines or 0), 0)
        argv = ["capture-pane", "-peJ", "-t", pane_id, "-p"]
        if n > 0:
            argv.extend(["-S", f"-{n}"])
        else:
            argv.extend(["-S", "-"])
        try:
            r = subprocess.run(
                self._tmux_cli_base() + argv,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ""
        if r.returncode != 0:
            return ""
        return r.stdout or ""

    def restore_snapshot(
        self,
        snapshot: Snapshot,
        *,
        kill_existing: bool = False,
        replay_scrollback: bool = False,
        relaunch_commands: bool = False,
        use_resurrect: bool = False,
    ) -> None:
        """Re-create sessions/windows/panes from ``snapshot``.

        ``replay_scrollback`` injects each pane's saved scrollback as a
        ``cat <<'__DMUX__'`` heredoc so the user sees the prior context above
        their fresh prompt. ``relaunch_commands`` re-runs the captured
        foreground cmdline (skipped for shells; see ``_SHELL_BINARIES``).

        ``use_resurrect`` short-circuits the structural rebuild: we point the
        plugin's ``last`` symlink at the snapshot's
        ``meta["resurrect_file"]`` (when present) and run
        ``scripts/restore.sh``, which re-creates sessions/windows/panes,
        re-launches captured processes, and (with options) restores pane
        contents and shell history. Falls back to the structural restore
        when no resurrect file is recorded.
        """
        if use_resurrect and snapshot.meta.get("resurrect_file"):
            from dmux.services import resurrect as _resurrect

            resurrect_path = Path(str(snapshot.meta["resurrect_file"]))
            # Only fail loudly on resurrect-specific failures the user can act on
            # (missing plugin, missing file). Falling back to structural restore
            # would silently lose the per-pane processes / pane contents the user
            # explicitly asked for, which is worse than a clear error message.
            if not _resurrect.is_installed():
                raise DmuxError(
                    "Restore requested via tmux-resurrect, but the plugin is not "
                    "installed on this machine. Open Plugins → Install, or "
                    "uncheck 'Restore via tmux-resurrect'."
                )
            if not resurrect_path.is_file():
                raise DmuxError(
                    f"Restore requested via tmux-resurrect, but the saved file "
                    f"is missing: {resurrect_path}. Uncheck 'Restore via "
                    "tmux-resurrect' to use the structural restore instead."
                )
            try:
                _resurrect.restore(resurrect_path, socket_path=self._socket_path)
                return
            except _resurrect.ResurrectError as e:
                raise DmuxError(str(e)) from e

        server = self.server()
        for sess in snapshot.sessions:
            if server.has_session(sess.name):
                if kill_existing:
                    self.kill_session(sess.name)
                else:
                    raise SessionExistsError(sess.name)

            if not sess.windows:
                server.new_session(session_name=sess.name, attach=False)
                continue

            first, *rest = sess.windows
            start = first.panes[0].cwd if first.panes else None
            session = server.new_session(
                session_name=sess.name,
                attach=False,
                window_name=first.name,
                start_directory=start,
            )
            w0 = session.windows[0]
            self._ensure_pane_count(w0, len(first.panes), first)
            self._apply_window(
                w0, first,
                replay_scrollback=replay_scrollback,
                relaunch_commands=relaunch_commands,
            )

            for extra in rest:
                nw = session.new_window(
                    window_name=extra.name,
                    start_directory=extra.panes[0].cwd if extra.panes else None,
                )
                self._ensure_pane_count(nw, len(extra.panes), extra)
                self._apply_window(
                    nw, extra,
                    replay_scrollback=replay_scrollback,
                    relaunch_commands=relaunch_commands,
                )

    def _apply_window(
        self,
        window: Window,
        snap: SnapshotWindow,
        *,
        replay_scrollback: bool,
        relaunch_commands: bool,
    ) -> None:
        if snap.layout_name:
            try:
                window.select_layout(snap.layout_name)
            except Exception:
                pass
        target = _txt(window.window_id, "")
        for opt, val in (snap.options or {}).items():
            if not target or not val:
                continue
            try:
                self._tmux_cli_run(["set-window-option", "-t", target, opt, val])
            except DmuxError:
                pass

        panes = list(window.panes)
        for i, pane in enumerate(panes):
            if i >= len(snap.panes):
                break
            psnap = snap.panes[i]
            pid = _txt(pane.pane_id, "")
            if not pid:
                continue
            if psnap.title:
                try:
                    self._tmux_cli_run(["select-pane", "-t", pid, "-T", psnap.title])
                except DmuxError:
                    pass
            if psnap.style_fg or psnap.style_bg:
                try:
                    self.set_pane_style(
                        pid,
                        foreground=psnap.style_fg or None,
                        background=psnap.style_bg or None,
                    )
                except DmuxError:
                    pass
            if replay_scrollback and psnap.scrollback:
                self._inject_scrollback(pid, psnap.scrollback)
            if relaunch_commands and self._cmdline_is_relaunchable(psnap):
                self._relaunch_command(pid, psnap.cmdline or (psnap.command,))

    def _cmdline_is_relaunchable(self, pane: SnapshotPane) -> bool:
        head = ""
        if pane.cmdline:
            head = pane.cmdline[0]
        elif pane.command:
            head = pane.command
        if not head:
            return False
        # Strip leading dash from login-shell argv0 (e.g. "-zsh") and any dir.
        base = head.split("/")[-1].lstrip("-")
        if not base:
            return False
        return base not in self._SHELL_BINARIES

    def _relaunch_command(self, pane_id: str, argv: tuple[str, ...]) -> None:
        text = " ".join(a for a in argv if a)
        if not text.strip():
            return
        try:
            self._tmux_cli_run(["send-keys", "-t", pane_id, text, "Enter"])
        except DmuxError:
            pass

    def _inject_scrollback(self, pane_id: str, scrollback: str) -> None:
        """Echo a saved scrollback into the pane via a heredoc-protected cat.

        Uses an unlikely sentinel so embedded ``EOF``/quoting in the captured
        text can't terminate the heredoc prematurely.
        """
        if not scrollback.strip():
            return
        sentinel = "DMUX_SNAPSHOT_REPLAY__"
        if sentinel in scrollback:
            return  # extremely unlikely; bail rather than mangle output
        prefix = "# ── dmux snapshot replay ──"
        body = f"cat <<'{sentinel}'\n{prefix}\n{scrollback.rstrip()}\n{sentinel}"
        try:
            # ``-l`` sends literal characters so quotes/heredocs survive intact.
            self._tmux_cli_run(["send-keys", "-t", pane_id, "-l", body])
            self._tmux_cli_run(["send-keys", "-t", pane_id, "Enter"])
        except DmuxError:
            pass

    def _ensure_pane_count(self, window: Window, count: int, snap: SnapshotWindow) -> None:
        if count <= 0:
            return
        panes = list(window.panes)
        for i in range(1, count):
            base = panes[i - 1]
            cwd = snap.panes[i].cwd if i < len(snap.panes) else snap.panes[-1].cwd
            vertical = (i % 2) == 1
            direction = PaneDirection.Below if vertical else PaneDirection.Right
            new_pane = base.split(attach=False, start_directory=cwd, direction=direction)
            panes.append(new_pane)

    def _get_window(self, session_name: str, window_index: int) -> Window:
        session = self.get_session(session_name)
        if window_index < 0 or window_index >= len(session.windows):
            raise WindowNotFoundError(f"{session_name}:{window_index}")
        return session.windows[window_index]

    def _find_pane(self, pane_id: str) -> Pane:
        for session in self.server().sessions:
            for window in session.windows:
                for pane in window.panes:
                    if pane.pane_id == pane_id:
                        return pane
        raise PaneNotFoundError(pane_id)

    def _session_to_dto(self, session: Session) -> SessionDTO:
        wins: list[WindowDTO] = []
        for window in session.windows:
            panes: list[PaneDTO] = []
            for pane in window.panes:
                pid_raw = getattr(pane, "pane_pid", None)
                try:
                    pid_int = int(str(pid_raw)) if pid_raw is not None else 0
                except (TypeError, ValueError):
                    pid_int = 0
                panes.append(
                    PaneDTO(
                        pane_id=_txt(pane.pane_id),
                        window_id=_txt(pane.window_id),
                        session_name=_txt(session.session_name),
                        index=_dim(pane.pane_index),
                        title=_txt(pane.pane_title, ""),
                        cwd=_txt(pane.pane_current_path, "."),
                        active=(pane == window.active_pane),
                        width=_dim(pane.pane_width),
                        height=_dim(pane.pane_height),
                        left=_dim(pane.pane_left),
                        top=_dim(pane.pane_top),
                        command=_txt(getattr(pane, "pane_current_command", ""), ""),
                        pid=pid_int,
                    )
                )
            zoomed_raw = getattr(window, "window_zoomed_flag", "0")
            sync_raw = self._show_window_option_safe(window, "synchronize-panes")
            wins.append(
                WindowDTO(
                    window_id=_txt(window.window_id),
                    session_name=_txt(session.session_name),
                    index=_dim(window.index),
                    name=_txt(window.name, "window"),
                    active=(window == session.active_window),
                    layout_name=window.window_layout,
                    panes=tuple(panes),
                    zoomed=str(zoomed_raw or "0") == "1",
                    synchronized=str(sync_raw or "off").lower() in {"on", "1"},
                )
            )
        return SessionDTO(
            session_id=_txt(session.session_id),
            name=_txt(session.session_name, "session"),
            attached=session.session_attached != "0",
            windows=tuple(wins),
        )

    def _show_window_option_safe(self, window: Window, option: str) -> str:
        """Return the value of a window-scoped tmux option, or "" on any error.

        We intentionally swallow errors because list-sessions polls this, and
        a missing option must never break the response.
        """
        target = _txt(window.window_id, "")
        if not target:
            return ""
        try:
            cmd = self._tmux_cli_base() + ["show-window", "-vt", target, option]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=4, check=False)
            if r.returncode != 0:
                return ""
            return (r.stdout or "").strip()
        except (subprocess.TimeoutExpired, OSError, DmuxError):
            return ""

    # ------------------------------------------------------------------
    # Pane / window mutation helpers (used by the JSON API)
    # ------------------------------------------------------------------

    def send_keys(
        self,
        pane_id: str,
        text: str,
        *,
        enter: bool = True,
        literal: bool = False,
    ) -> None:
        """Type ``text`` into ``pane_id`` (``send-keys``).

        ``literal`` skips tmux's key-name lookup (useful for ``C-c`` etc. when
        the caller wants the literal characters; default is to interpret tmux
        names so callers can send things like ``Enter`` or ``C-c``).
        """
        self._find_pane(pane_id)
        argv: list[str] = ["send-keys", "-t", pane_id]
        if literal:
            argv.append("-l")
        argv.append(text)
        self._tmux_cli_run(argv)
        if enter and not literal:
            self._tmux_cli_run(["send-keys", "-t", pane_id, "Enter"])

    def capture_pane(self, pane_id: str, *, lines: int = 200) -> str:
        """Return the visible (or scrolled-back) text of a pane.

        ``lines`` limits how many *trailing* lines to return; tmux reports
        the whole history when ``-S - -E -`` is passed.
        """
        self._find_pane(pane_id)
        cmd = self._tmux_cli_base() + ["capture-pane", "-pJ", "-t", pane_id, "-S", "-"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
        if r.returncode != 0:
            msg = (r.stderr or r.stdout or "capture-pane failed").strip()
            raise DmuxError(msg)
        text = r.stdout or ""
        if lines > 0:
            kept = text.splitlines()[-lines:]
            return "\n".join(kept)
        return text

    def rename_window(self, session_name: str, window_index: int, new_name: str) -> None:
        if not new_name.strip():
            raise DmuxError("window name must not be empty")
        window = self._get_window(session_name, window_index)
        try:
            window.rename_window(new_name.strip())
        except LibTmuxException as e:
            raise DmuxError(str(e).strip() or "rename-window failed") from e

    def move_window(self, session_name: str, window_index: int, *, direction: str) -> int:
        """Swap a window with its left/right neighbour. Returns the new index."""
        session = self.get_session(session_name)
        windows = list(session.windows)
        n = len(windows)
        if window_index < 0 or window_index >= n:
            raise WindowNotFoundError(f"{session_name}:{window_index}")
        if direction not in {"left", "right"}:
            raise DmuxError("direction must be 'left' or 'right'")
        target = window_index - 1 if direction == "left" else window_index + 1
        if target < 0 or target >= n:
            return window_index
        src = windows[window_index]
        dst = windows[target]
        # tmux swap-window swaps two windows in place.
        src_id = _txt(src.window_id)
        dst_id = _txt(dst.window_id)
        if not src_id or not dst_id:
            raise DmuxError("missing window ids; cannot swap")
        self._tmux_cli_run(["swap-window", "-d", "-s", src_id, "-t", dst_id])
        return target

    def break_pane(self, pane_id: str) -> None:
        """Move a pane into its own new window (``break-pane``)."""
        self._find_pane(pane_id)
        self._tmux_cli_run(["break-pane", "-d", "-s", pane_id])

    def toggle_zoom(self, pane_id: str) -> None:
        """Toggle the ``window-zoomed-flag`` for the pane's window."""
        self._find_pane(pane_id)
        self._tmux_cli_run(["resize-pane", "-Z", "-t", pane_id])

    def swap_pane(self, pane_id: str, *, direction: str) -> None:
        """Swap a pane with the previous/next pane in its window."""
        if direction not in {"up", "down"}:
            raise DmuxError("direction must be 'up' or 'down'")
        self._find_pane(pane_id)
        flag = "-U" if direction == "up" else "-D"
        self._tmux_cli_run(["swap-pane", flag, "-t", pane_id])

    def resize_pane(
        self,
        pane_id: str,
        *,
        delta_x: int = 0,
        delta_y: int = 0,
    ) -> None:
        """Apply ``resize-pane`` with directional cell deltas.

        Positive ``delta_x`` widens to the right, positive ``delta_y`` grows
        downward. Each axis maps to one ``resize-pane`` invocation.
        """
        self._find_pane(pane_id)
        if delta_x:
            flag = "-R" if delta_x > 0 else "-L"
            self._tmux_cli_run(["resize-pane", "-t", pane_id, flag, str(abs(delta_x))])
        if delta_y:
            flag = "-D" if delta_y > 0 else "-U"
            self._tmux_cli_run(["resize-pane", "-t", pane_id, flag, str(abs(delta_y))])

    def set_window_synchronize(
        self,
        session_name: str,
        window_index: int,
        *,
        on: bool,
    ) -> None:
        window = self._get_window(session_name, window_index)
        target = _txt(window.window_id, "")
        if not target:
            raise DmuxError("missing window id")
        self._tmux_cli_run(
            ["set-window-option", "-t", target, "synchronize-panes", "on" if on else "off"]
        )

    def kill_other_panes(self, pane_id: str) -> None:
        """``kill-pane -a`` — kill all other panes in the window."""
        self._find_pane(pane_id)
        self._tmux_cli_run(["kill-pane", "-a", "-t", pane_id])

    def server_info(self) -> dict[str, str | None]:
        """Lightweight server fingerprint for the topbar / debugging panel."""
        version = ""
        try:
            cmd = self._tmux_cli_base() + ["-V"]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=4, check=False)
            if r.returncode == 0:
                version = (r.stdout or "").strip()
        except (subprocess.TimeoutExpired, OSError):
            version = ""
        sessions = 0
        clients = 0
        try:
            sessions = len(list(self.server().sessions))
            cmd2 = self._tmux_cli_base() + ["list-clients", "-F", "#{client_name}"]
            r2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=4, check=False)
            if r2.returncode == 0:
                clients = len([s for s in (r2.stdout or "").splitlines() if s.strip()])
        except (subprocess.TimeoutExpired, OSError, LibTmuxException):
            pass
        return {
            "version": version,
            "socket_path": self._socket_path,
            "sessions": str(sessions),
            "clients": str(clients),
        }
