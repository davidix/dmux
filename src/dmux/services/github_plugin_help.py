"""GitHub repo + README snippets for TPM-style ``user/repo`` plugin specs."""

from __future__ import annotations

import base64
import json
import re
import time
import urllib.error
import urllib.request
from typing import Any

from dmux import ssl_fetch

_GITHUB_API = "https://api.github.com"
_USER_AGENT = "dmux (plugin help)"
_HELP_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_HELP_TTL_SEC = 86400.0
_README_MD_CACHE: dict[str, tuple[float, str | None]] = {}
_README_MD_TTL_SEC = 86400.0


def _parse_user_repo(spec: str) -> tuple[str, str] | None:
    """Return (owner, repo) for simple ``owner/repo`` or ``owner/repo#branch``."""
    main = spec.split("#", 1)[0].strip()
    if not main or "/" not in main:
        return None
    if main.startswith(("http://", "https://", "git@", "ssh://")):
        return None
    parts = main.rstrip("/").split("/")
    if len(parts) != 2:
        return None
    owner, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not owner or not repo:
        return None
    return owner, repo


def _http_json(url: str) -> tuple[dict[str, Any] | None, str | None]:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": _USER_AGENT,
        },
    )
    try:
        with ssl_fetch.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8", errors="replace")
        except OSError:
            err_body = ""
        return None, f"HTTP {e.code} {err_body[:120]}"
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        return None, ssl_fetch.urllib_error_message(e)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None, "invalid JSON"
    if not isinstance(data, dict):
        return None, "unexpected response"
    return data, None


def _readme_excerpt(markdown: str, limit: int = 380) -> str:
    """First substantive paragraph of README as plain-ish text."""
    text = markdown.replace("\r\n", "\n")
    blocks = re.split(r"\n\s*\n+", text)
    for block in blocks:
        lines: list[str] = []
        for line in block.split("\n"):
            s = line.strip()
            if not s:
                continue
            if s.startswith("#"):
                s = re.sub(r"^#+\s*", "", s)
            s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
            s = re.sub(r"!\[([^\]]*)\]\([^)]+\)", "", s)
            s = re.sub(r"`+", "", s)
            lines.append(s)
        chunk = " ".join(" ".join(lines).split())
        if len(chunk) < 24:
            continue
        if len(chunk) > limit:
            return chunk[: limit - 1].rstrip() + "…"
        return chunk
    one = " ".join(text.split())
    if len(one) > limit:
        return one[: limit - 1].rstrip() + "…"
    return one


def github_plugin_help(spec: str) -> dict[str, Any]:
    """Return metadata + description for a ``user/repo`` GitHub plugin.

    Uses the GitHub REST API (repo ``description``, then README excerpt).
    Cached per spec for :data:`_HELP_TTL_SEC`.
    """
    spec = spec.strip()
    out_base: dict[str, Any] = {
        "spec": spec,
        "github": False,
        "title": None,
        "description": None,
        "repo_url": None,
        "readme_url": None,
        "source": None,
        "error": None,
    }

    parsed = _parse_user_repo(spec)
    if not parsed:
        out_base["error"] = "not a simple GitHub user/repo spec"
        return out_base

    owner, repo = parsed
    now = time.monotonic()
    if spec in _HELP_CACHE:
        ts, cached = _HELP_CACHE[spec]
        if now - ts < _HELP_TTL_SEC:
            return dict(cached)

    repo_api = f"{_GITHUB_API}/repos/{owner}/{repo}"
    repo_data, err = _http_json(repo_api)
    if err or not repo_data:
        return {
            **out_base,
            "github": True,
            "error": err or "repo not found",
        }

    html_url = repo_data.get("html_url")
    if isinstance(html_url, str):
        out_base["repo_url"] = html_url
    default_branch = repo_data.get("default_branch")
    name = repo_data.get("name")
    if isinstance(name, str):
        out_base["title"] = name
    desc = repo_data.get("description")
    description: str | None = None
    if isinstance(desc, str) and desc.strip():
        description = desc.strip()

    readme_url: str | None = None
    if isinstance(html_url, str):
        br = default_branch if isinstance(default_branch, str) else "main"
        readme_url = f"{html_url}/blob/{br}/README.md"

    source = "repo"
    if not description or len(description) < 12:
        readme_api = f"{repo_api}/readme"
        rd, rerr = _http_json(readme_api)
        if not rerr and isinstance(rd, dict):
            html = rd.get("html_url")
            if isinstance(html, str):
                readme_url = html  # exact file URL from API
            b64 = rd.get("content")
            enc = rd.get("encoding")
            if enc == "base64" and isinstance(b64, str):
                try:
                    raw_md = base64.b64decode(b64).decode("utf-8", errors="replace")
                    excerpt = _readme_excerpt(raw_md)
                    if excerpt:
                        description = excerpt
                        source = "readme"
                except (ValueError, OSError):
                    pass

    out: dict[str, Any] = {
        **out_base,
        "github": True,
        "description": description,
        "readme_url": readme_url,
        "source": source if description else None,
        "error": None if description else "no description available",
    }
    _HELP_CACHE[spec] = (now, out)
    return dict(out)


def fetch_readme_markdown(spec: str) -> str | None:
    """Return ``README`` body (decoded) for a ``user/repo`` GitHub spec, or None."""
    spec = spec.strip()
    now = time.monotonic()
    if spec in _README_MD_CACHE:
        ts, body = _README_MD_CACHE[spec]
        if now - ts < _README_MD_TTL_SEC:
            return body
    parsed = _parse_user_repo(spec)
    if not parsed:
        _README_MD_CACHE[spec] = (now, None)
        return None
    owner, repo = parsed
    url = f"{_GITHUB_API}/repos/{owner}/{repo}/readme"
    rd, err = _http_json(url)
    if err or not isinstance(rd, dict):
        _README_MD_CACHE[spec] = (now, None)
        return None
    if rd.get("encoding") != "base64":
        _README_MD_CACHE[spec] = (now, None)
        return None
    b64 = rd.get("content")
    if not isinstance(b64, str):
        _README_MD_CACHE[spec] = (now, None)
        return None
    try:
        body = base64.b64decode(b64).decode("utf-8", errors="replace")
    except (ValueError, OSError):
        _README_MD_CACHE[spec] = (now, None)
        return None
    _README_MD_CACHE[spec] = (now, body)
    return body
