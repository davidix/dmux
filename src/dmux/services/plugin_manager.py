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
from pathlib import Path

from dmux import ssl_fetch
from dmux.exceptions import PluginManagerError
from dmux.plugin_doc_defaults import tmux_option_lines_for_plugin

# TPM ships inside the dmux package (clone or git submodule → src/dmux/vendor/tpm).
_PKG_ROOT = Path(__file__).resolve().parent.parent
TPM_ROOT = _PKG_ROOT / "vendor" / "tpm"

MARKER_BEGIN = "# >>> dmux: tpm plugins fragment >>>"
MARKER_END = "# <<< dmux: tpm plugins fragment <<<"

# GitHub-style user/repo or full git URL (TPM-supported forms, simplified).
_PLUGIN_RE = re.compile(r"^(?:https?://|git@|ssh://).+$|^[^/\s]+/[^/\s]+(?:#[^\s]+)?$")

# GitHub: tmux-plugins org (official TPM plugin index). Cached in-process.
# Value: (monotonic_ts, plugin_specs, optional_error_hint_when_degraded).
_CATALOG_CACHE: tuple[float, tuple[str, ...], str | None] | None = None
_CATALOG_TTL_SEC = 3600.0
_GITHUB_ORG_API = "https://api.github.com/orgs/tmux-plugins/repos"

# Used when GitHub is unreachable (offline, rate limit, firewall). Merged with live results when OK.
_FALLBACK_TMUX_PLUGINS: tuple[str, ...] = (
    "tmux-plugins/tmux-battery",
    "tmux-plugins/tmux-continuum",
    "tmux-plugins/tmux-copycat",
    "tmux-plugins/tmux-cpu",
    "tmux-plugins/tmux-example-plugin",
    "tmux-plugins/tmux-fpp",
    "tmux-plugins/tmux-maildir-counter",
    "tmux-plugins/tmux-mem-cpu-load",
    "tmux-plugins/tmux-net-speed",
    "tmux-plugins/tmux-online-status",
    "tmux-plugins/tmux-open",
    "tmux-plugins/tmux-pain-control",
    "tmux-plugins/tmux-prefix-highlight",
    "tmux-plugins/tmux-resurrect",
    "tmux-plugins/tmux-sensible",
    "tmux-plugins/tmux-sessionist",
    "tmux-plugins/tmux-sidebar",
    "tmux-plugins/tmux-super-fingers",
    "tmux-plugins/tmux-urlview",
    "tmux-plugins/tmux-yank",
    "tmux-plugins/vim-tmux-focus-events",
)


def _xdg_config() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))


def plugins_fragment_path() -> Path:
    """Managed TPM fragment: list of set -g @plugin lines + run tpm."""
    return _xdg_config() / "dmux" / "plugins.tmux"


def user_tmux_conf_path() -> Path:
    """Same resolution order as TPM's plugin_functions.sh."""
    xdg = _xdg_config() / "tmux" / "tmux.conf"
    if xdg.is_file():
        return xdg
    return Path.home() / ".tmux.conf"


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
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if len(normalized.encode("utf-8")) > _MAX_FRAGMENT_BYTES:
        raise PluginManagerError("Fragment too large")
    p = plugins_fragment_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(normalized, encoding="utf-8")


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


def _render_fragment(plugin_specs: list[str]) -> str:
    tpm_run = tpm_executable()
    lines = [
        "# Managed by dmux — TPM (https://github.com/tmux-plugins/tpm)",
        "# Edit via: dmux plugins … or the web UI.",
        "# Optional lines after each @plugin: README fenced blocks or data/plugin_defaults.json.",
        "",
        "set -g @plugin 'tmux-plugins/tpm'",
    ]
    seen: set[str] = set()
    for spec in plugin_specs:
        s = _validate_plugin_spec(spec)
        if s in seen:
            continue
        seen.add(s)
        if s == "tmux-plugins/tpm":
            continue
        lines.append(f"set -g @plugin '{s}'")
        for opt in tmux_option_lines_for_plugin(s):
            lines.append(opt)
    lines.extend(
        [
            "",
            f"run '{tpm_run}'",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def write_fragment(plugin_specs: list[str]) -> None:
    p = plugins_fragment_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_render_fragment(plugin_specs), encoding="utf-8")


def ensure_plugins_fragment_exists() -> None:
    """Create a minimal fragment (tpm only) if missing."""
    tpm_executable()
    if plugins_fragment_path().is_file():
        return
    write_fragment([])


def ensure_tmux_conf_hook() -> bool:
    """Append a source-file hook to the user's tmux.conf if absent. Returns True if changed."""
    conf = user_tmux_conf_path()
    frag = plugins_fragment_path()
    ensure_plugins_fragment_exists()
    hook_line = f"source-file '{frag}'"
    if conf.is_file():
        body = conf.read_text(encoding="utf-8", errors="replace")
    else:
        body = ""
    if MARKER_BEGIN in body and MARKER_END in body:
        return False
    block = f"\n{MARKER_BEGIN}\n{hook_line}\n{MARKER_END}\n"
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text(body.rstrip() + block + "\n", encoding="utf-8")
    return True


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


def _replace_plugin_option_block(
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
    plugins_fragment_path().write_text(new_body, encoding="utf-8")


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


def source_fragment_in_tmux() -> str:
    """Reload the managed fragment in the tmux server (best-effort)."""
    frag = plugins_fragment_path()
    proc = subprocess.run(
        ["tmux", "source-file", str(frag)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    msg = (proc.stdout + proc.stderr).strip()
    if proc.returncode != 0:
        raise PluginManagerError(
            f"tmux source-file failed ({proc.returncode}): {msg or 'tmux not running?'}"
        )
    return msg or "ok"


def official_tmux_plugins_catalog() -> tuple[list[str], str | None]:
    """List ``user/repo`` specs from the tmux-plugins GitHub org (non-forks).

    Results are cached for :data:`_CATALOG_TTL_SEC`. If GitHub is unreachable,
    returns a bundled :data:`_FALLBACK_TMUX_PLUGINS` list merged with any
    partial fetch, plus an error hint for the UI.
    """
    global _CATALOG_CACHE
    now = time.monotonic()
    if _CATALOG_CACHE is not None:
        ts, cached, cached_err = _CATALOG_CACHE
        if now - ts < _CATALOG_TTL_SEC:
            return list(cached), cached_err

    fb = sorted(s for s in _FALLBACK_TMUX_PLUGINS if s != "tmux-plugins/tpm")
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

    merged = sorted((set(collected) | set(fb)) - {"tmux-plugins/tpm"})
    _CATALOG_CACHE = (now, tuple(merged), err_note)
    return merged, err_note


def status_dict() -> dict[str, object]:
    """API payload: configured plugins, disk installs, paths."""
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
        "fragment_path": str(plugins_fragment_path()),
        "tmux_conf": str(user_tmux_conf_path()),
        "plugins": rows,
        "installed_dirs": installed,
    }
