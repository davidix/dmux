"""Typed DTOs for tmux state, API, and persistence."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class PaneDTO:
    pane_id: str
    window_id: str
    session_name: str
    index: int
    title: str
    cwd: str
    active: bool
    width: int
    height: int
    left: int  # tmux #{pane_left}, character column of top-left cell
    top: int  # tmux #{pane_top}, character row of top-left cell
    command: str = ""  # tmux #{pane_current_command} (foreground process)
    pid: int = 0  # tmux #{pane_pid}


@dataclass(frozen=True, slots=True)
class WindowDTO:
    window_id: str
    session_name: str
    index: int
    name: str
    active: bool
    layout_name: str | None
    panes: tuple[PaneDTO, ...]
    zoomed: bool = False  # tmux window-zoomed-flag
    synchronized: bool = False  # tmux window option synchronize-panes


@dataclass(frozen=True, slots=True)
class SessionDTO:
    session_id: str
    name: str
    attached: bool
    windows: tuple[WindowDTO, ...]


@dataclass(frozen=True, slots=True)
class SnapshotPane:
    index: int
    cwd: str
    width: int
    height: int
    active: bool
    # Rich state (all optional; default empty for backwards compat with v1 payloads).
    command: str = ""  # tmux #{pane_current_command} (e.g. "vim", "zsh")
    cmdline: tuple[str, ...] = ()  # ps -o args= for the foreground pid (full argv)
    pid: int = 0  # tmux #{pane_pid}
    title: str = ""  # tmux #{pane_title}
    style_fg: str = ""  # foreground colour (best-effort, may be empty)
    style_bg: str = ""  # background colour (best-effort, may be empty)
    scrollback: str = ""  # raw visible text (newest at the bottom; opt-in)
    history: tuple[str, ...] = ()  # heuristically-extracted command lines (opt-in)


@dataclass(frozen=True, slots=True)
class SnapshotWindow:
    index: int
    name: str
    layout_name: str | None
    active: bool
    panes: tuple[SnapshotPane, ...]
    options: dict[str, str] = field(default_factory=dict)  # window-scoped tmux options


@dataclass(frozen=True, slots=True)
class SnapshotSession:
    name: str
    windows: tuple[SnapshotWindow, ...]


@dataclass(frozen=True, slots=True)
class Snapshot:
    label: str
    created_unix: float
    sessions: tuple[SnapshotSession, ...]
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FuzzyMatch:
    kind: str  # "session" | "window" | "pane"
    session_name: str
    window_name: str | None
    pane_title: str | None
    score: float
    pane_id: str | None = None
    window_id: str | None = None
