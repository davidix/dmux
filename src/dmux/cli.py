"""Typer CLI for dmux."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer

from dmux import __version__
from dmux.exceptions import (
    DmuxError,
    PluginManagerError,
    SessionExistsError,
    SessionNotFoundError,
    SnapshotNotFoundError,
)
from dmux.navigation.fuzzy import fuzzy_targets
from dmux.persistence.serialize import snapshot_from_dict
from dmux.persistence.state_manager import StateManager
from dmux.services.tmux_service import LayoutKind, TmuxService
from dmux.workspaces import find_project_root, load_project_layout, save_project_layout


def _print_version(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


app = typer.Typer(
    name="dmux",
    help="dmux — unified control layer over tmux",
    no_args_is_help=True,
    invoke_without_command=True,
)


def _service(socket: str | None) -> TmuxService:
    return TmuxService(socket_path=socket)


def _smart_attach(tmux: TmuxService, name: str) -> None:
    if os.environ.get("TMUX"):
        tmux.switch_client_session(name)
        return
    if not sys.stdout.isatty():
        typer.secho(
            "Cannot attach: not a TTY. Run this from a terminal, or use `tmux attach -t "
            f"{name}` when a terminal is available.",
            err=True,
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(1)
    tmux.attach_session(name)


@app.callback()
def _root(
    ctx: typer.Context,
    version: Annotated[
        bool,
        typer.Option("--version", callback=_print_version, is_eager=True),
    ] = False,
) -> None:
    if ctx.invoked_subcommand is None and not version:
        typer.echo(ctx.get_help())
        raise typer.Exit(0)


@app.command("list")
def cmd_list(socket: Annotated[str | None, typer.Option("--socket", "-S")] = None) -> None:
    """List sessions, windows, and panes."""
    tmux = _service(socket)
    for s in tmux.list_sessions():
        flag = "*" if s.attached else " "
        typer.echo(f"{flag} {s.name}  (id {s.session_id})")
        for w in s.windows:
            wflag = "·" if w.active else " "
            typer.echo(f"   {wflag} [{w.index}] {w.name}  layout={w.layout_name or '-'}")
            for p in w.panes:
                pflag = "▸" if p.active else " "
                typer.echo(f"      {pflag} {p.pane_id}  {p.title}  {p.cwd}")


@app.command("new")
def cmd_new(
    name: str,
    cwd: Annotated[Path | None, typer.Option("--cwd", help="Starting directory")] = None,
    window_name: Annotated[str | None, typer.Option("--window", "-n")] = None,
    attach: Annotated[bool, typer.Option("--attach/--no-attach", "-a/-A")] = False,
    socket: Annotated[str | None, typer.Option("--socket", "-S")] = None,
) -> None:
    """Create a new tmux session."""
    tmux = _service(socket)
    try:
        tmux.new_session(name, cwd=str(cwd) if cwd else None, window_name=window_name)
    except SessionExistsError as e:
        typer.secho(str(e), err=True, fg=typer.colors.RED)
        raise typer.Exit(1)
    typer.echo(f"Created session {name}")
    if attach:
        _smart_attach(tmux, name)


@app.command("attach")
def cmd_attach(
    name: str,
    socket: Annotated[str | None, typer.Option("--socket", "-S")] = None,
) -> None:
    """Attach to a session (exec tmux attach when not inside tmux)."""
    tmux = _service(socket)
    try:
        _smart_attach(tmux, name)
    except SessionNotFoundError as e:
        typer.secho(str(e), err=True, fg=typer.colors.RED)
        raise typer.Exit(1)


@app.command("kill")
def cmd_kill(
    name: str,
    socket: Annotated[str | None, typer.Option("--socket", "-S")] = None,
) -> None:
    """Kill a tmux session."""
    tmux = _service(socket)
    try:
        tmux.kill_session(name)
    except SessionNotFoundError as e:
        typer.secho(str(e), err=True, fg=typer.colors.RED)
        raise typer.Exit(1)
    typer.echo(f"Killed {name}")


@app.command("rename")
def cmd_rename(
    old: str,
    new: str,
    socket: Annotated[str | None, typer.Option("--socket", "-S")] = None,
) -> None:
    """Rename a session."""
    tmux = _service(socket)
    try:
        tmux.rename_session(old, new)
    except SessionNotFoundError as e:
        typer.secho(str(e), err=True, fg=typer.colors.RED)
        raise typer.Exit(1)
    typer.echo(f"Renamed {old} -> {new}")


@app.command("save")
def cmd_save(
    label: Annotated[str, typer.Option("--label", "-l")] = "default",
    socket: Annotated[str | None, typer.Option("--socket", "-S")] = None,
) -> None:
    """Save current tmux layout to SQLite."""
    tmux = _service(socket)
    snap = tmux.capture_snapshot(label=label)
    sm = StateManager()
    sid = sm.save_snapshot(snap, is_auto=False)
    typer.echo(f"Saved snapshot #{sid} ({label})")


@app.command("restore")
def cmd_restore(
    label: Annotated[str, typer.Option("--label", "-l")] = "default",
    kill_existing: Annotated[bool, typer.Option("--kill/--no-kill")] = False,
    socket: Annotated[str | None, typer.Option("--socket", "-S")] = None,
) -> None:
    """Restore last saved snapshot for a label."""
    tmux = _service(socket)
    sm = StateManager()
    try:
        snap = sm.load_latest(label)
    except SnapshotNotFoundError as e:
        typer.secho(str(e), err=True, fg=typer.colors.RED)
        raise typer.Exit(1)
    try:
        tmux.restore_snapshot(snap, kill_existing=kill_existing)
    except SessionExistsError as e:
        typer.secho(str(e), err=True, fg=typer.colors.RED)
        raise typer.Exit(1)
    typer.echo("Restore complete")


@app.command("layout")
def cmd_layout(
    kind: LayoutKind,
    session: Annotated[str | None, typer.Option("--session", "-s")] = None,
    window: Annotated[int, typer.Option("--window", "-w")] = 0,
    socket: Annotated[str | None, typer.Option("--socket", "-S")] = None,
) -> None:
    """Apply a layout preset to a window (grid / vertical / horizontal)."""
    tmux = _service(socket)
    try:
        if session is None:
            tmux.apply_layout_active_window(kind)
        else:
            tmux.apply_layout(session, window, kind)
    except DmuxError as e:
        typer.secho(str(e), err=True, fg=typer.colors.RED)
        raise typer.Exit(1)


autosave_app = typer.Typer(help="Periodic autosave to SQLite")


@autosave_app.command("run")
def autosave_run(
    interval: Annotated[int, typer.Option("--interval", "-i")] = 300,
    label: Annotated[str, typer.Option("--label", "-l")] = "autosave",
) -> None:
    """Run autosave loop in the foreground (background with your shell or a service)."""
    from dmux.modules.autosave import run_daemon

    run_daemon(interval_sec=interval, label=label)


app.add_typer(autosave_app, name="autosave")


workspace_app = typer.Typer(help="Per-project .dmux layouts")


@workspace_app.command("save")
def workspace_save(
    path: Annotated[Path | None, typer.Argument()] = None,
    socket: Annotated[str | None, typer.Option("--socket", "-S")] = None,
) -> None:
    """Write current snapshot JSON into ./.dmux/layout.json for this project."""
    root = find_project_root(path)
    if root is None:
        typer.secho("Could not detect project root.", err=True, fg=typer.colors.RED)
        raise typer.Exit(1)
    tmux = _service(socket)
    snap = tmux.capture_snapshot(label=str(root))
    payload = {
        "version": 1,
        "project_root": str(root),
        "snapshot": {
            "label": snap.label,
            "created_unix": snap.created_unix,
            "meta": snap.meta,
            "sessions": [
                {
                    "name": s.name,
                    "windows": [
                        {
                            "index": w.index,
                            "name": w.name,
                            "layout_name": w.layout_name,
                            "active": w.active,
                            "panes": [
                                {
                                    "index": p.index,
                                    "cwd": p.cwd,
                                    "width": p.width,
                                    "height": p.height,
                                    "active": p.active,
                                }
                                for p in w.panes
                            ],
                        }
                        for w in s.windows
                    ],
                }
                for s in snap.sessions
            ],
        },
    }
    out = save_project_layout(root, payload)
    typer.echo(f"Wrote {out}")


@workspace_app.command("load")
def workspace_load(
    path: Annotated[Path | None, typer.Argument()] = None,
    kill_existing: Annotated[bool, typer.Option("--kill/--no-kill")] = False,
    socket: Annotated[str | None, typer.Option("--socket", "-S")] = None,
) -> None:
    """Restore snapshot from ./.dmux/layout.json."""
    root = find_project_root(path)
    if root is None:
        typer.secho("Could not detect project root.", err=True, fg=typer.colors.RED)
        raise typer.Exit(1)
    data = load_project_layout(root)
    if not data or "snapshot" not in data:
        typer.secho("No layout.json for this project.", err=True, fg=typer.colors.RED)
        raise typer.Exit(1)
    snap = snapshot_from_dict(data["snapshot"])
    tmux = _service(socket)
    try:
        tmux.restore_snapshot(snap, kill_existing=kill_existing)
    except SessionExistsError as e:
        typer.secho(str(e), err=True, fg=typer.colors.RED)
        raise typer.Exit(1)
    typer.echo("Project layout restored")


app.add_typer(workspace_app, name="workspace")


plugins_app = typer.Typer(help="Tmux plugins via bundled TPM (tmux-plugins/tpm)")


@plugins_app.command("status")
def plugins_cmd_status() -> None:
    """Show configured plugins and ~/.tmux/plugins installs."""
    from dmux.services import plugin_manager as pm

    st = pm.status_dict()
    typer.echo(f"TPM bundled: {st['tpm_bundled']}")
    typer.echo(f"Fragment:   {st['fragment_path']}")
    typer.echo(f"tmux.conf:  {st['tmux_conf']}")
    if not st["tpm_bundled"]:
        typer.secho(
            "Bundled TPM missing. Clone tpm into src/dmux/vendor/tpm "
            "(see README: Tmux plugins).",
            err=True,
        )
        raise typer.Exit(1)
    plugins = st.get("plugins")
    if not isinstance(plugins, list):
        return
    for row in plugins:
        if not isinstance(row, dict):
            continue
        mark = "✓" if row.get("installed") else "·"
        typer.echo(f"  {mark} {row.get('spec')}")


@plugins_app.command("bootstrap")
def plugins_cmd_bootstrap() -> None:
    """Create ~/.config/dmux/plugins.tmux and add a source-file hook to tmux.conf."""
    from dmux.services import plugin_manager as pm

    try:
        pm.ensure_plugins_fragment_exists()
        changed = pm.ensure_tmux_conf_hook()
    except PluginManagerError as e:
        typer.secho(str(e), err=True, fg=typer.colors.RED)
        raise typer.Exit(1) from e
    typer.echo("plugins.tmux ready.")
    if changed:
        typer.echo(f"Updated {pm.user_tmux_conf_path()} with dmux source-file hook.")
    else:
        typer.echo("tmux.conf hook already present.")


@plugins_app.command("add")
def plugins_cmd_add(
    plugin: Annotated[str, typer.Argument(help="e.g. tmux-plugins/tmux-sensible")],
) -> None:
    """Add a plugin line to the managed fragment."""
    from dmux.services import plugin_manager as pm

    try:
        pm.add_plugin(plugin)
    except PluginManagerError as e:
        typer.secho(str(e), err=True, fg=typer.colors.RED)
        raise typer.Exit(1) from e
    typer.echo(f"Added {plugin}. Run: dmux plugins install")


@plugins_app.command("remove")
def plugins_cmd_remove(
    plugin: Annotated[str, typer.Argument(help="Exact spec as shown in status")],
) -> None:
    """Remove a plugin from the list."""
    from dmux.services import plugin_manager as pm

    try:
        pm.remove_plugin(plugin)
    except PluginManagerError as e:
        typer.secho(str(e), err=True, fg=typer.colors.RED)
        raise typer.Exit(1) from e
    typer.echo(f"Removed {plugin}. Run: dmux plugins clean (and install) as needed.")


@plugins_app.command("install")
def plugins_cmd_install() -> None:
    """Run TPM install_plugins (git clone missing repos)."""
    from dmux.services import plugin_manager as pm

    try:
        msg = pm.tpm_install()
    except PluginManagerError as e:
        typer.secho(str(e), err=True, fg=typer.colors.RED)
        raise typer.Exit(1) from e
    typer.echo(msg)


@plugins_app.command("update")
def plugins_cmd_update() -> None:
    """Run TPM update_plugins all."""
    from dmux.services import plugin_manager as pm

    try:
        msg = pm.tpm_update_all()
    except PluginManagerError as e:
        typer.secho(str(e), err=True, fg=typer.colors.RED)
        raise typer.Exit(1) from e
    typer.echo(msg)


@plugins_app.command("clean")
def plugins_cmd_clean() -> None:
    """Remove plugin dirs not listed in the fragment."""
    from dmux.services import plugin_manager as pm

    try:
        msg = pm.tpm_clean()
    except PluginManagerError as e:
        typer.secho(str(e), err=True, fg=typer.colors.RED)
        raise typer.Exit(1) from e
    typer.echo(msg)


@plugins_app.command("source")
def plugins_cmd_source() -> None:
    """tmux source-file the managed fragment (tmux server must be running)."""
    from dmux.services import plugin_manager as pm

    try:
        msg = pm.source_fragment_in_tmux()
    except PluginManagerError as e:
        typer.secho(str(e), err=True, fg=typer.colors.RED)
        raise typer.Exit(1) from e
    typer.echo(msg)


app.add_typer(plugins_app, name="plugins")


@app.command("pick")
def cmd_pick(
    query: Annotated[str, typer.Argument()] = "",
    socket: Annotated[str | None, typer.Option("--socket", "-S")] = None,
) -> None:
    """Fuzzy-pick a session/window/pane and focus it (best-effort)."""
    tmux = _service(socket)
    matches = fuzzy_targets(tmux.list_sessions(), query)
    if not matches:
        typer.echo("No matches.")
        raise typer.Exit(1)
    top = matches[0]
    typer.echo(
        f"Best match ({top.kind}): {top.session_name}"
        + (f" / {top.window_name}" if top.window_name else "")
        + (f" / {top.pane_title}" if top.pane_title else "")
    )
    try:
        if top.kind == "session":
            _smart_attach(tmux, top.session_name)
        elif top.kind == "window" and top.window_id:
            session = tmux.get_session(top.session_name)
            for i, w in enumerate(session.windows):
                if w.window_id == top.window_id:
                    tmux.select_window(top.session_name, i)
                    break
        elif top.kind == "pane" and top.pane_id:
            tmux.select_pane_by_id(top.pane_id)
    except DmuxError as e:
        typer.secho(str(e), err=True, fg=typer.colors.RED)
        raise typer.Exit(1)


@app.command("copy")
def cmd_copy(socket: Annotated[str | None, typer.Option("--socket", "-S")] = None) -> None:
    """Copy tmux buffer 0 to the OS clipboard (yank-like)."""
    from dmux.modules.clipboard import buffer_to_clipboard

    try:
        buf = buffer_to_clipboard(socket_path=socket)
    except subprocess.CalledProcessError:
        typer.secho("No tmux buffer available.", err=True, fg=typer.colors.RED)
        raise typer.Exit(1)
    n = len(buf.rstrip("\n"))
    typer.echo(f"Copied {n} chars to clipboard")


def _ensure_web_assets() -> None:
    """Fail fast if the package was installed without bundled web/ (e.g. broken sdist)."""
    from importlib.resources import files

    try:
        root = files("dmux")
        if not root.joinpath("web/static/app.js").is_file():
            raise FileNotFoundError("web/static/app.js")
    except (FileNotFoundError, TypeError, OSError):
        typer.secho(
            "dmux web UI files are missing. From the project root run:\n"
            "  pip install -e .",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)


@app.command("ui")
def cmd_ui(
    host: Annotated[
        str,
        typer.Option("--host", help="Bind address (use 0.0.0.0 for all interfaces)"),
    ] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", "-p")] = 8756,
    socket: Annotated[str | None, typer.Option("--socket", "-S")] = None,
    open_browser: Annotated[
        bool,
        typer.Option("--open", help="Open the UI in your default browser"),
    ] = False,
    reload: Annotated[
        bool,
        typer.Option(
            "--reload/--no-reload",
            help="Restart on Python changes; reload templates/static without cache (dev)",
        ),
    ] = True,
) -> None:
    """Run the dmux web UI + API (keeps running until Ctrl+C)."""
    import webbrowser

    from dmux.api.app import create_app

    _ensure_web_assets()
    web_app = create_app(socket_path=socket)
    if reload:
        web_app.config["TEMPLATES_AUTO_RELOAD"] = True
        web_app.jinja_env.auto_reload = True
        web_app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
    url = f"http://{host}:{port}/"
    typer.echo(f"dmux UI → {url}")
    if reload:
        typer.secho(
            "Hot reload on (Python restarts; refresh browser for JS/CSS). "
            "Use --no-reload for a stable long-running server.",
            fg=typer.colors.CYAN,
        )
    typer.echo("Leave this terminal open. Press Ctrl+C to stop the server.")
    if open_browser:
        browse = f"http://127.0.0.1:{port}/" if host in ("0.0.0.0", "::") else url
        webbrowser.open(browse)
    try:
        web_app.run(
            host=host,
            port=port,
            debug=False,
            threaded=True,
            use_reloader=reload,
        )
    except OSError as e:
        typer.secho(f"Could not listen on {host}:{port} — {e}", err=True, fg=typer.colors.RED)
        typer.secho("Another process may be using the port. Try: dmux ui --port 8757", err=True)
        raise typer.Exit(1) from e


def main() -> None:
    app()


if __name__ == "__main__":
    main()
