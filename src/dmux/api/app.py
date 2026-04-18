"""Flask application factory and JSON API."""

from __future__ import annotations

import os
import types
from dataclasses import asdict

from flask import Flask, jsonify, request
from flask.typing import ResponseReturnValue
from werkzeug.wrappers import Response as WsgiResponse

from dmux.exceptions import (
    DmuxError,
    PaneNotFoundError,
    PluginManagerError,
    SessionExistsError,
    SessionNotFoundError,
    SnapshotIdNotFoundError,
    SnapshotNotFoundError,
    WindowNotFoundError,
)
from dmux.persistence.state_manager import StateManager
from dmux.plugin_doc_defaults import tmux_option_lines_for_plugin
from dmux.services import plugin_manager as tpm
from dmux.services.github_plugin_help import github_plugin_help as plugin_github_help
from dmux.paths import resolve_dmux_web_dir
from dmux.services.tmux_service import LayoutKind, TmuxService


def _ui_allow_browser_cache() -> bool:
    """When false, HTML/static are served without validators so edits show on normal refresh."""
    return os.environ.get("DMUX_UI_ALLOW_CACHE", "").strip().lower() in ("1", "true", "yes", "on")


def create_app(*, socket_path: str | None = None) -> Flask:
    tmux = TmuxService(socket_path=socket_path)
    state = StateManager()

    web_dir = resolve_dmux_web_dir()
    app = Flask(
        __name__,
        static_folder=str(web_dir / "static"),
        template_folder=str(web_dir / "templates"),
    )
    app.config["DMUX_WEB_DIR"] = str(web_dir.resolve())
    app.config["DMUX_UI_NO_CACHE"] = not _ui_allow_browser_cache()

    # Avoid 304 + stale UI: Werkzeug conditional static responses ignore weak Cache-Control hints.
    if app.config["DMUX_UI_NO_CACHE"] and app.static_folder:

        def _send_static_no_cond(self: Flask, filename: str) -> ResponseReturnValue:
            from flask.helpers import send_from_directory

            return send_from_directory(
                self.static_folder,
                filename,
                max_age=0,
                conditional=False,
            )

        app.send_static_file = types.MethodType(_send_static_no_cond, app)

    @app.after_request
    def _cache_policy(response: WsgiResponse) -> WsgiResponse:
        path = request.path or ""
        if path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
            return response
        if app.config.get("DMUX_UI_NO_CACHE") and (path == "/" or path.startswith("/static/")):
            response.headers["Cache-Control"] = "no-store, no-cache, max-age=0, must-revalidate, private"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
            for h in ("ETag", "Last-Modified"):
                response.headers.pop(h, None)
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
        """Apply tmux ``resize-pane`` with directional cell deltas.

        Body: ``{"delta_x": int, "delta_y": int}`` — positive grows
        right/down. Each non-zero axis turns into one tmux invocation.
        """
        data = request.get_json(silent=True) or {}

        def _coerce_int(name: str) -> int:
            v = data.get(name, 0)
            try:
                return int(v)
            except (TypeError, ValueError):
                raise DmuxError(f"{name} must be an integer") from None

        try:
            dx = _coerce_int("delta_x")
            dy = _coerce_int("delta_y")
            if not dx and not dy:
                return jsonify({"error": "delta_x or delta_y must be non-zero"}), 400
            tmux.resize_pane(pane_id, delta_x=dx, delta_y=dy)
            tmux.refresh()
            return jsonify({"ok": True, "pane_id": pane_id, "delta_x": dx, "delta_y": dy}), 200
        except PaneNotFoundError:
            return jsonify({"error": "pane not found"}), 404
        except DmuxError as e:
            return jsonify({"error": str(e)}), 400

    @app.post("/api/v1/panes/<pane_id>/send-keys")
    def send_keys(pane_id: str) -> ResponseReturnValue:
        """Send text into a pane (``tmux send-keys``).

        Body: ``{"text": str, "enter": bool=True, "literal": bool=False}``.
        """
        data = request.get_json(silent=True) or {}
        text = data.get("text", "")
        if not isinstance(text, str):
            return jsonify({"error": "text must be a string"}), 400
        enter = bool(data.get("enter", True))
        literal = bool(data.get("literal", False))
        try:
            tmux.send_keys(pane_id, text, enter=enter, literal=literal)
            return jsonify({"ok": True}), 200
        except PaneNotFoundError:
            return jsonify({"error": "pane not found"}), 404
        except DmuxError as e:
            return jsonify({"error": str(e)}), 400

    @app.get("/api/v1/panes/<pane_id>/capture")
    def capture_pane(pane_id: str) -> ResponseReturnValue:
        """Return the pane's recent text (``tmux capture-pane -p``).

        Query: ``?lines=200`` (default 200, ``0`` = full history).
        """
        try:
            lines = int(request.args.get("lines", "200"))
        except ValueError:
            lines = 200
        try:
            text = tmux.capture_pane(pane_id, lines=max(0, lines))
            return jsonify({"ok": True, "pane_id": pane_id, "text": text}), 200
        except PaneNotFoundError:
            return jsonify({"error": "pane not found"}), 404
        except DmuxError as e:
            return jsonify({"error": str(e)}), 400

    @app.post("/api/v1/panes/<pane_id>/zoom")
    def zoom_pane(pane_id: str) -> ResponseReturnValue:
        """Toggle the window's zoom flag for the given pane."""
        try:
            tmux.toggle_zoom(pane_id)
            tmux.refresh()
            return jsonify({"ok": True}), 200
        except PaneNotFoundError:
            return jsonify({"error": "pane not found"}), 404
        except DmuxError as e:
            return jsonify({"error": str(e)}), 400

    @app.post("/api/v1/panes/<pane_id>/break")
    def break_pane(pane_id: str) -> ResponseReturnValue:
        """``tmux break-pane`` — move pane into its own window."""
        try:
            tmux.break_pane(pane_id)
            tmux.refresh()
            return jsonify({"ok": True}), 200
        except PaneNotFoundError:
            return jsonify({"error": "pane not found"}), 404
        except DmuxError as e:
            return jsonify({"error": str(e)}), 400

    @app.post("/api/v1/panes/<pane_id>/swap")
    def swap_pane(pane_id: str) -> ResponseReturnValue:
        """``tmux swap-pane -U/-D`` — swap with previous/next pane."""
        data = request.get_json(silent=True) or {}
        direction = str(data.get("direction", "")).strip().lower()
        if direction not in {"up", "down"}:
            return jsonify({"error": "direction must be 'up' or 'down'"}), 400
        try:
            tmux.swap_pane(pane_id, direction=direction)
            tmux.refresh()
            return jsonify({"ok": True}), 200
        except PaneNotFoundError:
            return jsonify({"error": "pane not found"}), 404
        except DmuxError as e:
            return jsonify({"error": str(e)}), 400

    @app.post("/api/v1/panes/<pane_id>/kill-others")
    def kill_other_panes(pane_id: str) -> ResponseReturnValue:
        """``tmux kill-pane -a`` — kill all other panes in this window."""
        try:
            tmux.kill_other_panes(pane_id)
            tmux.refresh()
            return jsonify({"ok": True}), 200
        except PaneNotFoundError:
            return jsonify({"error": "pane not found"}), 404
        except DmuxError as e:
            return jsonify({"error": str(e)}), 400

    @app.patch("/api/v1/sessions/<name>/windows/<int:window_index>/rename")
    def rename_window_api(name: str, window_index: int) -> ResponseReturnValue:
        data = request.get_json(silent=True) or {}
        new_name = str(data.get("name", "")).strip()
        if not new_name:
            return jsonify({"error": "name required"}), 400
        try:
            tmux.rename_window(name, window_index, new_name)
            tmux.refresh()
            return jsonify({"ok": True, "name": new_name}), 200
        except SessionNotFoundError:
            return jsonify({"error": "session not found"}), 404
        except WindowNotFoundError:
            return jsonify({"error": "window not found"}), 404
        except DmuxError as e:
            return jsonify({"error": str(e)}), 400

    @app.post("/api/v1/sessions/<name>/windows/<int:window_index>/move")
    def move_window_api(name: str, window_index: int) -> ResponseReturnValue:
        """Body: ``{"direction": "left"|"right"}`` — swap with neighbour."""
        data = request.get_json(silent=True) or {}
        direction = str(data.get("direction", "")).strip().lower()
        if direction not in {"left", "right"}:
            return jsonify({"error": "direction must be 'left' or 'right'"}), 400
        try:
            new_index = tmux.move_window(name, window_index, direction=direction)
            tmux.refresh()
            return jsonify({"ok": True, "index": new_index}), 200
        except SessionNotFoundError:
            return jsonify({"error": "session not found"}), 404
        except WindowNotFoundError:
            return jsonify({"error": "window not found"}), 404
        except DmuxError as e:
            return jsonify({"error": str(e)}), 400

    @app.post("/api/v1/sessions/<name>/windows/<int:window_index>/synchronize")
    def synchronize_window(name: str, window_index: int) -> ResponseReturnValue:
        """Toggle / set ``synchronize-panes`` for a window."""
        data = request.get_json(silent=True) or {}
        on = bool(data.get("on", True))
        try:
            tmux.set_window_synchronize(name, window_index, on=on)
            tmux.refresh()
            return jsonify({"ok": True, "on": on}), 200
        except SessionNotFoundError:
            return jsonify({"error": "session not found"}), 404
        except WindowNotFoundError:
            return jsonify({"error": "window not found"}), 404
        except DmuxError as e:
            return jsonify({"error": str(e)}), 400

    @app.get("/api/v1/server")
    def server_info() -> ResponseReturnValue:
        try:
            return jsonify(tmux.server_info()), 200
        except DmuxError as e:
            return jsonify({"error": str(e)}), 500

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

    @app.delete("/api/v1/snapshots/<int:snapshot_id>")
    def delete_snapshot_row(snapshot_id: int) -> ResponseReturnValue:
        if not state.delete_snapshot(snapshot_id):
            return jsonify({"error": str(SnapshotIdNotFoundError(snapshot_id))}), 404
        return jsonify({"ok": True}), 200

    @app.post("/api/v1/snapshots/restore")
    def restore_snapshot() -> ResponseReturnValue:
        data = request.get_json(silent=True) or {}
        kill_existing = bool(data.get("kill_existing", False))
        raw_id = data.get("id")
        raw_label = data.get("label")

        if raw_id is not None and raw_id != "":
            try:
                sid = int(raw_id)
            except (TypeError, ValueError):
                return jsonify({"error": "invalid snapshot id"}), 400
            try:
                snap = state.load_by_id(sid)
            except SnapshotIdNotFoundError as e:
                return jsonify({"error": str(e)}), 404
        elif raw_label is not None:
            label = str(raw_label).strip()
            if not label:
                return jsonify({"error": "label must be non-empty"}), 400
            try:
                snap = state.load_latest(label)
            except SnapshotNotFoundError as e:
                return jsonify({"error": str(e)}), 404
        else:
            return jsonify({"error": "Provide snapshot id or label"}), 400

        try:
            tmux.restore_snapshot(snap, kill_existing=kill_existing)
            tmux.refresh()
            return jsonify({"ok": True}), 200
        except SessionExistsError as e:
            return jsonify({"error": str(e)}), 409
        except DmuxError as e:
            return jsonify({"error": str(e)}), 400

    @app.get("/api/v1/plugins")
    def plugins_status() -> ResponseReturnValue:
        payload = tpm.status_dict(socket_path=socket_path)
        payload["tmux_socket"] = socket_path
        return jsonify(payload), 200

    @app.get("/api/v1/plugins/fragment")
    def plugins_fragment_file() -> ResponseReturnValue:
        """Managed TPM fragment on disk (``plugins.tmux``) for read-only UI preview."""
        p = tpm.plugins_fragment_path()
        display_path = tpm.resolved_plugins_fragment_path()
        if not p.is_file():
            return jsonify({"path": display_path, "content": "", "exists": False}), 200
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return jsonify({"error": str(e)}), 500
        return jsonify({"path": display_path, "content": text, "exists": True}), 200

    @app.put("/api/v1/plugins/fragment")
    def plugins_fragment_put() -> ResponseReturnValue:
        """Save ``plugins.tmux`` from the web editor (same file as ``path`` in GET)."""
        data = request.get_json(silent=True) or {}
        content = data.get("content")
        if not isinstance(content, str):
            return jsonify({"error": "content must be a string"}), 400
        try:
            tpm.write_plugins_fragment_raw(content)
        except PluginManagerError as e:
            return jsonify({"error": str(e)}), 400
        return jsonify({"ok": True, "path": tpm.resolved_plugins_fragment_path()}), 200

    @app.get("/api/v1/plugins/catalog")
    def plugins_catalog() -> ResponseReturnValue:
        """Plugin catalog for UI autocomplete.

        Returns:

        * ``plugins``: flat list of ``user/repo`` specs (back-compat).
        * ``entries``: enriched ``[{spec, category, description, source}]`` —
          merges the bundled awesome list (``tmux-plugins/list``) with the
          live ``tmux-plugins`` org repos.
        * ``categories``: ordered list of categories present.
        * ``error``: hint when the live GitHub fetch was degraded.
        """
        entries, err = tpm.plugin_catalog_entries()
        plugins = [e["spec"] for e in entries]
        cat_order: list[str] = []
        cat_seen: set[str] = set()
        for e in entries:
            c = e["category"]
            if c not in cat_seen:
                cat_seen.add(c)
                cat_order.append(c)
        return (
            jsonify(
                {
                    "plugins": plugins,
                    "entries": entries,
                    "categories": cat_order,
                    "source": tpm.AWESOME_CATALOG_SOURCE_URL,
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
        """Create ~/.config/dmux/plugins.tmux and hook source-file into ~/.tmux.conf (or DMUX_TMUX_CONF)."""
        try:
            tpm.ensure_plugins_fragment_exists()
            changed, detail = tpm.ensure_tmux_conf_hook()
            return jsonify(
                {
                    "ok": True,
                    "tmux_conf": tpm.resolved_user_tmux_conf_path(),
                    "tmux_conf_updated": changed,
                    "detail": detail,
                }
            ), 200
        except PluginManagerError as e:
            return jsonify({"error": str(e)}), 400
        except OSError as e:
            return jsonify({"error": str(e)}), 500

    @app.post("/api/v1/plugins/remove-tmux-hook")
    def plugins_remove_tmux_hook() -> ResponseReturnValue:
        """Remove the dmux source-file block from ~/.tmux.conf (or DMUX_TMUX_CONF)."""
        try:
            removed, detail = tpm.remove_tmux_conf_hook()
            return jsonify(
                {
                    "ok": True,
                    "tmux_conf": tpm.resolved_user_tmux_conf_path(),
                    "removed": removed,
                    "detail": detail,
                }
            ), 200
        except PluginManagerError as e:
            return jsonify({"error": str(e)}), 400
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

    @app.post("/api/v1/plugins/plugin-lines")
    def plugins_plugin_lines() -> ResponseReturnValue:
        """Replace option lines after a ``set -g @plugin '…'`` row (e.g. status-bar wizard)."""
        data = request.get_json(silent=True) or {}
        spec = str(data.get("plugin", "")).strip()
        lines = data.get("lines")
        if not spec:
            return jsonify({"error": "plugin field required"}), 400
        if not isinstance(lines, list) or not all(isinstance(x, str) for x in lines):
            return jsonify({"error": "lines must be an array of strings"}), 400
        try:
            tpm.apply_custom_plugin_lines(spec, lines)
        except PluginManagerError as e:
            return jsonify({"error": str(e)}), 400
        return jsonify({"ok": True}), 200

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
            result = tpm.source_fragment_in_tmux(socket_path=socket_path)
            return jsonify({
                "ok": True,
                "output": result.get("output", ""),
                "warning": result.get("warning"),
            }), 200
        except PluginManagerError as e:
            return jsonify({"error": str(e)}), 400

    @app.get("/")
    def index() -> ResponseReturnValue:
        from flask import render_template

        return render_template("index.html")

    return app
