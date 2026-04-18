"""SQLite-backed persistence for tmux snapshots."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from dmux.exceptions import SnapshotIdNotFoundError, SnapshotNotFoundError
from dmux.paths import database_path, dmux_data_dir
from dmux.persistence.serialize import snapshot_from_dict, snapshot_to_json
from dmux.schemas import Snapshot


def _snapshot_payload_summary(payload: str) -> dict[str, Any]:
    """Lightweight counts from stored JSON (no full Snapshot validation)."""
    try:
        d = json.loads(payload)
    except json.JSONDecodeError:
        return {
            "session_count": 0,
            "window_count": 0,
            "pane_count": 0,
            "session_names": [],
            "parse_error": True,
        }
    raw_sessions = d.get("sessions")
    sessions = raw_sessions if isinstance(raw_sessions, list) else []
    names: list[str] = []
    window_count = 0
    pane_count = 0
    for s in sessions:
        if not isinstance(s, dict):
            continue
        names.append(str(s.get("name", "")).strip() or "(unnamed)")
        wins = s.get("windows")
        if not isinstance(wins, list):
            continue
        window_count += len(wins)
        for w in wins:
            if not isinstance(w, dict):
                continue
            panes = w.get("panes")
            if isinstance(panes, list):
                pane_count += len(panes)
    return {
        "session_count": len([s for s in sessions if isinstance(s, dict)]),
        "window_count": window_count,
        "pane_count": pane_count,
        "session_names": names,
    }


class StateManager:
    """Stores labeled snapshots and optional autosave metadata."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or database_path()
        self._lock = threading.Lock()
        dmux_data_dir().mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    label TEXT NOT NULL,
                    created_unix REAL NOT NULL,
                    payload TEXT NOT NULL,
                    is_auto INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_snapshots_label_created
                ON snapshots (label, created_unix DESC)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )

    def save_snapshot(self, snapshot: Snapshot, *, is_auto: bool = False) -> int:
        payload = snapshot_to_json(snapshot)
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO snapshots (label, created_unix, payload, is_auto) VALUES (?, ?, ?, ?)",
                (snapshot.label, snapshot.created_unix, payload, int(is_auto)),
            )
            conn.commit()
            rid = cur.lastrowid
            return int(rid) if rid is not None else 0

    def load_latest(self, label: str = "default") -> Snapshot:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload FROM snapshots
                WHERE label = ?
                ORDER BY created_unix DESC
                LIMIT 1
                """,
                (label,),
            ).fetchone()
        if row is None:
            raise SnapshotNotFoundError(label)
        return snapshot_from_dict(json.loads(row["payload"]))

    def load_by_id(self, snapshot_id: int) -> Snapshot:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM snapshots WHERE id = ?",
                (snapshot_id,),
            ).fetchone()
        if row is None:
            raise SnapshotIdNotFoundError(snapshot_id)
        return snapshot_from_dict(json.loads(row["payload"]))

    def list_snapshots(self, label: str | None = None) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            if label is None:
                rows = conn.execute(
                    """
                    SELECT id, label, created_unix, is_auto, payload
                    FROM snapshots
                    ORDER BY created_unix DESC
                    """
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, label, created_unix, is_auto, payload
                    FROM snapshots
                    WHERE label = ?
                    ORDER BY created_unix DESC
                    """,
                    (label,),
                ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            payload = str(d.pop("payload"))
            d["summary"] = _snapshot_payload_summary(payload)
            out.append(d)
        return out

    def delete_snapshot(self, snapshot_id: int) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM snapshots WHERE id = ?", (snapshot_id,))
            conn.commit()
            return bool(getattr(cur, "rowcount", 0))

    def set_meta(self, key: str, value: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO meta (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )
            conn.commit()

    def get_meta(self, key: str) -> str | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row["value"])
