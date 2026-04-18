import json
from pathlib import Path

from dmux import ssl_fetch
from dmux.api.app import create_app
from dmux.paths import dmux_package_dir, resolve_dmux_web_dir
from dmux.persistence.state_manager import StateManager
from dmux.schemas import Snapshot, SnapshotPane, SnapshotSession, SnapshotWindow
from dmux.services import plugin_manager as pm


def test_resolve_dmux_web_dir_accepts_repo_root_layout(tmp_path, monkeypatch) -> None:
    """DMUX_WEB_ROOT can point at a checkout root (…/repo) with src/dmux/web/…"""
    fake_pkg = tmp_path / "src" / "dmux"
    (fake_pkg / "web" / "templates").mkdir(parents=True)
    (fake_pkg / "web" / "templates" / "index.html").write_text("<html/>", encoding="utf-8")
    monkeypatch.setenv("DMUX_WEB_ROOT", str(tmp_path))
    assert resolve_dmux_web_dir() == fake_pkg / "web"


def test_resolve_dmux_web_dir_falls_back_when_invalid(monkeypatch) -> None:
    monkeypatch.setenv("DMUX_WEB_ROOT", "/this/path/does/not/exist/ever")
    assert resolve_dmux_web_dir() == dmux_package_dir() / "web"


def test_ui_static_is_not_served_as_304_with_if_none_match(monkeypatch: object) -> None:
    """Browsers were reusing stale JS/CSS because Flask sent 304 when If-None-Match matched."""
    monkeypatch.delenv("DMUX_UI_ALLOW_CACHE", raising=False)
    app = create_app()
    app.testing = True
    c = app.test_client()
    r1 = c.get("/static/styles.css")
    assert r1.status_code == 200
    assert "no-store" in (r1.headers.get("Cache-Control") or "")
    fake_etag = '"bogus-etag-for-test"'
    r2 = c.get("/static/styles.css", headers={"If-None-Match": fake_etag})
    assert r2.status_code == 200
    assert r2.headers.get("ETag") is None


def test_health() -> None:
    app = create_app()
    app.testing = True
    c = app.test_client()
    r = c.get("/api/health")
    assert r.status_code == 200
    assert r.get_json() == {"status": "ok"}


def test_pane_send_keys_validates_and_400s_when_pane_missing(monkeypatch) -> None:
    """Bad pane id surfaces 404; missing text yields 400."""
    from dmux.exceptions import PaneNotFoundError

    def fake_send(self: object, pane_id: str, text: str, **kwargs: object) -> None:
        raise PaneNotFoundError(pane_id)

    monkeypatch.setattr("dmux.api.app.TmuxService.send_keys", fake_send)
    app = create_app()
    app.testing = True
    c = app.test_client()
    # 400 — invalid body
    r = c.post(
        "/api/v1/panes/%25/send-keys",
        data=json.dumps({"text": 5}),
        content_type="application/json",
    )
    assert r.status_code == 400
    # 404 — pane not found
    r2 = c.post(
        "/api/v1/panes/%2599/send-keys",
        data=json.dumps({"text": "ls"}),
        content_type="application/json",
    )
    assert r2.status_code == 404


def test_pane_resize_rejects_zero_delta() -> None:
    app = create_app()
    app.testing = True
    c = app.test_client()
    r = c.post(
        "/api/v1/panes/%250/resize",
        data=json.dumps({"delta_x": 0, "delta_y": 0}),
        content_type="application/json",
    )
    assert r.status_code == 400


def test_pane_swap_rejects_invalid_direction() -> None:
    app = create_app()
    app.testing = True
    c = app.test_client()
    r = c.post(
        "/api/v1/panes/%250/swap",
        data=json.dumps({"direction": "sideways"}),
        content_type="application/json",
    )
    assert r.status_code == 400


def test_window_move_rejects_invalid_direction() -> None:
    app = create_app()
    app.testing = True
    c = app.test_client()
    r = c.post(
        "/api/v1/sessions/x/windows/0/move",
        data=json.dumps({"direction": "diagonal"}),
        content_type="application/json",
    )
    assert r.status_code == 400


def test_window_rename_requires_name() -> None:
    app = create_app()
    app.testing = True
    c = app.test_client()
    r = c.patch(
        "/api/v1/sessions/x/windows/0/rename",
        data=json.dumps({"name": "  "}),
        content_type="application/json",
    )
    assert r.status_code == 400


def test_pane_capture_invokes_service(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_capture(self: object, pane_id: str, *, lines: int = 200) -> str:
        captured["pane_id"] = pane_id
        captured["lines"] = lines
        return "hello\nworld"

    monkeypatch.setattr("dmux.api.app.TmuxService.capture_pane", fake_capture)
    app = create_app()
    app.testing = True
    c = app.test_client()
    r = c.get("/api/v1/panes/%2542/capture?lines=50")
    assert r.status_code == 200
    body = r.get_json()
    assert body.get("ok") is True
    assert body.get("text") == "hello\nworld"
    assert captured == {"pane_id": "%42", "lines": 50}


def test_server_info_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(
        "dmux.api.app.TmuxService.server_info",
        lambda self: {"version": "tmux 3.4", "socket_path": None, "sessions": "0", "clients": "0"},
    )
    app = create_app()
    app.testing = True
    c = app.test_client()
    r = c.get("/api/v1/server")
    assert r.status_code == 200
    body = r.get_json()
    assert body["version"] == "tmux 3.4"
    assert body["socket_path"] is None


def test_plugins_fragment_put(monkeypatch) -> None:
    saved: list[str] = []

    def fake_write(text: str) -> None:
        saved.append(text)

    monkeypatch.setattr(pm, "write_plugins_fragment_raw", fake_write)
    app = create_app()
    app.testing = True
    c = app.test_client()
    r = c.put(
        "/api/v1/plugins/fragment",
        data=json.dumps({"content": "set -g x y\n"}),
        content_type="application/json",
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body.get("ok") is True
    assert body.get("path") == pm.resolved_plugins_fragment_path()
    assert saved == ["set -g x y\n"]


def test_plugins_fragment_put_invalid() -> None:
    app = create_app()
    app.testing = True
    c = app.test_client()
    r = c.put(
        "/api/v1/plugins/fragment",
        data=json.dumps({"content": 123}),
        content_type="application/json",
    )
    assert r.status_code == 400


def test_plugins_fragment_get() -> None:
    app = create_app()
    app.testing = True
    c = app.test_client()
    r = c.get("/api/v1/plugins/fragment")
    assert r.status_code == 200
    data = r.get_json()
    assert "path" in data
    assert "exists" in data
    assert "content" in data


def test_user_tmux_conf_defaults_to_dot_tmux_conf(monkeypatch, tmp_path) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.delenv("DMUX_TMUX_CONF", raising=False)
    assert pm.user_tmux_conf_path() == fake_home / ".tmux.conf"


def test_user_tmux_conf_respects_dmux_env(tmp_path, monkeypatch) -> None:
    custom = tmp_path / "my-tmux.conf"
    monkeypatch.setenv("DMUX_TMUX_CONF", str(custom))
    assert pm.user_tmux_conf_path() == custom


def test_plugins_status() -> None:
    app = create_app()
    app.testing = True
    c = app.test_client()
    r = c.get("/api/v1/plugins")
    assert r.status_code == 200
    data = r.get_json()
    assert "tpm_bundled" in data
    assert "plugins" in data
    assert "tmux_socket" in data
    assert data["tmux_socket"] is None


def test_plugins_source_uses_app_socket(monkeypatch) -> None:
    recorded: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> object:
        recorded.append(cmd)

        class R:
            returncode = 0
            stdout = "ok"
            stderr = ""

        return R()

    monkeypatch.setattr("dmux.services.plugin_manager.subprocess.run", fake_run)
    monkeypatch.setattr(
        pm,
        "freed_wu_status_bar_diagnostic",
        lambda *, socket_path=None: {"warning": None},
    )
    app = create_app(socket_path="/tmp/dmux-test-tmux.sock")
    app.testing = True
    c = app.test_client()
    r = c.post("/api/v1/plugins/source", data="{}", content_type="application/json")
    assert r.status_code == 200
    body = r.get_json()
    assert body.get("ok") is True
    assert body.get("output") == "ok"
    assert body.get("warning") is None
    assert recorded, "tmux should have been invoked"
    assert "-S" in recorded[0]
    assert "/tmp/dmux-test-tmux.sock" in recorded[0]
    assert "source-file" in recorded[0]


def test_plugins_source_warns_when_freed_wu_clobbers_status(monkeypatch) -> None:
    """When Freed-Wu plugin is configured but the AOT compiler is missing, sourcing the
    fragment leaves status-left/status-right empty — surface that as a `warning` in the
    POST /api/v1/plugins/source response (UI shows a warn-style toast)."""

    def fake_run(cmd: list[str], **kwargs: object) -> object:
        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        return R()

    monkeypatch.setattr("dmux.services.plugin_manager.subprocess.run", fake_run)
    monkeypatch.setattr(pm, "list_configured_plugins", lambda: [pm.FREED_WU_STATUS_BAR_SPEC])
    monkeypatch.setattr(pm, "_which_with_extra_paths", lambda name: None)
    monkeypatch.setattr(pm, "_tmux_show_global_option", lambda name, *, socket_path=None: "")

    app = create_app()
    app.testing = True
    c = app.test_client()
    r = c.post("/api/v1/plugins/source", data="{}", content_type="application/json")
    assert r.status_code == 200
    body = r.get_json()
    assert body.get("ok") is True
    warning = body.get("warning") or ""
    assert "tmux-powerline-compiler" in warning
    assert "status-left" in warning


def test_freed_wu_diagnostic_silent_when_plugin_not_configured(monkeypatch) -> None:
    monkeypatch.setattr(pm, "list_configured_plugins", lambda: ["tmux-plugins/tmux-sensible"])
    diag = pm.freed_wu_status_bar_diagnostic()
    assert diag["enabled"] is False
    assert diag["warning"] is None


def test_status_dict_includes_freed_wu_diagnostic(monkeypatch) -> None:
    monkeypatch.setattr(pm, "list_configured_plugins", lambda: [])
    payload = pm.status_dict()
    assert "freed_wu_status_bar" in payload
    assert payload["freed_wu_status_bar"]["enabled"] is False
    assert payload["freed_wu_status_bar"]["warning"] is None


def test_plugins_install_single_via_json_body(monkeypatch) -> None:
    def fake_one(spec: str) -> str:
        assert spec == "tmux-plugins/tmux-sensible"
        return "cloned"

    monkeypatch.setattr(pm, "tpm_install_one", fake_one)
    monkeypatch.setattr(pm, "tpm_install", lambda: "should-not-run")

    app = create_app()
    app.testing = True
    c = app.test_client()
    r = c.post(
        "/api/v1/plugins/install",
        data=json.dumps({"plugin": "tmux-plugins/tmux-sensible"}),
        content_type="application/json",
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body.get("ok") is True
    assert "cloned" in (body.get("output") or "")


def test_plugins_catalog_mocked(monkeypatch) -> None:
    payload = [
        {"full_name": "tmux-plugins/tmux-sensible"},
        {"full_name": "tmux-plugins/tpm"},
    ]

    class _FakeResp:
        def __enter__(self) -> "_FakeResp":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def read(self) -> bytes:
            return json.dumps(payload).encode("utf-8")

    def _fake_urlopen(req: object, timeout: float = 0) -> _FakeResp:
        return _FakeResp()

    monkeypatch.setattr(ssl_fetch, "urlopen", _fake_urlopen)
    pm._CATALOG_CACHE = None  # type: ignore[attr-defined]

    plugins, err = pm.official_tmux_plugins_catalog()
    assert err is None
    assert "tmux-plugins/tmux-sensible" in plugins
    assert "tmux-plugins/tpm" in plugins

    app = create_app()
    app.testing = True
    c = app.test_client()
    r = c.get("/api/v1/plugins/catalog")
    assert r.status_code == 200
    body = r.get_json()
    assert body["source"] == "https://github.com/tmux-plugins/list"
    assert "tmux-plugins/tmux-sensible" in body["plugins"]
    assert body.get("error") is None
    entries = body.get("entries")
    assert isinstance(entries, list) and entries, "entries must be a non-empty list"
    by_spec = {e["spec"]: e for e in entries if isinstance(e, dict)}
    sensible = by_spec.get("tmux-plugins/tmux-sensible")
    assert sensible is not None
    assert sensible.get("category"), "awesome-list entry must have a category"
    assert sensible.get("description"), "awesome-list entry must have a description"
    catppuccin = by_spec.get("catppuccin/tmux")
    assert catppuccin is not None, "awesome-list non-tmux-plugins specs should be exposed"
    assert catppuccin.get("category") == "Themes"
    cats = body.get("categories")
    assert isinstance(cats, list) and "Themes" in cats and "Status Bar" in cats


def test_plugins_help_mocked(monkeypatch) -> None:
    def fake_help(spec: str) -> dict[str, object]:
        return {
            "spec": spec,
            "github": True,
            "title": "tmux-sidebar",
            "description": "A sidebar for tmux.",
            "repo_url": "https://github.com/tmux-plugins/tmux-sidebar",
            "readme_url": "https://github.com/tmux-plugins/tmux-sidebar/blob/master/README.md",
            "source": "repo",
            "error": None,
        }

    monkeypatch.setattr("dmux.api.app.plugin_github_help", fake_help)
    monkeypatch.setattr(
        "dmux.api.app.tmux_option_lines_for_plugin",
        lambda s: ["set -g @sidebar-width '40'"],
    )
    app = create_app()
    app.testing = True
    c = app.test_client()
    r = c.get("/api/v1/plugins/help?plugin=tmux-plugins%2Ftmux-sidebar")
    assert r.status_code == 200
    body = r.get_json()
    assert body["description"] == "A sidebar for tmux."
    assert "tmux-sidebar" in (body.get("repo_url") or "")
    assert body.get("suggested_tmux_lines") == ["set -g @sidebar-width '40'"]


def test_plugin_readme_extracts_option_lines() -> None:
    from dmux.plugin_doc_defaults import extract_tmux_option_lines_from_readme

    md = """# P
```tmux
# comment
set -g @foo 'bar'
set -g @plugin 'other/other'
```
"""
    xs = extract_tmux_option_lines_from_readme(md)
    assert xs == ["set -g @foo 'bar'"]


def test_plugins_apply_defaults(monkeypatch) -> None:
    called: list[int] = []

    def fake_regenerate() -> None:
        called.append(1)

    monkeypatch.setattr(pm, "regenerate_plugins_fragment", fake_regenerate)
    app = create_app()
    app.testing = True
    c = app.test_client()
    r = c.post("/api/v1/plugins/apply-defaults")
    assert r.status_code == 200
    assert r.get_json() == {"ok": True}
    assert called == [1]


def test_plugins_plugin_lines_post(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    pm.write_fragment(["Freed-Wu/tmux-status-bar"])
    app = create_app()
    app.testing = True
    c = app.test_client()
    lines = ["# wizard", 'set -g status-left "#{status-left:a,b,c}"']
    r = c.post(
        "/api/v1/plugins/plugin-lines",
        data=json.dumps({"plugin": "Freed-Wu/tmux-status-bar", "lines": lines}),
        content_type="application/json",
    )
    assert r.status_code == 200
    frag = pm.read_fragment()
    assert "# wizard" in frag
    assert 'set -g status-left "#{status-left:a,b,c}"' in frag


def test_plugins_apply_defaults_one_plugin(monkeypatch) -> None:
    called: list[str] = []

    def fake_apply(spec: str) -> None:
        called.append(spec)

    monkeypatch.setattr(pm, "apply_suggested_options_for_plugin", fake_apply)
    app = create_app()
    app.testing = True
    c = app.test_client()
    r = c.post(
        "/api/v1/plugins/apply-defaults",
        data=json.dumps({"plugin": "tmux-plugins/tmux-sensible"}),
        content_type="application/json",
    )
    assert r.status_code == 200
    assert called == ["tmux-plugins/tmux-sensible"]


def test_replace_plugin_option_block() -> None:
    frag = (
        "set -g @plugin 'tmux-plugins/tpm'\n"
        "set -g @plugin 'a/b'\n"
        "set -g @drop 'yes'\n"
        "set -g @plugin 'c/d'\n"
        "run 'tpm'\n"
    )
    new_body, ok = pm._replace_plugin_option_block(frag, "a/b", ["set -g @x '1'"])
    assert ok is True
    assert "set -g @x '1'" in new_body
    assert "set -g @drop 'yes'" not in new_body
    assert "set -g @plugin 'c/d'" in new_body


def test_replace_plugin_option_block_marked() -> None:
    frag = (
        "set -g @plugin 'tmux-plugins/tpm'\n"
        "set -g @plugin 'a/b'\n"
        "run '/tpm'\n"
        "\n"
        "# dmux:opts:a/b\n"
        "OLD\n"
        "# dmux:opts:c/d\n"
        "KEEP\n"
    )
    new_body, ok = pm._replace_plugin_option_block(frag, "a/b", ["NEW1", "NEW2"])
    assert ok is True
    assert "NEW1" in new_body
    assert "OLD" not in new_body
    assert "KEEP" in new_body


def test_render_fragment_inserts_doc_defaults(monkeypatch) -> None:
    monkeypatch.setattr(
        pm,
        "tmux_option_lines_for_plugin",
        lambda s: ["set -g @x 'y'"] if s == "a/b" else [],
    )
    text = pm._render_fragment(["a/b"])
    assert "set -g @plugin 'a/b'" in text
    assert "set -g @x 'y'" in text
    assert "# dmux:opts:a/b" in text
    assert text.index("run '") < text.index("set -g @x 'y'")


def test_render_fragment_freed_wu_status_lines_before_run_tpm(monkeypatch) -> None:
    def fake_lines(spec: str) -> list[str]:
        if spec == pm.FREED_WU_STATUS_BAR_SPEC:
            return ["# c", "set -g status-left X"]
        if spec == "tmux-plugins/tmux-sensible":
            return ["set -g @foo '1'"]
        return []

    monkeypatch.setattr(pm, "tmux_option_lines_for_plugin", fake_lines)
    text = pm._render_fragment([pm.FREED_WU_STATUS_BAR_SPEC, "tmux-plugins/tmux-sensible"])
    assert text.index("set -g status-left") < text.index("run '")
    assert text.index("run '") < text.index("set -g @foo")


def test_normalize_freed_wu_moves_opts_before_tpm() -> None:
    bad = """set -g @plugin 'x'
run '/path/tpm/tpm'
# --- after ---
# dmux:opts:Freed-Wu/tmux-status-bar
set -g status-left Z
"""
    fixed = pm.normalize_freed_wu_opts_before_tpm(bad)
    assert fixed.index("set -g status-left") < fixed.index("run '")


def test_strip_status_strftime_percent_s_lead() -> None:
    raw = 'set -g status-right "#{status-right:%s;white,colour04,#{session_name};}"'
    out = pm._strip_status_strftime_percent_s_leads(raw)
    assert "%s" not in out
    assert "#{status-right:white,colour04" in out
    raw2 = 'set -g status-right "#{status-right:%%s;white,colour04,x;}"'
    out2 = pm._strip_status_strftime_percent_s_leads(raw2)
    assert "#{status-right:white,colour04" in out2


def test_plugins_catalog_fallback_when_github_fails(monkeypatch) -> None:
    def _boom(req: object, timeout: float = 0) -> None:
        raise pm.urllib.error.URLError("no route to host")

    monkeypatch.setattr(ssl_fetch, "urlopen", _boom)
    pm._CATALOG_CACHE = None  # type: ignore[attr-defined]

    plugins, err = pm.official_tmux_plugins_catalog()
    assert err is not None
    assert "Network unreachable" in err or "no route" in err.lower()
    assert "tmux-plugins/tmux-sensible" in plugins
    assert len(plugins) >= 10

    app = create_app()
    app.testing = True
    c = app.test_client()
    r = c.get("/api/v1/plugins/catalog")
    assert r.status_code == 200
    body = r.get_json()
    assert "tmux-plugins/tmux-sensible" in body["plugins"]
    assert body.get("error") is not None


def test_remove_tmux_conf_hook_strips_block_with_utf8_bom(tmp_path, monkeypatch) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.delenv("DMUX_TMUX_CONF", raising=False)
    conf = fake_home / ".tmux.conf"
    conf.write_bytes(
        "\ufeff".encode("utf-8")
        + (
            f"{pm.MARKER_BEGIN}\n"
            "source-file 'x'\n"
            f"{pm.MARKER_END}\n"
        ).encode("utf-8"),
    )
    removed, _detail = pm.remove_tmux_conf_hook()
    assert removed is True
    assert pm.MARKER_BEGIN not in conf.read_text(encoding="utf-8")


def test_remove_tmux_conf_hook_strips_block(tmp_path, monkeypatch) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.delenv("DMUX_TMUX_CONF", raising=False)
    conf = fake_home / ".tmux.conf"
    conf.write_text(
        "# preamble\n"
        f"{pm.MARKER_BEGIN}\n"
        "source-file '/x/plugins.tmux'\n"
        f"{pm.MARKER_END}\n"
        "# tail\n",
        encoding="utf-8",
    )
    removed, _detail = pm.remove_tmux_conf_hook()
    assert removed is True
    text = conf.read_text(encoding="utf-8")
    assert pm.MARKER_BEGIN not in text
    assert pm.MARKER_END not in text
    assert "preamble" in text
    assert "tail" in text


def test_remove_tmux_conf_hook_no_markers(tmp_path, monkeypatch) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.delenv("DMUX_TMUX_CONF", raising=False)
    conf = fake_home / ".tmux.conf"
    conf.write_text("set -g x 1\n", encoding="utf-8")
    removed, _detail = pm.remove_tmux_conf_hook()
    assert removed is False
    assert conf.read_text(encoding="utf-8") == "set -g x 1\n"


def test_plugins_remove_tmux_hook_api(tmp_path, monkeypatch) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.delenv("DMUX_TMUX_CONF", raising=False)
    conf = fake_home / ".tmux.conf"
    conf.write_text(
        f"{pm.MARKER_BEGIN}\nsource-file 'x'\n{pm.MARKER_END}\n",
        encoding="utf-8",
    )
    app = create_app()
    app.testing = True
    c = app.test_client()
    r = c.post(
        "/api/v1/plugins/remove-tmux-hook",
        data="{}",
        content_type="application/json",
    )
    assert r.status_code == 200
    j = r.get_json()
    assert j.get("ok") is True
    assert j.get("removed") is True
    assert pm.MARKER_BEGIN not in conf.read_text(encoding="utf-8")


def test_awesome_catalog_loaded() -> None:
    """The bundled `tmux-plugins/list` mirror loads with categorized entries."""
    entries = pm._awesome_catalog_entries()  # type: ignore[attr-defined]
    assert len(entries) > 80, f"awesome list should ship ~100 plugins, got {len(entries)}"
    by_spec = {e["spec"]: e for e in entries}
    assert "catppuccin/tmux" in by_spec
    assert by_spec["catppuccin/tmux"]["category"] == "Themes"
    assert "tmux-plugins/tmux-sensible" in by_spec
    assert "Freed-Wu/tmux-status-bar" in by_spec
    cats = {e["category"] for e in entries}
    assert {"General", "Status Bar", "Sessions", "Themes", "Copy Mode", "Navigation"} <= cats
    for e in entries:
        assert "/" in e["spec"] and len(e["spec"].split("/")) == 2, f"bad spec {e['spec']}"


def test_plugin_catalog_entries_offline(monkeypatch) -> None:
    """When GitHub is unreachable, catalog still serves the awesome list."""

    def boom(req: object, timeout: float = 0) -> object:
        raise OSError("network down")

    monkeypatch.setattr(ssl_fetch, "urlopen", boom)
    pm._CATALOG_CACHE = None  # type: ignore[attr-defined]

    entries, err = pm.plugin_catalog_entries()
    assert err is not None, "should report network error hint"
    assert len(entries) > 80
    specs = {e["spec"] for e in entries}
    assert "catppuccin/tmux" in specs
    assert "tmux-plugins/tpm" in specs


def test_plugin_install_url_for_third_party_spec() -> None:
    """`tpm_install_one` must accept any user/repo (not only tmux-plugins/*)."""
    spec = "catppuccin/tmux"
    assert pm._validate_plugin_spec(spec) == spec  # type: ignore[attr-defined]
    main, branch = pm._split_plugin_branch(spec)  # type: ignore[attr-defined]
    assert main == spec and branch is None
    assert pm._plugin_dir_name(spec) == "tmux"  # type: ignore[attr-defined]


def _minimal_snapshot(label: str = "default") -> Snapshot:
    return Snapshot(
        label=label,
        created_unix=1700000000.0,
        sessions=(
            SnapshotSession(
                name="alpha",
                windows=(
                    SnapshotWindow(
                        0,
                        "shell",
                        None,
                        True,
                        (SnapshotPane(0, "/tmp", 80, 24, True), SnapshotPane(1, "/var", 80, 24, False)),
                    ),
                ),
            ),
            SnapshotSession(
                name="beta",
                windows=(SnapshotWindow(0, "logs", "tiled", True, (SnapshotPane(0, "/", 80, 24, True),)),),
            ),
        ),
    )


def test_state_manager_list_snapshots_includes_summary(tmp_path: Path) -> None:
    db = tmp_path / "snap.db"
    sm = StateManager(db_path=db)
    sid = sm.save_snapshot(_minimal_snapshot(), is_auto=False)
    rows = sm.list_snapshots()
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == sid
    assert row["label"] == "default"
    assert row["is_auto"] == 0
    summary = row["summary"]
    assert summary["session_count"] == 2
    assert summary["window_count"] == 2
    assert summary["pane_count"] == 3
    assert summary["session_names"] == ["alpha", "beta"]


def test_state_manager_load_by_id(tmp_path: Path) -> None:
    from dmux.exceptions import SnapshotIdNotFoundError

    db = tmp_path / "snap2.db"
    sm = StateManager(db_path=db)
    sid = sm.save_snapshot(_minimal_snapshot(), is_auto=False)
    loaded = sm.load_by_id(sid)
    assert loaded.label == "default"
    assert tuple(s.name for s in loaded.sessions) == ("alpha", "beta")
    try:
        sm.load_by_id(99999)
    except SnapshotIdNotFoundError as e:
        assert e.snapshot_id == 99999
    else:
        raise AssertionError("expected SnapshotIdNotFoundError")


def test_snapshots_api_list_and_restore(monkeypatch: object, tmp_path: Path) -> None:
    monkeypatch.setattr("dmux.paths.data_home", lambda: tmp_path)
    snap = _minimal_snapshot()
    sid = StateManager().save_snapshot(snap, is_auto=True)

    restored: list[object] = []

    def fake_restore(self: object, s: object, *, kill_existing: bool = False) -> None:
        restored.append((tuple(x.name for x in s.sessions), kill_existing))  # type: ignore[attr-defined]

    monkeypatch.setattr("dmux.api.app.TmuxService.restore_snapshot", fake_restore)

    app = create_app()
    app.testing = True
    c = app.test_client()
    r = c.get("/api/v1/snapshots")
    assert r.status_code == 200
    body = r.get_json()
    assert len(body["snapshots"]) >= 1
    hit = next(x for x in body["snapshots"] if x["id"] == sid)
    assert hit["summary"]["pane_count"] == 3
    assert hit["summary"]["session_names"] == ["alpha", "beta"]

    r2 = c.post(
        "/api/v1/snapshots/restore",
        data=json.dumps({"id": sid, "kill_existing": True}),
        content_type="application/json",
    )
    assert r2.status_code == 200
    assert r2.get_json() == {"ok": True}
    assert restored == [(("alpha", "beta"), True)]

    r3 = c.post("/api/v1/snapshots/restore", data=json.dumps({}), content_type="application/json")
    assert r3.status_code == 400

    r4 = c.post(
        "/api/v1/snapshots/restore",
        data=json.dumps({"id": 999999999}),
        content_type="application/json",
    )
    assert r4.status_code == 404

    r5 = c.delete(f"/api/v1/snapshots/{sid}")
    assert r5.status_code == 200
    assert r5.get_json() == {"ok": True}
    r6 = c.delete(f"/api/v1/snapshots/{sid}")
    assert r6.status_code == 404
