"""tmux buffer helpers (yank-like)."""

from __future__ import annotations

import subprocess
from typing import Literal


def show_buffer(*, socket_path: str | None = None) -> str:
    cmd = _base_cmd(socket_path) + ["show-buffer", "-b", "0"]
    proc = subprocess.run(cmd, text=True, capture_output=True)
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd, proc.stdout, proc.stderr)
    return proc.stdout


def copy_to_clipboard_os(
    text: str, *, target: Literal["clipboard", "primary"] = "clipboard"
) -> None:
    """Best-effort copy using pbcopy (macOS), xclip, or wl-copy."""
    import shutil

    if sys_pbcopy := shutil.which("pbcopy"):
        subprocess.run([sys_pbcopy], input=text, text=True, check=False)
        return
    if sys_xclip := shutil.which("xclip"):
        sel = "primary" if target == "primary" else "clipboard"
        subprocess.run([sys_xclip, "-selection", sel], input=text, text=True, check=False)
        return
    if sys_wl := shutil.which("wl-copy"):
        subprocess.run([sys_wl], input=text, text=True, check=False)


def buffer_to_clipboard(*, socket_path: str | None = None) -> str:
    buf = show_buffer(socket_path=socket_path)
    copy_to_clipboard_os(buf)
    return buf


def _base_cmd(socket_path: str | None) -> list[str]:
    if socket_path:
        return ["tmux", "-S", socket_path]
    return ["tmux"]
