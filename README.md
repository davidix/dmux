# dmux

Unified control layer over **tmux**: native persistence, project workspaces, fuzzy navigation, CLI, and a small web UI. Optional **TPM** ([tmux-plugins/tpm](https://github.com/tmux-plugins/tpm)) is bundled under `src/dmux/vendor/tpm` for a plugin manager (`dmux plugins`, web **Plugins** view).

## Requirements

- Python 3.10+
- `tmux` 3.2+ on `PATH`
- Install: `pip install -e .` (CLI command: **`dmux`**)

## Quick start

```bash
dmux list
dmux new mysession
dmux attach mysession
dmux save
dmux restore
dmux layout grid
dmux ui --open
```

Leave that terminal open while you use the browser. The UI is **not** a background service: if you close the terminal or stop the process, **http://127.0.0.1:8756** will stop responding. If the port is busy, run `dmux ui --port 8757`. Reinstall with `pip install -e .` if the UI shows “assets missing”.

State and SQLite live under `~/.local/share/dmux/` (or `$XDG_DATA_HOME/dmux/`).

## Tmux plugins (TPM)

The app vendors TPM next to the Python package. From a git checkout you can also use a submodule:

`git submodule add https://github.com/tmux-plugins/tpm.git src/dmux/vendor/tpm`

1. **Bootstrap** (once): `dmux plugins bootstrap` — writes `~/.config/dmux/plugins.tmux` and adds a `source-file` hook to your `tmux.conf` / `~/.config/tmux/tmux.conf`.
2. **Add** plugins: `dmux plugins add tmux-plugins/tmux-sensible` then `dmux plugins install` (or use the web UI).
3. **Reload** in tmux: `dmux plugins source` (or `tmux source-file ~/.config/dmux/plugins.tmux`).

Same flows are available under **Plugins (TPM)** in the web UI.

## Layout

- `dmux.services` — libtmux wrapper (`TmuxService`), DTOs (`schemas.py`), TPM integration (`plugin_manager.py` + `vendor/tpm/`)
- `dmux.persistence` — SQLite `StateManager` + JSON snapshot serialization
- `dmux.navigation` — fuzzy targets (stdlib `difflib`)
- `dmux.workspaces` — `.dmux/layout.json` per project root
- `dmux.modules` — clipboard + autosave daemon
- `dmux.api` — Flask JSON API + static UI under `dmux/web/`

## License

MIT
