<div align="center">

<img src="docs/assets/logo.svg" width="84" height="84" alt="dmux logo" />

# dmux

**One control layer over tmux — sessions, persistence, plugins, and a beautiful web UI.**

A modern command center for tmux. Manage sessions and panes from a typed CLI, persist them to SQLite, configure TPM plugins (and the Freed-Wu status bar) from a wizard in your browser — all without leaving your terminal flow.

[![PyPI](https://img.shields.io/pypi/v/dmux.svg?logo=pypi&logoColor=white)](https://pypi.org/project/dmux/)
[![Downloads](https://static.pepy.tech/badge/dmux/month)](https://pepy.tech/project/dmux)
[![Python](https://img.shields.io/pypi/pyversions/dmux.svg?logo=python&logoColor=white)](https://pypi.org/project/dmux/)
[![tmux](https://img.shields.io/badge/tmux-3.2%2B-1BB91F?logo=tmux&logoColor=white)](https://github.com/tmux/tmux)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](#license)
[![Tests](https://img.shields.io/badge/tests-32%20passing-brightgreen)](tests/)
[![Stars](https://img.shields.io/github/stars/davidix/dmux?style=social)](https://github.com/davidix/dmux/stargazers)

[**Quick start**](#quick-start) · [**Web UI**](#the-web-ui) · [**Plugins (TPM)**](#tmux-plugins-tpm) · [**Why dmux?**](#why-dmux) · [**Site**](https://davidix.github.io/dmux)

</div>

---

## Why dmux?

You already love tmux. dmux makes it **easier to live in**.

| Pain                                                                     | dmux fix                                                                            |
| ------------------------------------------------------------------------ | ----------------------------------------------------------------------------------- |
| Sessions vanish on reboot or accidental `kill-server`.                   | `dmux save` / `dmux restore` — full snapshots in SQLite, one command.               |
| Per-project layouts re-built from muscle memory every time.              | `.dmux/layout.json` per project root, restored on demand.                           |
| TPM plugins are great, but `~/.tmux.conf` is a wall of `set -g @plugin`. | Plugin manager UI + CLI: add, install, update, clean, source — without editing.    |
| Freed-Wu status bar is powerful but its template syntax is dense.        | A real **wizard** in the browser builds the JIT `#{status-left:…}` for you.         |
| Want a quick visual of who's running what?                               | Live web UI: pane mosaic, fuzzy filter, dark mode, glassmorphic topbar — under 1 MB. |
| Need scripts to drive tmux from CI / hooks?                              | Typed JSON API (`/api/v1/...`) you can `curl` from anywhere.                        |

Built on [`libtmux`](https://github.com/tmux-python/libtmux), [`Typer`](https://typer.tiangolo.com), and [`Flask`](https://flask.palletsprojects.com). Vendors [TPM](https://github.com/tmux-plugins/tpm) so plugin installs are zero-setup.

---

## Highlights

- **Session manager** — list, create, attach, rename, kill from one CLI, with smart attach (`switch-client` inside tmux, `attach` outside).
- **Persistence** — `dmux save` snapshots every session/window/pane (working dir, command, layout) into SQLite under `~/.local/share/dmux/`. `dmux restore` rebuilds them, even after a reboot.
- **Project workspaces** — `.dmux/layout.json` per repo root. Walk into any project, `dmux restore`, get the same layout you left.
- **Fuzzy navigation** — `dmux pick` for sessions/windows/panes via stdlib `difflib`. No fzf required.
- **Web UI** — Bootstrap 5, dark/light theme with no-flash init, live pane mosaic, plugin manager, status-bar wizard. Served by a tiny Flask app on `127.0.0.1`.
- **TPM, batteries-included** — TPM is vendored at `src/dmux/vendor/tpm`. `dmux plugins bootstrap` writes `~/.config/dmux/plugins.tmux` and hooks `~/.tmux.conf` for you.
- **Freed-Wu status-bar wizard** — segment table editor that builds JIT or AOT templates and writes them back to `plugins.tmux`. **Source in tmux** does a real reload (sources your `tmux.conf` and `refresh-client -S`), so changes show up live.
- **JSON API** — every UI action is a documented endpoint (`/api/v1/sessions`, `/api/v1/plugins/source`, etc.). Build your own dashboards or shell scripts.
- **Type-checked** — `py.typed` package, mypy-clean public surface.
- **Tested** — 32-test smoke suite covering CLI, API, plugin manager, snapshots.

---

## Requirements

- Python **3.10+**
- `tmux` **3.2+** on `PATH`
- Linux or macOS terminal (Windows users: WSL)

---

## Install

```bash
pip install dmux
```

That's it — TPM is vendored in the wheel, so the plugin manager works out of the box:

```bash
dmux --version
dmux --help
```

Prefer a hacking checkout?

```bash
git clone https://github.com/davidix/dmux.git
cd dmux
pip install -e ".[dev]"
```

> **Note:** `dmux` ships with TPM as vendored code under `src/dmux/vendor/tpm`. The PyPI wheel includes it. From a fresh git checkout you can pull it as a submodule: `git submodule update --init src/dmux/vendor/tpm`.

---

## Quick start

```bash
dmux list                        # show every session / window / pane
dmux new mysession               # create a session
dmux attach mysession            # smart attach (switch-client inside tmux)
dmux save                        # snapshot every session into SQLite
dmux restore                     # rebuild them after a reboot
dmux layout grid                 # apply a preset to the focused window
dmux pick                        # fuzzy pick session/window/pane
dmux ui --open                   # launch the web UI in your browser
```

State and SQLite live under `~/.local/share/dmux/` (or `$XDG_DATA_HOME/dmux/`).

---

## The web UI

```bash
dmux ui --open            # http://127.0.0.1:8756
dmux ui --port 8757       # if 8756 is busy
dmux ui -S /tmp/my.sock   # talk to a non-default tmux socket
```

What you get in the browser:

- **Sidebar** — every session with attach/kill controls, fuzzy filter, live counts.
- **Topbar** — sticky, glassmorphic, animated "live" pulse, light/dark toggle (persisted, no flash).
- **Pane mosaic** — every pane in the focused window as a tile, with hover lift and a corner status dot.
- **Plugins (TPM)** — list, add, remove, install, update, clean, **Source in tmux**. Inline editor for `plugins.tmux` (CodeMirror) with awesome-list autocomplete.
- **Status-bar wizard** — segment table editor for `Freed-Wu/tmux-status-bar`. Builds the JIT `#{status-left:…}` template and writes it back to `plugins.tmux`. **Source** reloads it live.

The UI is **not a background service**. Close the terminal → port stops responding. That's intentional: dmux is a tool you start when you need it, not a daemon.

> Want a tour without installing? See the [GitHub Pages site](https://davidix.github.io/dmux).

---

## Tmux plugins (TPM)

The classic TPM workflow, as a typed CLI **and** a web UI.

```bash
# 1. Bootstrap once: writes ~/.config/dmux/plugins.tmux and hooks ~/.tmux.conf
dmux plugins bootstrap

# 2. Add plugins
dmux plugins add tmux-plugins/tmux-sensible
dmux plugins add Freed-Wu/tmux-status-bar

# 3. Install
dmux plugins install

# 4. Reload in the running tmux server (does a full re-source + refresh)
dmux plugins source
```

Or skip the CLI and use the **Plugins (TPM)** view in the web UI — same flows, with autocomplete from the [tmux-plugins awesome list](https://github.com/tmux-plugins/list).

`dmux plugins source` doesn't just `source-file plugins.tmux` — it also re-sources your main `tmux.conf` (mirroring TPM's `prefix+I` reload) and runs `refresh-client -S`, so plugins that cache state at TPM init time (Freed-Wu/tmux-status-bar, dracula, …) actually pick up your edits without a `kill-server`.

---

## CLI reference

| Command                              | Purpose                                                          |
| ------------------------------------ | ---------------------------------------------------------------- |
| `dmux list`                          | List sessions, windows, panes                                    |
| `dmux new <name>`                    | Create a new session                                             |
| `dmux attach <name>`                 | Smart attach (switch-client inside tmux, attach outside)         |
| `dmux kill <name>`                   | Kill a session                                                   |
| `dmux rename <old> <new>`            | Rename a session                                                 |
| `dmux save`                          | Snapshot every session to SQLite                                 |
| `dmux restore`                       | Rebuild sessions from the latest snapshot                        |
| `dmux layout <preset>`               | Apply a preset (`even-h`, `even-v`, `main-h`, `main-v`, `tiled`) |
| `dmux pick`                          | Fuzzy pick a session / window / pane                             |
| `dmux copy`                          | Copy current tmux buffer to system clipboard                     |
| `dmux ui [--open] [--port] [-S]`     | Launch the web UI                                                |
| `dmux plugins {bootstrap,add,…}`     | TPM management (`status`, `add`, `remove`, `install`, `update`, `clean`, `source`) |

Run any subcommand with `--help` for full options.

---

## JSON API

Every UI action is a `curl`-able endpoint under `/api/v1/`:

```bash
curl http://127.0.0.1:8756/api/v1/sessions | jq
curl -XPOST http://127.0.0.1:8756/api/v1/sessions -d '{"name":"work"}' \
     -H 'Content-Type: application/json'
curl -XPOST http://127.0.0.1:8756/api/v1/plugins/source -d '{}' \
     -H 'Content-Type: application/json'
```

Useful endpoints:

| Method   | Path                                              | Use                                  |
| -------- | ------------------------------------------------- | ------------------------------------ |
| `GET`    | `/api/health`                                     | Liveness probe                       |
| `GET`    | `/api/v1/sessions`                                | List every session/window/pane       |
| `POST`   | `/api/v1/sessions`                                | Create a session                     |
| `DELETE` | `/api/v1/sessions/<name>`                        | Kill                                 |
| `POST`   | `/api/v1/sessions/<name>/layout`                 | Apply layout preset                  |
| `POST`   | `/api/v1/snapshots/save` / `GET /api/v1/snapshots` | Save / list snapshots              |
| `GET`    | `/api/v1/plugins`                                 | TPM status + configured plugins      |
| `GET`/`PUT` | `/api/v1/plugins/fragment`                      | Read / write `plugins.tmux`          |
| `POST`   | `/api/v1/plugins/{install,update,clean,source}`   | TPM operations                       |
| `POST`   | `/api/v1/plugins/plugin-lines`                    | Replace one plugin's option block    |

---

## Architecture

```
src/dmux/
├── api/             Flask app + JSON routes
├── cli.py           Typer CLI (dmux, dmux plugins …)
├── modules/         clipboard + autosave daemon
├── navigation/      stdlib-only fuzzy targets
├── persistence/     SQLite StateManager + JSON snapshots
├── services/        libtmux wrapper, TPM integration
├── workspaces/      .dmux/layout.json per project root
├── web/             Bootstrap 5 UI (templates/ + static/)
├── vendor/tpm/      Vendored tmux-plugins/tpm
└── data/            Awesome-list catalog + plugin defaults
```

No background processes. No daemons. The web UI runs only while `dmux ui` is in the foreground.

---

## Configuration

| Environment variable    | Effect                                                              |
| ----------------------- | ------------------------------------------------------------------- |
| `XDG_DATA_HOME`         | Override `~/.local/share/dmux/` for SQLite + snapshots              |
| `XDG_CONFIG_HOME`       | Override `~/.config/dmux/` for `plugins.tmux`                       |
| `DMUX_TMUX_CONF`        | Use a different tmux.conf (e.g. `~/.config/tmux/tmux.conf`)         |

---

## Development

```bash
git clone https://github.com/davidix/dmux.git
cd dmux
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest                                    # 32-test smoke suite
ruff check src tests                      # lint
mypy src                                  # type-check
dmux ui --open                            # try the UI
```

---

## FAQ

**Is this a tmux replacement?** No. dmux is a control layer *on top of* tmux. The tmux server, plugins, and `~/.tmux.conf` you already have keep working.

**Does it work without TPM?** Yes — every session/persistence/UI feature works without ever touching plugins. TPM integration is opt-in via `dmux plugins bootstrap`.

**Is the web UI safe to expose?** It's bound to `127.0.0.1` by design. There is no auth. **Don't** put it behind a public reverse proxy without adding one.

**Why "dmux"?** The `d` is for "dashboard" — and it tab-completes faster than `tmuxp`.

**Can I drive it from CI / cron?** Yes — every action has a stable CLI command and a JSON endpoint.

**Does `dmux plugins source` actually reload everything?** It sources `plugins.tmux`, then your `tmux.conf` (TPM's own `prefix+I` strategy), then `refresh-client -S`. Plugins that cache state at init pick up changes without a server restart.

---

## Roadmap

- [ ] Public PyPI release
- [ ] `dmux watch` — live snapshot daemon (opt-in)
- [ ] Theme presets in the web UI
- [ ] Per-pane shell history capture into snapshots
- [ ] `dmux export --format=tmuxp` interop

PRs welcome.

---

## Contributing

1. Fork → feature branch → PR.
2. Keep `pytest` green and `ruff check` clean.
3. Don't touch `src/dmux/vendor/tpm/` (vendored upstream).
4. Add a smoke test for any new endpoint or CLI command.

If dmux saved you 10 minutes today, please **[star the repo](https://github.com/davidix/dmux/stargazers)** — it's the cheapest way to fund more.

---

## License

MIT — see [LICENSE](LICENSE).
TPM is bundled under its own MIT license (see `src/dmux/vendor/tpm/LICENSE.md`).

<div align="center">

Built with ♥ on top of [tmux](https://github.com/tmux/tmux), [libtmux](https://github.com/tmux-python/libtmux), and [TPM](https://github.com/tmux-plugins/tpm).

</div>
