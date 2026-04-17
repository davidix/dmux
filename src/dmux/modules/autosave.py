"""Periodic snapshot autosave (continuum-like)."""

from __future__ import annotations

import os
import signal
import sys
import time

from dmux.paths import autosave_pid_path, dmux_data_dir
from dmux.persistence.state_manager import StateManager
from dmux.services.tmux_service import TmuxService


def run_daemon(interval_sec: int = 300, label: str = "autosave") -> None:
    """Run in foreground; intended to be backgrounded by the shell or process manager."""
    dmux_data_dir().mkdir(parents=True, exist_ok=True)
    pid_file = autosave_pid_path()
    pid_file.write_text(str(os.getpid()), encoding="utf-8")
    state = StateManager()
    tmux = TmuxService()

    def _stop(*_args: object) -> None:
        if pid_file.exists():
            pid_file.unlink(missing_ok=True)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    while True:
        try:
            snap = tmux.capture_snapshot(label=label)
            state.save_snapshot(snap, is_auto=True)
            state.set_meta("last_autosave_unix", str(time.time()))
        except Exception:
            pass
        time.sleep(max(30, interval_sec))


def write_pid_stub() -> None:
    """Reserve PID path parent."""
    dmux_data_dir().mkdir(parents=True, exist_ok=True)
