import json

from dmux import ssl_fetch
from dmux.api.app import create_app
from dmux.services import plugin_manager as pm


def test_health() -> None:
    app = create_app()
    app.testing = True
    c = app.test_client()
    r = c.get("/api/health")
    assert r.status_code == 200
    assert r.get_json() == {"status": "ok"}


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
    assert r.get_json() == {"ok": True}
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


def test_plugins_status() -> None:
    app = create_app()
    app.testing = True
    c = app.test_client()
    r = c.get("/api/v1/plugins")
    assert r.status_code == 200
    data = r.get_json()
    assert "tpm_bundled" in data
    assert "plugins" in data


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
    assert "tmux-plugins/tpm" not in plugins

    app = create_app()
    app.testing = True
    c = app.test_client()
    r = c.get("/api/v1/plugins/catalog")
    assert r.status_code == 200
    body = r.get_json()
    assert body["source"] == "https://github.com/tmux-plugins"
    assert "tmux-plugins/tmux-sensible" in body["plugins"]
    assert body.get("error") is None


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


def test_render_fragment_inserts_doc_defaults(monkeypatch) -> None:
    monkeypatch.setattr(
        pm,
        "tmux_option_lines_for_plugin",
        lambda s: ["set -g @x 'y'"] if s == "a/b" else [],
    )
    text = pm._render_fragment(["a/b"])
    assert "set -g @plugin 'a/b'" in text
    assert "set -g @x 'y'" in text


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
