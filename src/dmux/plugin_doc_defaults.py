"""Default tmux option lines for TPM plugins (curated + README code fences).

When rendering ``plugins.tmux``, dmux inserts ``set -g @…`` lines taken from:

1. :file:`data/plugin_defaults.json` when present for that ``user/repo`` (curated).
2. Otherwise, fenced code blocks in the GitHub README (`` ```tmux`` / `` ``` ``).

Lines must look like plugin options: ``set -g @name-…`` (``@plugin`` lines are skipped).
"""

from __future__ import annotations

import json
import re
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from dmux.services.github_plugin_help import fetch_readme_markdown

_PACKAGE_DIR = Path(__file__).resolve().parent
_DEFAULTS_PATH = _PACKAGE_DIR / "data" / "plugin_defaults.json"

_README_LINES_CACHE: dict[str, tuple[float, tuple[str, ...]]] = {}
_README_TTL_SEC = 86400.0

_FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)


def _load_curated() -> dict[str, Any]:
    if not _DEFAULTS_PATH.is_file():
        return {}
    try:
        raw = json.loads(_DEFAULTS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


@lru_cache(maxsize=1)
def _curated_map() -> dict[str, Any]:
    return _load_curated()


def _is_plugin_option_line(line: str) -> bool:
    s = line.strip()
    if not s or s.startswith("#"):
        return False
    if "$(" in s or "`" in s:
        return False
    if re.match(r"set\s+-g\s+@plugin\s+", s):
        return False
    if re.match(r"set\s+-g\s+@", s):
        return True
    if re.match(r"set-option\s+-g\s+@", s):
        return True
    return False


def extract_tmux_option_lines_from_readme(markdown: str) -> list[str]:
    """Collect ``set -g @…`` lines from fenced README blocks (order preserved)."""
    seen: set[str] = set()
    out: list[str] = []
    for m in _FENCE.finditer(markdown):
        block = m.group(1)
        for raw in block.splitlines():
            s = raw.strip()
            if not _is_plugin_option_line(s):
                continue
            if s in seen:
                continue
            seen.add(s)
            out.append(s)
            if len(out) >= 36:
                return out
    return out


def tmux_option_lines_for_plugin(spec: str) -> list[str]:
    """Return extra tmux lines to place after ``set -g @plugin 'spec'``."""
    spec = spec.strip()
    curated = _curated_map().get(spec)
    if isinstance(curated, dict) and curated.get("lines"):
        return [str(x) for x in curated["lines"] if isinstance(x, str)]
    if isinstance(curated, dict) and curated.get("skip_readme") is True:
        return []

    now = time.monotonic()
    if spec in _README_LINES_CACHE:
        ts, cached = _README_LINES_CACHE[spec]
        if now - ts < _README_TTL_SEC:
            return list(cached)

    md = fetch_readme_markdown(spec)
    lines: tuple[str, ...] = ()
    if md:
        lines = tuple(extract_tmux_option_lines_from_readme(md))
    _README_LINES_CACHE[spec] = (now, lines)
    return list(lines)
