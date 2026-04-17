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
    return {
        "index": w.index,
        "name": w.name,
        "layout_name": w.layout_name,
        "active": w.active,
        "panes": [_pane_to_dict(p) for p in w.panes],
    }


def _pane_to_dict(p: SnapshotPane) -> dict[str, Any]:
    return {
        "index": p.index,
        "cwd": p.cwd,
        "width": p.width,
        "height": p.height,
        "active": p.active,
    }


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
    return SnapshotWindow(
        index=int(obj["index"]),
        name=str(obj["name"]),
        layout_name=obj.get("layout_name"),
        active=bool(obj.get("active", False)),
        panes=panes,
    )


def _pane_from_dict(obj: dict[str, Any]) -> SnapshotPane:
    return SnapshotPane(
        index=int(obj["index"]),
        cwd=str(obj["cwd"]),
        width=int(obj.get("width", 80)),
        height=int(obj.get("height", 24)),
        active=bool(obj.get("active", False)),
    )
