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


@dataclass(frozen=True, slots=True)
class WindowDTO:
    window_id: str
    session_name: str
    index: int
    name: str
    active: bool
    layout_name: str | None
    panes: tuple[PaneDTO, ...]


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


@dataclass(frozen=True, slots=True)
class SnapshotWindow:
    index: int
    name: str
    layout_name: str | None
    active: bool
    panes: tuple[SnapshotPane, ...]


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
