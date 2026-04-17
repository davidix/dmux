"""Flask application factory and JSON API."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from flask import Flask, jsonify, request
from flask.typing import ResponseReturnValue
from werkzeug.wrappers import Response as WsgiResponse

from dmux.exceptions import (
    DmuxError,
    PaneNotFoundError,
    PluginManagerError,
    SessionExistsError,
    SessionNotFoundError,
    WindowNotFoundError,
)
from dmux.persistence.state_manager import StateManager
from dmux.plugin_doc_defaults import tmux_option_lines_for_plugin
from dmux.services import plugin_manager as tpm
from dmux.services.github_plugin_help import github_plugin_help as plugin_github_help
from dmux.services.tmux_service import LayoutKind, TmuxService

_PKG = Path(__file__).resolve().parent.parent


def create_app(*, socket_path: str | None = None) -> Flask:
    tmux = TmuxService(socket_path=socket_path)
    state = StateManager()

    app = Flask(
        __name__,
        static_folder=str(_PKG / "web" / "static"),
        template_folder=str(_PKG / "web" / "templates"),
    )

    @app.after_request
    def _no_store_api(response: WsgiResponse) -> WsgiResponse:
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/health")
    def health() -> ResponseReturnValue:
        return {"status": "ok"}, 200

    @app.get("/api/v1/sessions")
    def list_sessions() -> ResponseReturnValue:
        tmux.refresh()
        sessions = tmux.list_sessions()
        return jsonify({"sessions": [asdict(s) for s in sessions]}), 200

    @app.post("/api/v1/sessions")
    def create_session() -> ResponseReturnValue:
        data = request.get_json(silent=True) or {}
        name = str(data.get("name", "")).strip()
        if not name:
            return jsonify({"error": "name required"}), 400
        cwd = data.get("cwd")
        try:
            tmux.new_session(name, cwd=cwd if isinstance(cwd, str) else None)
            tmux.refresh()
            return jsonify({"ok": True, "name": name}), 201
        except SessionExistsError as e:
            return jsonify({"error": str(e)}), 409

    @app.patch("/api/v1/sessions/rename")
    def rename_session_api() -> ResponseReturnValue:
        data = request.get_json(silent=True) or {}
        old = str(data.get("from", "")).strip()
        new = str(data.get("to", "")).strip()
        if not old or not new:
            return jsonify({"error": "from and to names required"}), 400
        if old == new:
            return jsonify({"ok": True, "name": new}), 200
        try:
            tmux.rename_session(old, new)
            tmux.refresh()
            return jsonify({"ok": True, "name": new}), 200
        except SessionNotFoundError:
            return jsonify({"error": "session not found"}), 404
        except SessionExistsError as e:
            return jsonify({"error": str(e)}), 409

    @app.delete("/api/v1/sessions/<name>")
    def delete_session(name: str) -> ResponseReturnValue:
        try:
            tmux.kill_session(name)
            tmux.refresh()
            return jsonify({"ok": True}), 200
        except SessionNotFoundError:
            return jsonify({"error": "session not found"}), 404
        except DmuxError as e:
            return jsonify({"error": str(e)}), 400

    @app.delete("/api/v1/sessions/<name>/windows/<int:window_index>")
    def delete_window(name: str, window_index: int) -> ResponseReturnValue:
        try:
            tmux.kill_window(name, window_index)
            tmux.refresh()
            return jsonify({"ok": True}), 200
        except SessionNotFoundError:
            return jsonify({"error": "session not found"}), 404
        except WindowNotFoundError:
            return jsonify({"error": "window not found"}), 404
        except DmuxError as e:
            return jsonify({"error": str(e)}), 400

    @app.delete("/api/v1/panes/<pane_id>")
    def delete_pane(pane_id: str) -> ResponseReturnValue:
        try:
            tmux.kill_pane(pane_id)
            tmux.refresh()
            return jsonify({"ok": True}), 200
        except PaneNotFoundError:
            return jsonify({"error": "pane not found"}), 404
        except DmuxError as e:
            return jsonify({"error": str(e)}), 400

    @app.post("/api/v1/sessions/<name>/attach")
    def attach(name: str) -> ResponseReturnValue:
        try:
            tmux.get_session(name)
        except SessionNotFoundError:
            return jsonify({"error": "not found"}), 404
        hint = "use CLI `dmux attach` from a real TTY; API cannot attach your terminal"
        return jsonify({"hint": hint}), 200

    @app.post("/api/v1/sessions/<name>/windows/<int:window_index>/focus")
    def focus_window(name: str, window_index: int) -> ResponseReturnValue:
        try:
            tmux.select_window(name, window_index)
            return jsonify({"ok": True}), 200
        except SessionNotFoundError:
            return jsonify({"error": "session not found"}), 404
        except DmuxError as e:
            return jsonify({"error": str(e)}), 400

    @app.post("/api/v1/sessions/<name>/windows")
    def create_window(name: str) -> ResponseReturnValue:
        data = request.get_json(silent=True) or {}
        raw_name = data.get("name")
        win_name: str | None = None
        if isinstance(raw_name, str) and raw_name.strip():
            win_name = raw_name.strip()
        cwd = data.get("cwd")
        cwd_s: str | None = cwd if isinstance(cwd, str) and cwd.strip() else None
        try:
            tmux.new_window(name, name=win_name, cwd=cwd_s)
            tmux.refresh()
            return jsonify({"ok": True}), 201
        except SessionNotFoundError:
            return jsonify({"error": "session not found"}), 404
        except DmuxError as e:
            return jsonify({"error": str(e)}), 400

    @app.post("/api/v1/panes/<pane_id>/split")
    def split_pane(pane_id: str) -> ResponseReturnValue:
        data = request.get_json(silent=True) or {}
        vertical = bool(data.get("vertical", True))
        cwd = data.get("cwd")
        cwd_s: str | None = cwd if isinstance(cwd, str) and cwd.strip() else None
        try:
            tmux.split_pane(pane_id, vertical=vertical, cwd=cwd_s)
            tmux.refresh()
            return jsonify({"ok": True}), 200
        except PaneNotFoundError:
            return jsonify({"error": "pane not found"}), 404
        except DmuxError as e:
            return jsonify({"error": str(e)}), 400

    @app.post("/api/v1/panes/<pane_id>/focus")
    def focus_pane(pane_id: str) -> ResponseReturnValue:
        try:
            tmux.select_pane_by_id(pane_id)
            return jsonify({"ok": True}), 200
        except PaneNotFoundError:
            return jsonify({"error": "pane not found"}), 404

    @app.post("/api/v1/panes/<pane_id>/style")
    def pane_style(pane_id: str) -> ResponseReturnValue:
        """Set tmux pane colours (``set-option -p`` window-style). Font is not supported."""
        data = request.get_json(silent=True) or {}

        def _opt_colour(key: str) -> str | None:
            v = data.get(key)
            if v is None:
                return None
            if isinstance(v, str):
                s = v.strip()
                return s if s else None
            return str(v).strip() or None

        try:
            tmux.set_pane_style(
                pane_id,
                foreground=_opt_colour("foreground"),
                background=_opt_colour("background"),
            )
            tmux.refresh()
            return jsonify({"ok": True}), 200
        except PaneNotFoundError:
            return jsonify({"error": "pane not found"}), 404
        except DmuxError as e:
            return jsonify({"error": str(e)}), 400

    @app.post("/api/v1/panes/<pane_id>/resize")
    def resize_pane(pane_id: str) -> ResponseReturnValue:
        """Reserved for future resize-key injection; API shape is stable."""
        data = request.get_json(silent=True) or {}
        _ = data.get("delta_x"), data.get("delta_y")
        return (
            jsonify(
                {
                    "status": "not_implemented",
                    "message": "Pane resize will map to tmux resize-pane; not wired yet.",
                    "pane_id": pane_id,
                }
            ),
            501,
        )

    @app.post("/api/v1/sessions/<name>/layout")
    def layout(name: str) -> ResponseReturnValue:
        data = request.get_json(silent=True) or {}
        kind_raw = str(data.get("kind", "grid"))
        allowed: set[str] = {
            "grid",
            "vertical",
            "horizontal",
            "main-horizontal",
            "main-vertical",
        }
        if kind_raw not in allowed:
            return jsonify({"error": "invalid layout kind", "allowed": sorted(allowed)}), 400
        kind: LayoutKind = kind_raw  # type: ignore[assignment]
        window_index = int(data.get("window_index", 0))
        try:
            tmux.apply_layout(name, window_index, kind)
            return jsonify({"ok": True}), 200
        except SessionNotFoundError:
            return jsonify({"error": "session not found"}), 404
        except DmuxError as e:
            return jsonify({"error": str(e)}), 400

    @app.post("/api/v1/snapshots/save")
    def save_snapshot() -> ResponseReturnValue:
        data = request.get_json(silent=True) or {}
        label = str(data.get("label", "default"))
        snap = tmux.capture_snapshot(label=label)
        sid = state.save_snapshot(snap, is_auto=False)
        return jsonify({"id": sid, "label": label}), 200

    @app.get("/api/v1/snapshots")
    def list_snapshots() -> ResponseReturnValue:
        return jsonify({"snapshots": state.list_snapshots()}), 200

    @app.get("/api/v1/plugins")
    def plugins_status() -> ResponseReturnValue:
        return jsonify(tpm.status_dict()), 200

    @app.get("/api/v1/plugins/fragment")
    def plugins_fragment_file() -> ResponseReturnValue:
        """Managed TPM fragment on disk (``plugins.tmux``) for read-only UI preview."""
        p = tpm.plugins_fragment_path()
        if not p.is_file():
            return jsonify({"path": str(p), "content": "", "exists": False}), 200
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return jsonify({"error": str(e)}), 500
        return jsonify({"path": str(p), "content": text, "exists": True}), 200

    @app.put("/api/v1/plugins/fragment")
    def plugins_fragment_put() -> ResponseReturnValue:
        """Save ``plugins.tmux`` from the web editor."""
        data = request.get_json(silent=True) or {}
        content = data.get("content")
        if not isinstance(content, str):
            return jsonify({"error": "content must be a string"}), 400
        try:
            tpm.write_plugins_fragment_raw(content)
        except PluginManagerError as e:
            return jsonify({"error": str(e)}), 400
        return jsonify({"ok": True}), 200

    @app.get("/api/v1/plugins/catalog")
    def plugins_catalog() -> ResponseReturnValue:
        """Official tmux-plugins org repos (GitHub), for UI autocomplete."""
        plugins, err = tpm.official_tmux_plugins_catalog()
        return (
            jsonify(
                {
                    "plugins": plugins,
                    "source": "https://github.com/tmux-plugins",
                    "error": err,
                }
            ),
            200,
        )

    @app.get("/api/v1/plugins/help")
    def plugins_help() -> ResponseReturnValue:
        """GitHub repo description + README excerpt + suggested tmux option lines."""
        spec = str(request.args.get("plugin", "")).strip()
        if not spec:
            return jsonify({"error": "query ?plugin= is required"}), 400
        payload = plugin_github_help(spec)
        payload["suggested_tmux_lines"] = tmux_option_lines_for_plugin(spec)
        return jsonify(payload), 200

    @app.post("/api/v1/plugins")
    def plugins_add() -> ResponseReturnValue:
        data = request.get_json(silent=True) or {}
        spec = str(data.get("plugin", "")).strip()
        if not spec:
            return (
                jsonify({"error": "plugin field required (e.g. tmux-plugins/tmux-sensible)"}),
                400,
            )
        try:
            tpm.add_plugin(spec)
            return jsonify({"ok": True}), 200
        except PluginManagerError as e:
            return jsonify({"error": str(e)}), 400

    @app.delete("/api/v1/plugins")
    def plugins_remove() -> ResponseReturnValue:
        spec = str(request.args.get("plugin", "")).strip()
        if not spec:
            return jsonify({"error": "query ?plugin= is required"}), 400
        try:
            tpm.remove_plugin(spec)
            return jsonify({"ok": True}), 200
        except PluginManagerError as e:
            return jsonify({"error": str(e)}), 400

    @app.post("/api/v1/plugins/bootstrap")
    def plugins_bootstrap() -> ResponseReturnValue:
        """Create ~/.config/dmux/plugins.tmux and hook source-file into tmux.conf."""
        try:
            tpm.ensure_plugins_fragment_exists()
            changed = tpm.ensure_tmux_conf_hook()
            return jsonify({"ok": True, "tmux_conf_updated": changed}), 200
        except OSError as e:
            return jsonify({"error": str(e)}), 500

    @app.post("/api/v1/plugins/apply-defaults")
    def plugins_apply_defaults() -> ResponseReturnValue:
        """Regenerate fragment, or rewrite one plugin block (JSON body ``plugin``)."""
        data = request.get_json(silent=True) or {}
        one = str(data.get("plugin", "")).strip()
        try:
            if one:
                tpm.apply_suggested_options_for_plugin(one)
            else:
                tpm.regenerate_plugins_fragment()
            return jsonify({"ok": True}), 200
        except PluginManagerError as e:
            return jsonify({"error": str(e)}), 400

    @app.post("/api/v1/plugins/install")
    def plugins_install() -> ResponseReturnValue:
        data = request.get_json(silent=True) or {}
        one = str(data.get("plugin", "")).strip()
        try:
            msg = tpm.tpm_install_one(one) if one else tpm.tpm_install()
            return jsonify({"ok": True, "output": msg}), 200
        except PluginManagerError as e:
            return jsonify({"error": str(e)}), 500

    @app.post("/api/v1/plugins/update")
    def plugins_update() -> ResponseReturnValue:
        try:
            msg = tpm.tpm_update_all()
            return jsonify({"ok": True, "output": msg}), 200
        except PluginManagerError as e:
            return jsonify({"error": str(e)}), 500

    @app.post("/api/v1/plugins/clean")
    def plugins_clean() -> ResponseReturnValue:
        try:
            msg = tpm.tpm_clean()
            return jsonify({"ok": True, "output": msg}), 200
        except PluginManagerError as e:
            return jsonify({"error": str(e)}), 500

    @app.post("/api/v1/plugins/source")
    def plugins_source() -> ResponseReturnValue:
        try:
            msg = tpm.source_fragment_in_tmux()
            return jsonify({"ok": True, "output": msg}), 200
        except PluginManagerError as e:
            return jsonify({"error": str(e)}), 400

    @app.get("/")
    def index() -> ResponseReturnValue:
        from flask import render_template

        return render_template("index.html")

    return app
