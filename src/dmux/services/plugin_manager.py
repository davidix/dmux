"""Tmux Plugin Manager (TPM) integration — vendored copy + ~/.config/dmux/plugins.tmux."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from functools import lru_cache
from pathlib import Path

from dmux import ssl_fetch
from dmux.exceptions import PluginManagerError
from dmux.plugin_doc_defaults import tmux_option_lines_for_plugin

# TPM ships inside the dmux package (clone or git submodule → src/dmux/vendor/tpm).
_PKG_ROOT = Path(__file__).resolve().parent.parent
TPM_ROOT = _PKG_ROOT / "vendor" / "tpm"

MARKER_BEGIN = "# >>> dmux: tpm plugins fragment >>>"
MARKER_END = "# <<< dmux: tpm plugins fragment <<<"

# Most plugin options go after `run tpm`; Freed-Wu status lines go before it (see _render_fragment).
_OPTS_MARKER_PREFIX = "# dmux:opts:"

# Freed-Wu's status-bar.tmux runs during `run tpm` and reads status-left/right then; those
# `set -g status-*` lines must appear *before* `run tpm` in plugins.tmux.
FREED_WU_STATUS_BAR_SPEC = "Freed-Wu/tmux-status-bar"

# GitHub-style user/repo or full git URL (TPM-supported forms, simplified).
_PLUGIN_RE = re.compile(r"^(?:https?://|git@|ssh://).+$|^[^/\s]+/[^/\s]+(?:#[^\s]+)?$")

# GitHub: tmux-plugins org (official TPM plugin index). Cached in-process.
# Value: (monotonic_ts, plugin_specs, optional_error_hint_when_degraded).
_CATALOG_CACHE: tuple[float, tuple[str, ...], str | None] | None = None
_CATALOG_TTL_SEC = 3600.0
_GITHUB_ORG_API = "https://api.github.com/orgs/tmux-plugins/repos"

# Awesome list catalog (tmux-plugins/list mirror, ships with the package).
_AWESOME_CATALOG_PATH = _PKG_ROOT / "data" / "awesome_tmux_plugins.json"
AWESOME_CATALOG_SOURCE_URL = "https://github.com/tmux-plugins/list"


@lru_cache(maxsize=1)
def _awesome_catalog_entries() -> tuple[dict[str, str], ...]:
    """Curated list of `{spec, category, description}` entries from the awesome list."""
    try:
        raw = json.loads(_AWESOME_CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    plugins = raw.get("plugins") if isinstance(raw, dict) else None
    if not isinstance(plugins, list):
        return ()
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in plugins:
        if not isinstance(item, dict):
            continue
        spec = str(item.get("spec", "")).strip()
        if not spec or spec in seen:
            continue
        seen.add(spec)
        out.append(
            {
                "spec": spec,
                "category": str(item.get("category", "")).strip() or "Other",
                "description": str(item.get("description", "")).strip(),
            }
        )
    return tuple(out)


def _awesome_catalog_specs() -> tuple[str, ...]:
    return tuple(e["spec"] for e in _awesome_catalog_entries())


# Used when GitHub is unreachable (offline, rate limit, firewall). Always available — sourced from
# the bundled awesome list, plus a few historical extras for backward compatibility.
_EXTRA_FALLBACK: tuple[str, ...] = (
    "tmux-plugins/tmux-example-plugin",
    "tmux-plugins/tmux-super-fingers",
    "tmux-plugins/vim-tmux-focus-events",
)


def _fallback_catalog_specs() -> tuple[str, ...]:
    base = _awesome_catalog_specs()
    if not base:
        return _EXTRA_FALLBACK
    return tuple(sorted(set(base) | set(_EXTRA_FALLBACK)))


# Back-compat alias (older imports/tests). Kept as a property-like callable result.
_FALLBACK_TMUX_PLUGINS: tuple[str, ...] = _fallback_catalog_specs()


def _xdg_config() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))


def plugins_fragment_path() -> Path:
    """Managed TPM fragment: list of set -g @plugin lines + run tpm."""
    return _xdg_config() / "dmux" / "plugins.tmux"


def resolved_plugins_fragment_path() -> str:
    """Absolute path string for the fragment file (same target as GET/PUT ``/api/v1/plugins/fragment``)."""
    return _path_for_api(plugins_fragment_path())


def _path_for_api(p: Path) -> str:
    """Absolute, resolved path for UI / errors (symlinks expanded when possible)."""
    try:
        return str(p.expanduser().resolve())
    except OSError:
        return str(p.expanduser())


# Restrictive mode for config dirs we create under the user home (best-effort chmod on Unix).
_CONFIG_DIR_MODE = 0o700


def _mkdir_parent_chain(path: Path) -> None:
    """Ensure parent directory of ``path`` exists; propagate permission errors clearly."""
    parent = path.parent
    try:
        parent.mkdir(parents=True, mode=_CONFIG_DIR_MODE, exist_ok=True)
    except OSError as e:
        raise PluginManagerError(
            f"Cannot create directory {parent} (needed for {path.name}): {e}. "
            "Fix ownership or permissions on parent folders (e.g. chmod u+rwx)."
        ) from e


def _read_user_tmux_conf_text(conf: Path) -> str:
    """Read tmux user config; uses UTF-8 with BOM stripped so marker lines match."""
    return conf.read_text(encoding="utf-8-sig", errors="replace")


def _write_config_file(path: Path, content: str, *, what: str) -> None:
    """Write UTF-8 text to a user config file with clear permission errors."""
    _mkdir_parent_chain(path)
    try:
        path.write_text(content, encoding="utf-8")
    except PermissionError as e:
        raise PluginManagerError(
            f"Permission denied writing {what} at {_path_for_api(path)}. "
            "Run dmux as the user who owns this file, or fix ACLs: "
            f"chmod u+rw {_path_for_api(path)} && chmod u+rwx {_path_for_api(path.parent)}"
        ) from e
    except OSError as e:
        raise PluginManagerError(
            f"Cannot write {what} at {_path_for_api(path)}: {e}"
        ) from e


def user_tmux_conf_path() -> Path:
    """File dmux appends the bootstrap ``source-file`` hook to.

    Resolution:

    1. ``DMUX_TMUX_CONF`` — if set, use this path (expand ``~``). Use this to write the hook
       to e.g. ``~/.config/tmux/tmux.conf`` when you rely on the XDG config file.
    2. Otherwise **``~/.tmux.conf``** in the process user's home directory.

    tmux itself may load ``~/.config/tmux/tmux.conf`` first when that file exists; if you use
    both files, ensure the dmux hook lives in the config file tmux actually reads, or set
    ``DMUX_TMUX_CONF`` to that path.
    """
    override = (os.environ.get("DMUX_TMUX_CONF") or "").strip()
    if override:
        return Path(override).expanduser()
    # Explicit ~/.tmux.conf (same as shell path) — not Path.home()/".tmux.conf" alone, so HOME quirks match `expanduser`.
    return Path(os.path.expanduser("~/.tmux.conf")).resolve(strict=False)


def resolved_user_tmux_conf_path() -> str:
    """Absolute path string for the tmux config file dmux bootstraps (for API / toasts)."""
    return _path_for_api(user_tmux_conf_path())


def tpm_executable() -> Path:
    p = TPM_ROOT / "tpm"
    if not p.is_file():
        raise PluginManagerError(
            f"Bundled TPM not found at {p}. Reinstall dmux or add submodule: "
            "git submodule update --init src/dmux/vendor/tpm"
        )
    return p


def _parse_plugin_lines(text: str) -> list[str]:
    out: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = re.match(
            r"^set(?:-option)?\s+-g\s+@plugin\s+['\"]([^'\"]+)['\"]",
            s,
        )
        if m:
            out.append(m.group(1))
    return out


def _plugin_dir_name(plugin_spec: str) -> str:
    main = plugin_spec.split("#", 1)[0]
    base = main.rstrip("/").split("/")[-1]
    if base.endswith(".git"):
        base = base[:-4]
    return base


def _validate_plugin_spec(spec: str) -> str:
    s = spec.strip()
    if not s or len(s) > 500:
        raise PluginManagerError("Invalid plugin string")
    if not _PLUGIN_RE.match(s):
        raise PluginManagerError("Plugin must look like user/repo, a git URL, or user/repo#branch")
    return s


def read_fragment() -> str:
    p = plugins_fragment_path()
    if not p.is_file():
        return ""
    return p.read_text(encoding="utf-8", errors="replace")


_MAX_FRAGMENT_BYTES = 600_000


def write_plugins_fragment_raw(text: str) -> None:
    """Overwrite the managed fragment file (web editor)."""
    if "\x00" in text:
        raise PluginManagerError("Invalid content (null byte)")
    normalized = normalize_freed_wu_opts_before_tpm(
        text.replace("\r\n", "\n").replace("\r", "\n")
    )
    if len(normalized.encode("utf-8")) > _MAX_FRAGMENT_BYTES:
        raise PluginManagerError("Fragment too large")
    p = plugins_fragment_path()
    _write_config_file(p, normalized, what="plugins.tmux")


def list_configured_plugins() -> list[str]:
    return _parse_plugin_lines(read_fragment())


def tpm_plugins_root() -> Path:
    """Directory where TPM installs plugins; matches vendor tpm `set_default_tpm_path()`."""
    home = Path.home()
    xdg_tmux = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) / "tmux"
    if (xdg_tmux / "tmux.conf").is_file():
        return (xdg_tmux / "plugins").resolve()
    return (home / ".tmux" / "plugins").resolve()


def _tmux_plugin_manager_path_value() -> str:
    """Value for tmux global `TMUX_PLUGIN_MANAGER_PATH` (trailing slash, TPM convention)."""
    return str(tpm_plugins_root()) + "/"


def _ensure_tmux_plugin_manager_path() -> None:
    """TPM `bin/*` scripts read `TMUX_PLUGIN_MANAGER_PATH` via `tmux show-environment`; the
    main `tpm` script sets it when sourced — CLI use must set it if still unset.
    """
    env = os.environ.copy()
    env.setdefault("HOME", str(Path.home()))
    try:
        chk = subprocess.run(
            [
                "tmux",
                "start-server",
                ";",
                "show-environment",
                "-g",
                "TMUX_PLUGIN_MANAGER_PATH",
            ],
            capture_output=True,
            text=True,
            env=env,
        )
    except FileNotFoundError as e:
        raise PluginManagerError(
            "tmux not found on PATH (required to run TPM install/update/clean)"
        ) from e
    if chk.returncode == 0 and (chk.stdout or "").strip().startswith("TMUX_PLUGIN_MANAGER_PATH="):
        return
    path = _tmux_plugin_manager_path_value()
    r = subprocess.run(
        [
            "tmux",
            "start-server",
            ";",
            "set-environment",
            "-g",
            "TMUX_PLUGIN_MANAGER_PATH",
            path,
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    if r.returncode != 0:
        msg = (r.stderr or r.stdout or "").strip() or f"exit {r.returncode}"
        raise PluginManagerError(f"tmux could not set TMUX_PLUGIN_MANAGER_PATH: {msg}")


def list_installed_plugin_dirs() -> list[str]:
    root = tpm_plugins_root()
    if not root.is_dir():
        return []
    return sorted(d.name for d in root.iterdir() if d.is_dir())


def _opts_marker_line(spec: str) -> str:
    return f"{_OPTS_MARKER_PREFIX}{spec}"


def _freed_wu_opts_slice(lines: list[str]) -> tuple[int, int] | None:
    """Return ``[start, end)`` line indices for the dmux opts block for Freed-Wu, or ``None``."""
    marker = _opts_marker_line(FREED_WU_STATUS_BAR_SPEC)
    start: int | None = None
    for i, line in enumerate(lines):
        if line.strip() == marker:
            start = i
            break
    if start is None:
        return None
    end = start + 1
    while end < len(lines):
        nxt = lines[end]
        if nxt.startswith(_OPTS_MARKER_PREFIX) and nxt.strip() != marker:
            break
        end += 1
    return (start, end)


def _tpm_run_line_index(lines: list[str]) -> int | None:
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("run '") and "tpm" in s:
            return i
    return None


# Tmux applies strftime to status-left/status-right; a literal %s becomes Unix time — strip
# optional Freed-Wu "format" leads that are only %s or %%s (still ends up as strftime %s).
_STATUS_STRFTIME_LEAD = re.compile(r'(#\{status-(?:left|right):)(?:%s|%%s)\s*;')


def _strip_status_strftime_percent_s_leads(body: str) -> str:
    return _STATUS_STRFTIME_LEAD.sub(r"\1", body)


def normalize_freed_wu_opts_before_tpm(body: str) -> str:
    """Strip strftime-prone ``%s`` leads in status JIT lines; move Freed-Wu block before ``run tpm`` if needed."""
    body = _strip_status_strftime_percent_s_leads(body)
    nl = body.endswith("\n")
    text = body.replace("\r\n", "\n").replace("\r", "\n")
    if not text.endswith("\n"):
        text += "\n"
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    win = _freed_wu_opts_slice(lines)
    i_run = _tpm_run_line_index(lines)
    if win is None or i_run is None:
        return body
    start, end = win
    if start < i_run:
        return body
    block = lines[start:end]
    new_lines = lines[:start] + lines[end:]
    i_run = _tpm_run_line_index(new_lines)
    if i_run is None:
        return body
    merged = new_lines[:i_run] + block + [""] + new_lines[i_run:]
    out = "\n".join(merged) + "\n"
    if not nl:
        out = out.rstrip("\n")
    return out


def _render_fragment(plugin_specs: list[str]) -> str:
    tpm_run = tpm_executable()
    lines = [
        "# Managed by dmux — TPM (https://github.com/tmux-plugins/tpm)",
        "# Edit via: dmux plugins … or the web UI.",
        "# Freed-Wu/tmux-status-bar: `set -g status-*` lines must appear *before* `run tpm`",
        "# (status-bar.tmux runs during TPM and reads those options then). Other plugins: options after `run tpm`.",
        "",
        "set -g @plugin 'tmux-plugins/tpm'",
    ]
    seen: set[str] = set()
    ordered: list[str] = []
    for spec in plugin_specs:
        s = _validate_plugin_spec(spec)
        if s in seen:
            continue
        seen.add(s)
        if s == "tmux-plugins/tpm":
            continue
        ordered.append(s)
        lines.append(f"set -g @plugin '{s}'")
    lines.append("")
    if FREED_WU_STATUS_BAR_SPEC in ordered:
        lines.append(f"# --- {FREED_WU_STATUS_BAR_SPEC} (before `run tpm`; see header) ---")
        lines.append(_opts_marker_line(FREED_WU_STATUS_BAR_SPEC))
        for opt in tmux_option_lines_for_plugin(FREED_WU_STATUS_BAR_SPEC):
            lines.append(opt)
        lines.append("")
    lines.append(f"run '{tpm_run}'")
    lines.append("")
    lines.append("# --- Options below: after TPM (see header above) ---")
    for spec in ordered:
        if spec == FREED_WU_STATUS_BAR_SPEC:
            continue
        lines.append(_opts_marker_line(spec))
        for opt in tmux_option_lines_for_plugin(spec):
            lines.append(opt)
    lines.append("")
    return "\n".join(lines) + "\n"


def write_fragment(plugin_specs: list[str]) -> None:
    p = plugins_fragment_path()
    _write_config_file(p, _render_fragment(plugin_specs), what="plugins.tmux")


def ensure_plugins_fragment_exists() -> None:
    """Create a minimal fragment (tpm only) if missing."""
    tpm_executable()
    if plugins_fragment_path().is_file():
        return
    write_fragment([])


def ensure_tmux_conf_hook() -> tuple[bool, str]:
    """Append a source-file hook to ``~/.tmux.conf`` (or ``DMUX_TMUX_CONF``) if absent.

    Returns ``(wrote, message)`` where ``message`` is suitable for UI (always includes resolved path).
    """
    conf = user_tmux_conf_path()
    display = resolved_user_tmux_conf_path()
    frag = plugins_fragment_path()
    ensure_plugins_fragment_exists()
    hook_line = f"source-file '{frag}'"
    if conf.is_file():
        try:
            body = _read_user_tmux_conf_text(conf)
        except OSError as e:
            raise PluginManagerError(
                f"Cannot read tmux config {display}: {e}. "
                "Fix read permission or point DMUX_TMUX_CONF at a readable file."
            ) from e
    else:
        body = ""
    if MARKER_BEGIN in body and MARKER_END in body:
        return (
            False,
            f"Hook already present — no change (file: {display}). Delete the dmux block there to re-bootstrap.",
        )
    block = f"\n{MARKER_BEGIN}\n{hook_line}\n{MARKER_END}\n"
    _write_config_file(conf, body.rstrip() + block + "\n", what="tmux config (bootstrap hook)")
    return (True, f"Wrote dmux hook to {display}")


def remove_tmux_conf_hook() -> tuple[bool, str]:
    """Remove the dmux ``source-file`` block (markers + hook) from the tmux config file.

    Does **not** delete ``plugins.tmux``. Returns ``(removed, message)``.
    """
    conf = user_tmux_conf_path()
    display = resolved_user_tmux_conf_path()
    if not conf.is_file():
        return (False, f"No config file at {display} — nothing to remove.")
    try:
        body = _read_user_tmux_conf_text(conf)
    except OSError as e:
        raise PluginManagerError(
            f"Cannot read tmux config {display}: {e}"
        ) from e
    normalized = body.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    begin_idx: int | None = None
    end_idx: int | None = None
    for idx, line in enumerate(lines):
        if line.strip() == MARKER_BEGIN:
            begin_idx = idx
            break
    if begin_idx is None:
        return (False, f"No dmux hook block in {display}.")
    for idx in range(begin_idx + 1, len(lines)):
        if lines[idx].strip() == MARKER_END:
            end_idx = idx
            break
    if end_idx is None:
        return (
            False,
            f"Found start marker but no end marker in {display} — fix or edit the file by hand.",
        )
    new_lines = lines[:begin_idx] + lines[end_idx + 1 :]
    new_body = "\n".join(new_lines).rstrip() + "\n"
    _write_config_file(conf, new_body, what="tmux config (remove dmux hook)")
    return (True, f"Removed dmux hook block from {display}")


def _user_plugin_specs() -> list[str]:
    return [p for p in list_configured_plugins() if p != "tmux-plugins/tpm"]


def add_plugin(spec: str) -> None:
    spec = _validate_plugin_spec(spec)
    ensure_plugins_fragment_exists()
    cur = _user_plugin_specs()
    if spec in cur:
        raise PluginManagerError(f"Plugin already listed: {spec}")
    cur.append(spec)
    write_fragment(cur)


def remove_plugin(spec: str) -> None:
    spec = spec.strip()
    if spec == "tmux-plugins/tpm":
        raise PluginManagerError("Cannot remove tpm itself from the list")
    cur = _user_plugin_specs()
    if spec not in cur:
        raise PluginManagerError(f"Plugin not in list: {spec}")
    write_fragment([p for p in cur if p != spec])


def regenerate_plugins_fragment() -> None:
    """Rewrite ``plugins.tmux`` from the current list (re-apply README / curated option lines)."""
    ensure_plugins_fragment_exists()
    write_fragment(_user_plugin_specs())


def _replace_plugin_option_block_marked(
    fragment: str, spec: str, new_options: list[str]
) -> tuple[str, bool]:
    """Replace lines after ``# dmux:opts:spec`` until the next opts marker or EOF."""
    marker = _opts_marker_line(spec)
    lines = fragment.splitlines()
    out: list[str] = []
    i = 0
    n = len(lines)
    found = False
    while i < n:
        line = lines[i]
        if line.strip() == marker:
            found = True
            out.append(marker)
            i += 1
            while i < n and not lines[i].startswith(_OPTS_MARKER_PREFIX):
                i += 1
            for opt in new_options:
                out.append(opt)
            continue
        out.append(line)
        i += 1
    body = "\n".join(out)
    if fragment.endswith("\n"):
        body += "\n"
    return body, found


def _replace_plugin_option_block_legacy(
    fragment: str, spec: str, new_options: list[str]
) -> tuple[str, bool]:
    """Replace lines after ``set -g @plugin 'spec'`` until the next ``@plugin`` or ``run``."""
    lines = fragment.splitlines()
    out: list[str] = []
    i = 0
    n = len(lines)
    needle = f"set -g @plugin '{spec}'"
    found = False
    while i < n:
        line = lines[i]
        if line.strip() == needle:
            found = True
            out.append(line)
            i += 1
            while i < n:
                sl = lines[i]
                if sl.startswith("set -g @plugin '") or sl.startswith("run '"):
                    break
                i += 1
            for opt in new_options:
                out.append(opt)
            continue
        out.append(line)
        i += 1
    body = "\n".join(out)
    if fragment.endswith("\n"):
        body += "\n"
    return body, found


def _replace_plugin_option_block(
    fragment: str, spec: str, new_options: list[str]
) -> tuple[str, bool]:
    """Replace plugin option lines (post-TPM markers when present, else legacy pre-run block)."""
    if _OPTS_MARKER_PREFIX in fragment and _opts_marker_line(spec) in fragment:
        return _replace_plugin_option_block_marked(fragment, spec, new_options)
    return _replace_plugin_option_block_legacy(fragment, spec, new_options)


def apply_suggested_options_for_plugin(spec: str) -> None:
    """Rewrite only the option block for one plugin (leave other lines unchanged)."""
    spec = _validate_plugin_spec(spec)
    ensure_plugins_fragment_exists()
    if spec not in _user_plugin_specs():
        raise PluginManagerError(f"Plugin not in list: {spec}")
    opts = tmux_option_lines_for_plugin(spec)
    body = read_fragment()
    new_body, ok = _replace_plugin_option_block(body, spec, opts)
    if not ok:
        raise PluginManagerError(f"Could not find @plugin line for {spec} in plugins.tmux")
    new_body = normalize_freed_wu_opts_before_tpm(new_body)
    _write_config_file(plugins_fragment_path(), new_body, what="plugins.tmux")


_MAX_CUSTOM_PLUGIN_LINES = 160
_MAX_CUSTOM_PLUGIN_LINE_CHARS = 16_384


def apply_custom_plugin_lines(spec: str, lines: list[str]) -> None:
    """Replace the tmux option lines after ``@plugin 'spec'`` (until next ``@plugin`` or ``run``)."""
    spec = _validate_plugin_spec(spec)
    if spec == "tmux-plugins/tpm":
        raise PluginManagerError("Cannot replace option lines for tpm")
    ensure_plugins_fragment_exists()
    if spec not in _user_plugin_specs():
        raise PluginManagerError(f"Plugin not in list: {spec}")
    if len(lines) > _MAX_CUSTOM_PLUGIN_LINES:
        raise PluginManagerError("Too many option lines")
    normalized: list[str] = []
    for line in lines:
        if not isinstance(line, str):
            raise PluginManagerError("Each line must be a string")
        if "\x00" in line:
            raise PluginManagerError("Invalid line (null byte)")
        if len(line) > _MAX_CUSTOM_PLUGIN_LINE_CHARS:
            raise PluginManagerError("Option line too long")
        normalized.append(line.replace("\r\n", "\n").replace("\r", "\n"))
    body = read_fragment()
    new_body, ok = _replace_plugin_option_block(body, spec, normalized)
    if not ok:
        raise PluginManagerError(f"Could not find @plugin line for {spec} in plugins.tmux")
    new_body = normalize_freed_wu_opts_before_tpm(new_body)
    if len(new_body.encode("utf-8")) > _MAX_FRAGMENT_BYTES:
        raise PluginManagerError("Fragment too large after update")
    _write_config_file(plugins_fragment_path(), new_body, what="plugins.tmux")


def _run_tpm_bin(script: str, *args: str) -> tuple[int, str, str]:
    exe = TPM_ROOT / "bin" / script
    if not exe.is_file():
        raise PluginManagerError(f"TPM script missing: {exe}")
    _ensure_tmux_plugin_manager_path()
    env = os.environ.copy()
    env.setdefault("HOME", str(Path.home()))
    proc = subprocess.run(
        ["bash", str(exe), *args],
        capture_output=True,
        text=True,
        timeout=600,
        env=env,
    )
    return proc.returncode, proc.stdout, proc.stderr


def tpm_install() -> str:
    """Clone missing plugins (TPM install_plugins)."""
    code, out, err = _run_tpm_bin("install_plugins")
    msg = (out + err).strip() or "(no output)"
    if code != 0:
        raise PluginManagerError(f"tpm install_plugins failed ({code}): {msg}")
    return msg


def _split_plugin_branch(spec: str) -> tuple[str, str | None]:
    if "#" in spec:
        base, _, rest = spec.partition("#")
        b = rest.strip()
        return base.strip(), (b or None)
    return spec.strip(), None


def _installed_origin_spec(target: Path) -> str | None:
    """Return ``user/repo`` for the git ``origin`` of an existing clone (None if unknown)."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(target), "config", "--get", "remote.origin.url"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    url = (proc.stdout or "").strip()
    if not url:
        return None
    # Strip ".git" + scheme/host to get user/repo when possible.
    s = url
    if s.endswith(".git"):
        s = s[:-4]
    for prefix in ("https://github.com/", "git@github.com:", "ssh://git@github.com/"):
        if s.startswith(prefix):
            return s[len(prefix):]
    return s


def _specs_point_to_same_repo(a: str, b: str) -> bool:
    def _norm(spec: str) -> str:
        s = spec.strip().rstrip("/")
        if s.endswith(".git"):
            s = s[:-4]
        for prefix in ("https://github.com/", "git@github.com:", "ssh://git@github.com/"):
            if s.startswith(prefix):
                s = s[len(prefix):]
                break
        return s.lower()

    return _norm(a) == _norm(b)


def tpm_install_one(spec: str) -> str:
    """Clone a single configured plugin into ~/.tmux/plugins (TPM-compatible layout)."""
    spec = _validate_plugin_spec(spec)
    configured = list_configured_plugins()
    if spec not in configured:
        raise PluginManagerError(f"Plugin not in configured list: {spec}")
    tpm_executable()
    main, branch = _split_plugin_branch(spec)
    dir_name = _plugin_dir_name(spec)
    plugins_root = tpm_plugins_root()
    target = plugins_root / dir_name
    if target.is_dir() and (target / ".git").exists():
        installed_spec = _installed_origin_spec(target)
        if installed_spec and not _specs_point_to_same_repo(installed_spec, main):
            raise PluginManagerError(
                f"Cannot install {spec}: ~/.tmux/plugins/{dir_name} already holds "
                f"{installed_spec} (TPM uses the basename of the repo). "
                "Pick one of these plugins at a time, or remove the existing dir."
            )
        return f"Already installed: {spec}"
    if target.exists():
        raise PluginManagerError(f"Path exists but is not a git clone: {target}")
    plugins_root.mkdir(parents=True, exist_ok=True)

    urls: list[str] = []
    if re.match(r"^(?:https?://|git@|ssh://)", main):
        urls.append(main)
    else:
        urls.append(f"https://github.com/{main}.git")
        urls.append(f"https://git::@github.com/{main}")

    env = os.environ.copy()
    env.setdefault("HOME", str(Path.home()))
    env["GIT_TERMINAL_PROMPT"] = "0"
    last_err = ""
    for url in urls:
        cmd = ["git", "clone", "--single-branch", "--recursive"]
        if branch:
            cmd.extend(["-b", branch])
        cmd.extend([url, str(target)])
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=env)
        if proc.returncode == 0:
            msg = (proc.stdout + proc.stderr).strip()
            return msg or f"Installed {spec}"
        last_err = (proc.stderr or proc.stdout or "").strip()
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
    raise PluginManagerError(f"git clone failed for {spec}: {last_err or 'unknown error'}")


def tpm_update_all() -> str:
    code, out, err = _run_tpm_bin("update_plugins", "all")
    msg = (out + err).strip() or "(no output)"
    if code != 0:
        raise PluginManagerError(f"tpm update failed ({code}): {msg}")
    return msg


def tpm_clean() -> str:
    code, out, err = _run_tpm_bin("clean_plugins")
    msg = (out + err).strip() or "(no output)"
    if code != 0:
        raise PluginManagerError(f"tpm clean failed ({code}): {msg}")
    return msg


def _tmux_show_global_option(name: str, *, socket_path: str | None = None) -> str | None:
    """Return current global value of a tmux option, or ``None`` if tmux not reachable."""
    cmd: list[str] = ["tmux"]
    if socket_path:
        cmd.extend(["-S", socket_path])
    cmd.extend(["show-option", "-gqv", name])
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return (proc.stdout or "").rstrip("\n")


# Apple silicon homebrew prefix isn't always on PATH for GUI-launched servers; check both.
_FREED_WU_COMPILER_BIN = "tmux-powerline-compiler"
_FREED_WU_EXTRA_PATH_HINTS = (
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/usr/bin",
    str(Path.home() / ".cargo" / "bin"),
    str(Path.home() / ".local" / "bin"),
)


def _which_with_extra_paths(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for d in _FREED_WU_EXTRA_PATH_HINTS:
        candidate = Path(d) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def freed_wu_status_bar_diagnostic(
    *,
    socket_path: str | None = None,
) -> dict[str, object]:
    """Detect the common ``Freed-Wu/tmux-status-bar`` failure: missing AOT compiler.

    The plugin's ``status-bar.tmux`` reads ``status-left``/``status-right`` then pipes them
    through ``tmux-powerline-compiler``; if that binary is missing the script silently
    overwrites both options with an empty string. Result: a blank status bar after every
    ``source-file``. We surface this with an actionable ``warning`` for the UI.
    """
    enabled = FREED_WU_STATUS_BAR_SPEC in list_configured_plugins()
    plugin_dir = tpm_plugins_root() / _plugin_dir_name(FREED_WU_STATUS_BAR_SPEC)
    installed = plugin_dir.is_dir() and (plugin_dir / "status-bar.tmux").is_file()
    compiler_path = _which_with_extra_paths(_FREED_WU_COMPILER_BIN) if enabled else None
    build_tools = {
        name: _which_with_extra_paths(name)
        for name in ("xmake", "nix-shell", "cpan")
    } if enabled else {}
    if enabled:
        status_left = _tmux_show_global_option("status-left", socket_path=socket_path)
        status_right = _tmux_show_global_option("status-right", socket_path=socket_path)
    else:
        status_left = None
        status_right = None
    blanked = enabled and status_left == "" and status_right == ""

    warning: str | None = None
    if enabled and not compiler_path:
        any_build_tool = any(v for v in build_tools.values())
        bits = [
            f"{FREED_WU_STATUS_BAR_SPEC} requires `{_FREED_WU_COMPILER_BIN}` "
            "to translate `#{status-left:…}` / `#{status-right:…}` syntax into tmux "
            "format strings.",
            f"Without it the plugin clears `status-left`/`status-right` on every `source-file`"
            f"{' — that is exactly what just happened (both are empty).' if blanked else '.'}",
        ]
        if any_build_tool:
            tools = ", ".join(name for name, p in build_tools.items() if p)
            bits.append(
                f"Build it with one of: {tools} (see "
                "https://github.com/Freed-Wu/tmux-status-bar#install)."
            )
        else:
            bits.append(
                "Install build deps then build it: "
                "`brew install xmake flex bison && cd ~/.tmux/plugins/tmux-status-bar && xmake`, "
                "or `cpan tmux-status-bar` for the perl variant, "
                "or remove the plugin from the list."
            )
        warning = " ".join(bits)

    return {
        "enabled": enabled,
        "installed": installed,
        "compiler_path": compiler_path,
        "build_tools": build_tools,
        "status_left_value": status_left,
        "status_right_value": status_right,
        "status_blanked": blanked,
        "warning": warning,
    }


def source_fragment_in_tmux(*, socket_path: str | None = None) -> dict[str, object]:
    """Reload the managed fragment in the tmux server so edits take effect live.

    A bare ``tmux source-file plugins.tmux`` re-runs the ``set -g`` lines and re-queues
    ``run '<tpm>'`` but many plugins (Freed-Wu/tmux-status-bar, dracula, …) cache state
    at TPM init time and skip work on a re-source — symptom: the file changes but the
    running tmux server still shows the old status bar / keybinds until ``kill-server``.
    To make this feel like a real reload (matching what TPM's own ``prefix+I`` does via
    ``reload_tmux_environment``) we:

    1. Source the dmux fragment (always, even when the user's tmux.conf has no dmux hook).
    2. Source the user's main tmux.conf when present — this re-runs the full bootstrap
       chain so every plugin's ``*.tmux`` entrypoint executes against the new option
       values (re-binding keys, recomputing status formats, etc.).
    3. Force ``refresh-client -S`` so every attached client immediately redraws the
       status bar / format strings instead of waiting for the next status-interval tick.

    ``socket_path`` must match the server dmux is using (e.g. ``dmux ui -S …``); otherwise
    ``tmux`` talks to the default socket and ``source-file`` appears to do nothing or errors.

    Returns ``{"output": str, "warning": str | None}``: ``warning`` is set when sourcing
    succeeded but a known plugin issue (e.g. Freed-Wu compiler missing) silently broke
    the visible status bar.
    """
    base: list[str] = ["tmux", "-2"]
    if socket_path:
        base.extend(["-S", socket_path])

    frag = plugins_fragment_path()
    proc = subprocess.run(
        [*base, "source-file", str(frag)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    msg = (proc.stdout + proc.stderr).strip()
    if proc.returncode != 0:
        raise PluginManagerError(
            f"tmux source-file failed ({proc.returncode}): {msg or 'tmux not running?'}"
        )

    extra_msgs: list[str] = []
    user_conf = user_tmux_conf_path()
    if user_conf.is_file():
        # Full re-bootstrap (mirrors `reload_tmux_environment` in vendor/tpm). Idempotent
        # even when the dmux hook in tmux.conf re-sources plugins.tmux a second time.
        conf_proc = subprocess.run(
            [*base, "source-file", str(user_conf)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        conf_msg = (conf_proc.stdout + conf_proc.stderr).strip()
        if conf_proc.returncode != 0:
            # Not fatal — the fragment-only source already succeeded. Surface as note so
            # the user can fix tmux.conf without losing the partial reload.
            extra_msgs.append(
                f"note: re-sourcing {resolved_user_tmux_conf_path()} failed "
                f"({conf_proc.returncode}): {conf_msg or 'see tmux output'}"
            )

    # Best-effort: nudge every attached client to redraw status / formats now. Plugins
    # that update `status-left` from a `run-shell` may finish *after* tmux's last
    # automatic redraw cycle for this command, leaving the bar visually stale.
    subprocess.run(
        [*base, "refresh-client", "-S"],
        capture_output=True,
        text=True,
        timeout=10,
    )

    diag = freed_wu_status_bar_diagnostic(socket_path=socket_path)
    output = "\n".join([m for m in [msg, *extra_msgs] if m]) or "ok"
    return {"output": output, "warning": diag.get("warning")}


def official_tmux_plugins_catalog() -> tuple[list[str], str | None]:
    """List ``user/repo`` specs for the install autocomplete.

    Merges three sources (de-duplicated, sorted):

    * The bundled awesome list (``data/awesome_tmux_plugins.json`` mirroring
      ``tmux-plugins/list``) — always available offline, ~100 plugins from
      many GitHub users (catppuccin, dracula, Freed-Wu, sainnhe, …).
    * The live ``tmux-plugins`` GitHub org repos (cached for
      :data:`_CATALOG_TTL_SEC`) — picks up new official plugins automatically.
    * A small extras list (``_EXTRA_FALLBACK``) for historical specs.

    If GitHub is unreachable, the returned ``error`` hint is set but the list
    is still useful (awesome list + extras).
    """
    global _CATALOG_CACHE
    now = time.monotonic()
    if _CATALOG_CACHE is not None:
        ts, cached, cached_err = _CATALOG_CACHE
        if now - ts < _CATALOG_TTL_SEC:
            return list(cached), cached_err

    fb = sorted(_fallback_catalog_specs())
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "dmux (tmux-plugins catalog)",
    }
    collected: list[str] = []
    err_note: str | None = None
    page = 1
    try:
        while True:
            url = (
                f"{_GITHUB_ORG_API}?per_page=100&page={page}"
                "&type=sources&sort=full_name&direction=asc"
            )
            req = urllib.request.Request(url, headers=headers)
            with ssl_fetch.urlopen(req, timeout=25) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            payload: object = json.loads(raw)
            if not isinstance(payload, list):
                err_note = "unexpected GitHub API response"
                break
            for item in payload:
                if not isinstance(item, dict):
                    continue
                fn = item.get("full_name")
                if isinstance(fn, str) and fn.strip():
                    collected.append(fn.strip())
            if len(payload) < 100:
                break
            page += 1
            if page > 50:
                break
    except urllib.error.HTTPError as e:
        err_note = f"GitHub API HTTP {e.code}"
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        err_note = ssl_fetch.urllib_error_message(e)

    merged = sorted(set(collected) | set(fb))
    _CATALOG_CACHE = (now, tuple(merged), err_note)
    return merged, err_note


def plugin_catalog_entries() -> tuple[list[dict[str, str]], str | None]:
    """Enriched catalog: ``[{spec, category, description, source}, …]``.

    Combines the bundled awesome list (categories + descriptions) with the
    live ``tmux-plugins`` GitHub org repos. Org-only entries get
    ``category="Official"`` and an empty description; awesome-list entries
    keep their human-curated category. ``source`` is one of ``"awesome"``,
    ``"github-org"``, or ``"both"``.

    Returned list is sorted by ``(category, spec)``. The optional ``error``
    is set when the live GitHub fetch failed (the list itself is still
    populated from the bundled file).
    """
    awesome = _awesome_catalog_entries()
    awesome_by_spec: dict[str, dict[str, str]] = {e["spec"]: dict(e) for e in awesome}

    live_specs, err = official_tmux_plugins_catalog()
    live_set = set(live_specs)

    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for spec, entry in awesome_by_spec.items():
        seen.add(spec)
        out.append(
            {
                "spec": spec,
                "category": entry.get("category") or "Other",
                "description": entry.get("description", ""),
                "source": "both" if spec in live_set else "awesome",
            }
        )
    for spec in live_specs:
        if spec in seen:
            continue
        out.append(
            {
                "spec": spec,
                "category": "Official",
                "description": "",
                "source": "github-org",
            }
        )
    out.sort(key=lambda e: (e["category"].lower(), e["spec"].lower()))
    return out, err


def status_dict(*, socket_path: str | None = None) -> dict[str, object]:
    """API payload: configured plugins, disk installs, paths, plugin-specific diagnostics."""
    tpm_ok = TPM_ROOT.joinpath("tpm").is_file()
    if tpm_ok:
        ensure_plugins_fragment_exists()
    configured = list_configured_plugins() if plugins_fragment_path().is_file() else []
    installed = list_installed_plugin_dirs()
    rows: list[dict[str, object]] = []
    for spec in configured:
        name = _plugin_dir_name(spec)
        rows.append(
            {
                "spec": spec,
                "directory": name,
                "installed": name in installed,
            }
        )
    return {
        "tpm_bundled": tpm_ok,
        "fragment_path": resolved_plugins_fragment_path(),
        "tmux_conf": resolved_user_tmux_conf_path(),
        "plugins": rows,
        "installed_dirs": installed,
        "freed_wu_status_bar": freed_wu_status_bar_diagnostic(socket_path=socket_path),
    }
