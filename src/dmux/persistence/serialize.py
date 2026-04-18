"""JSON serialization for snapshot DTOs."""

from __future__ import annotations

import json
from typing import Any

from dmux.schemas import Snapshot, SnapshotPane, SnapshotSession, SnapshotWindow


def snapshot_to_json(snapshot: Snapshot) -> str:
    return json.dumps(_snapshot_to_dict(snapshot), indent=2, sort_keys=True)


def snapshot_from_json(data: str) -> Snapshot:
    return _snapshot_from_dict(json.loads(data))


def snapshot_from_dict(obj: dict[str, Any]) -> Snapshot:
    return _snapshot_from_dict(obj)


def _snapshot_to_dict(s: Snapshot) -> dict[str, Any]:
    return {
        "label": s.label,
        "created_unix": s.created_unix,
        "meta": dict(s.meta),
        "sessions": [_session_to_dict(x) for x in s.sessions],
    }


def _session_to_dict(s: SnapshotSession) -> dict[str, Any]:
    return {
        "name": s.name,
        "windows": [_window_to_dict(w) for w in s.windows],
    }


def _window_to_dict(w: SnapshotWindow) -> dict[str, Any]:
    out: dict[str, Any] = {
        "index": w.index,
        "name": w.name,
        "layout_name": w.layout_name,
        "active": w.active,
        "panes": [_pane_to_dict(p) for p in w.panes],
    }
    if w.options:
        out["options"] = dict(w.options)
    return out


def _pane_to_dict(p: SnapshotPane) -> dict[str, Any]:
    out: dict[str, Any] = {
        "index": p.index,
        "cwd": p.cwd,
        "width": p.width,
        "height": p.height,
        "active": p.active,
    }
    # Only emit rich fields when set, so v1 round-trips stay byte-identical.
    if p.command:
        out["command"] = p.command
    if p.cmdline:
        out["cmdline"] = list(p.cmdline)
    if p.pid:
        out["pid"] = p.pid
    if p.title:
        out["title"] = p.title
    if p.style_fg:
        out["style_fg"] = p.style_fg
    if p.style_bg:
        out["style_bg"] = p.style_bg
    if p.scrollback:
        out["scrollback"] = p.scrollback
    if p.history:
        out["history"] = list(p.history)
    return out


def _snapshot_from_dict(obj: dict[str, Any]) -> Snapshot:
    sessions = tuple(_session_from_dict(s) for s in obj.get("sessions", []))
    return Snapshot(
        label=str(obj.get("label", "default")),
        created_unix=float(obj["created_unix"]),
        sessions=sessions,
        meta=dict(obj.get("meta", {})),
    )


def _session_from_dict(obj: dict[str, Any]) -> SnapshotSession:
    wins = tuple(_window_from_dict(w) for w in obj.get("windows", []))
    return SnapshotSession(name=str(obj["name"]), windows=wins)


def _window_from_dict(obj: dict[str, Any]) -> SnapshotWindow:
    panes = tuple(_pane_from_dict(p) for p in obj.get("panes", []))
    raw_opts = obj.get("options")
    options: dict[str, str] = {}
    if isinstance(raw_opts, dict):
        for k, v in raw_opts.items():
            if isinstance(k, str):
                options[k] = "" if v is None else str(v)
    return SnapshotWindow(
        index=int(obj["index"]),
        name=str(obj["name"]),
        layout_name=obj.get("layout_name"),
        active=bool(obj.get("active", False)),
        panes=panes,
        options=options,
    )


def _str_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, list | tuple):
        return tuple(str(x) for x in value)
    return ()


def _pane_from_dict(obj: dict[str, Any]) -> SnapshotPane:
    pid_raw = obj.get("pid", 0)
    try:
        pid = int(pid_raw) if pid_raw is not None else 0
    except (TypeError, ValueError):
        pid = 0
    return SnapshotPane(
        index=int(obj["index"]),
        cwd=str(obj["cwd"]),
        width=int(obj.get("width", 80)),
        height=int(obj.get("height", 24)),
        active=bool(obj.get("active", False)),
        command=str(obj.get("command", "") or ""),
        cmdline=_str_tuple(obj.get("cmdline")),
        pid=pid,
        title=str(obj.get("title", "") or ""),
        style_fg=str(obj.get("style_fg", "") or ""),
        style_bg=str(obj.get("style_bg", "") or ""),
        scrollback=str(obj.get("scrollback", "") or ""),
        history=_str_tuple(obj.get("history")),
    )
