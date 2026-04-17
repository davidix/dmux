/**
 * dmux web UI — sessions, windows, pane focus, layouts, snapshot save.
 */

const LAYOUTS = [
  { kind: "grid", label: "Grid" },
  { kind: "vertical", label: "Vertical" },
  { kind: "horizontal", label: "Horizontal" },
  { kind: "main-horizontal", label: "Main H" },
  { kind: "main-vertical", label: "Main V" },
];

let sessionsData = [];
let selectedName = null;
let refreshTimer = null;
let loadGeneration = 0;
/** @type {"sessions" | "plugins"} */
let mainView = "sessions";
/** Official tmux-plugins GitHub org specs; filled by ensurePluginCatalog(). */
let pluginCatalogSpecs = [];
let pluginCatalogLoadFailed = false;
let pluginCatalogLoadPromise = null;

/** Bundled list when /api/v1/plugins/catalog fails (must stay in sync with plugin_manager._FALLBACK_TMUX_PLUGINS). */
const PLUGIN_CATALOG_STATIC_FALLBACK = [
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
];

const el = (id) => document.getElementById(id);

/** Same-origin API base (avoids wrong host when opened via unusual URLs). */
function apiUrl(path) {
  try {
    return new URL(path, window.location.href).href;
  } catch {
    return path;
  }
}

function toast(message, type = "ok") {
  const wrap = el("toasts");
  const t = document.createElement("div");
  t.className = `toast ${type === "err" ? "err" : "ok"}`;
  t.setAttribute("role", "status");
  const icon = document.createElement("span");
  icon.className = "toast-icon";
  icon.setAttribute("aria-hidden", "true");
  icon.textContent = type === "err" ? "✕" : "✓";
  const msg = document.createElement("div");
  msg.className = "toast-msg";
  msg.textContent = message;
  t.appendChild(icon);
  t.appendChild(msg);
  wrap.appendChild(t);
  setTimeout(() => {
    t.style.opacity = "0";
    t.style.transition = "opacity 0.25s ease";
    setTimeout(() => t.remove(), 260);
  }, 4800);
}

/** Last-fetch time in the browser’s local zone (for comparison with your system clock). */
function formatSyncTime() {
  return new Date().toLocaleString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
    timeZoneName: "short",
  });
}

function setConnectionStatus(ok) {
  const pill = el("status-pill");
  const label = el("status-label");
  const block = el("status-block");
  if (pill) {
    pill.classList.remove("ok", "err");
    pill.classList.add(ok ? "ok" : "err");
  }
  if (label) {
    label.textContent = ok ? "Live" : "Offline";
  }
  if (block) {
    block.setAttribute("aria-label", ok ? "Connected to API" : "Cannot reach API");
  }
}

function updateDocumentTitle() {
  const err = el("api-error");
  if (err && !err.classList.contains("hidden")) {
    document.title = "dmux — offline";
    return;
  }
  if (mainView === "plugins") {
    document.title = "Plugins — dmux";
    return;
  }
  document.title = selectedName ? `${selectedName} — dmux` : "dmux";
}

function applyMainView() {
  const vs = el("view-sessions");
  const vp = el("view-plugins");
  const ns = el("nav-sessions");
  const np = el("nav-plugins");
  const tw = el("topbar-toggle-wrap");
  const sa = el("topbar-session-actions");
  const isS = mainView === "sessions";
  if (vs) vs.classList.toggle("hidden", !isS);
  if (vp) vp.classList.toggle("hidden", isS);
  if (ns) {
    ns.classList.toggle("active", isS);
    ns.setAttribute("aria-selected", isS ? "true" : "false");
  }
  if (np) {
    np.classList.toggle("active", !isS);
    np.setAttribute("aria-selected", isS ? "false" : "true");
  }
  if (tw) tw.classList.toggle("hidden", !isS);
  if (sa) sa.classList.toggle("hidden", !isS);
  updateDocumentTitle();
  if (!isS) {
    loadPluginsPanel();
    ensurePluginCatalog();
  }
}

async function fetchPluginsStatus() {
  const res = await fetch(apiUrl("/api/v1/plugins"), {
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

/** Load GET /api/v1/plugins/catalog once; fills pluginCatalogSpecs. */
function ensurePluginCatalog() {
  if (pluginCatalogLoadPromise) return pluginCatalogLoadPromise;
  const hint = el("plugin-spec-hint");
  pluginCatalogLoadPromise = (async () => {
    const setHintDefault = () => {
      if (!hint) return;
      hint.innerHTML =
        'Suggestions from <a href="https://github.com/tmux-plugins" target="_blank" rel="noopener noreferrer">tmux-plugins</a> on GitHub';
    };
    const setHintDegraded = (detail) => {
      if (!hint) return;
      hint.textContent = `Curated plugin list (${detail}). You can still type any user/repo (e.g. tmux-plugins/tmux-sensible).`;
    };
    try {
      const res = await fetch(apiUrl("/api/v1/plugins/catalog"), {
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
      const data = await res.json().catch(() => ({}));
      const fromApi = Array.isArray(data.plugins) ? data.plugins : [];
      pluginCatalogSpecs =
        fromApi.length > 0 ? fromApi : PLUGIN_CATALOG_STATIC_FALLBACK.slice();
      pluginCatalogLoadFailed = pluginCatalogSpecs.length === 0;
      if (!res.ok) {
        pluginCatalogSpecs = PLUGIN_CATALOG_STATIC_FALLBACK.slice();
        pluginCatalogLoadFailed = false;
        setHintDegraded(`API HTTP ${res.status}`);
        return;
      }
      if (data.error) {
        setHintDegraded(data.error);
      } else {
        setHintDefault();
      }
    } catch (e) {
      pluginCatalogSpecs = PLUGIN_CATALOG_STATIC_FALLBACK.slice();
      pluginCatalogLoadFailed = false;
      setHintDegraded(String(e.message || e) || "network error");
    }
  })();
  return pluginCatalogLoadPromise;
}

/** Combobox + chevron: full catalog dropdown, filter-as-you-type. */
function setupPluginSpecAutocomplete() {
  const input = el("plugin-spec");
  const list = el("plugin-spec-suggest");
  const btn = el("btn-plugin-spec-dropdown");
  const combo = input?.closest(".plugins-add-combo");
  if (!input || !list || !combo) return;

  let highlight = -1;

  function filterSpecs(query) {
    const q = String(query || "").trim().toLowerCase();
    if (!pluginCatalogSpecs.length) return [];
    if (!q) return pluginCatalogSpecs.slice();
    return pluginCatalogSpecs.filter((s) => s.toLowerCase().includes(q));
  }

  function setHighlight(items) {
    items.forEach((li, i) => {
      const on = i === highlight;
      li.setAttribute("aria-selected", on ? "true" : "false");
      if (on) li.scrollIntoView({ block: "nearest" });
    });
  }

  function setExpanded(open) {
    const v = open ? "true" : "false";
    input.setAttribute("aria-expanded", v);
    if (btn) btn.setAttribute("aria-expanded", v);
  }

  function closeList() {
    list.innerHTML = "";
    list.hidden = true;
    setExpanded(false);
    highlight = -1;
  }

  function renderSuggestions(matches) {
    list.innerHTML = "";
    highlight = -1;
    if (!matches.length) {
      closeList();
      return;
    }
    matches.forEach((spec, i) => {
      const li = document.createElement("li");
      li.setAttribute("role", "option");
      li.setAttribute("id", `plugin-spec-opt-${i}`);
      li.textContent = spec;
      li.addEventListener("mousedown", (e) => {
        e.preventDefault();
        input.value = spec;
        closeList();
      });
      list.appendChild(li);
    });
    list.hidden = false;
    setExpanded(true);
  }

  function renderCatalogError() {
    list.innerHTML = "";
    const li = document.createElement("li");
    li.className = "plugin-spec-suggest-muted";
    li.setAttribute("role", "presentation");
    li.textContent =
      "Could not load plugin list — type user/repo (e.g. tmux-plugins/tmux-sensible)";
    list.appendChild(li);
    list.hidden = false;
    setExpanded(true);
  }

  function syncView() {
    if (
      pluginCatalogLoadFailed &&
      !pluginCatalogSpecs.length &&
      !String(input.value || "").trim()
    ) {
      renderCatalogError();
      return;
    }
    const matches = filterSpecs(input.value);
    renderSuggestions(matches);
  }

  async function openOrRefresh() {
    await ensurePluginCatalog();
    syncView();
  }

  async function toggleDropdown() {
    await ensurePluginCatalog();
    if (!list.hidden) {
      closeList();
      return;
    }
    if (pluginCatalogLoadFailed && !pluginCatalogSpecs.length) {
      renderCatalogError();
      input.focus();
      return;
    }
    renderSuggestions(pluginCatalogSpecs.slice());
    input.focus();
  }

  input.addEventListener("focus", () => {
    openOrRefresh();
  });

  input.addEventListener("input", () => {
    syncView();
  });

  if (btn) {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      toggleDropdown();
    });
  }

  input.addEventListener("keydown", (e) => {
    const items = list.querySelectorAll('li[role="option"]');

    if (e.key === "Escape") {
      if (!list.hidden) {
        e.preventDefault();
        closeList();
      }
      return;
    }

    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (list.hidden || !items.length) {
        openOrRefresh().then(() => {
          const its = list.querySelectorAll('li[role="option"]');
          if (its.length) {
            highlight = 0;
            setHighlight(its);
          }
        });
      } else {
        highlight =
          highlight < 0 ? 0 : Math.min(highlight + 1, items.length - 1);
        setHighlight(items);
      }
      return;
    }

    if (e.key === "ArrowUp") {
      if (list.hidden || !items.length) return;
      e.preventDefault();
      highlight = Math.max(highlight - 1, 0);
      setHighlight(items);
    }

    if (e.key === "Enter" && !list.hidden && highlight >= 0 && items[highlight]) {
      e.preventDefault();
      input.value = items[highlight].textContent || "";
      closeList();
    }
  });

  combo.addEventListener("focusout", (e) => {
    const next = e.relatedTarget;
    if (next && combo.contains(next)) return;
    setTimeout(() => {
      if (!combo.contains(document.activeElement)) closeList();
    }, 0);
  });
}

function pluginHelpSafeUrl(u) {
  try {
    return new URL(String(u)).href;
  } catch {
    return null;
  }
}

async function fillPluginAboutCell(td, spec) {
  const raw = String(spec || "").trim();
  if (!raw) {
    td.innerHTML = '<span class="plugins-help-muted">—</span>';
    return;
  }
  if (/^https?:\/\//i.test(raw) || raw.startsWith("git@") || raw.startsWith("ssh://")) {
    td.innerHTML =
      '<span class="plugins-help-muted">Git URL plugin — see upstream docs.</span>';
    return;
  }
  td.innerHTML =
    '<span class="plugins-help-loading" aria-busy="true">Loading…</span>';
  try {
    const res = await fetch(
      apiUrl(`/api/v1/plugins/help?plugin=${encodeURIComponent(raw)}`),
      { cache: "no-store", headers: { Accept: "application/json" } },
    );
    const j = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(j.error || `HTTP ${res.status}`);
    const desc = j.description && String(j.description).trim();
    const repoHref = j.repo_url ? pluginHelpSafeUrl(j.repo_url) : null;
    const readmeHref = j.readme_url ? pluginHelpSafeUrl(j.readme_url) : null;
    const bits = [];
    if (desc) {
      bits.push(`<p class="plugins-help-desc">${escapeHtml(desc)}</p>`);
    }
    const links = [];
    if (repoHref) {
      links.push(
        `<a href="${repoHref}" target="_blank" rel="noopener noreferrer">Repository</a>`,
      );
    }
    if (readmeHref) {
      links.push(
        `<a href="${readmeHref}" target="_blank" rel="noopener noreferrer">README</a>`,
      );
    }
    if (links.length) {
      bits.push(`<p class="plugins-help-links">${links.join(" · ")}</p>`);
    }
    const sug = Array.isArray(j.suggested_tmux_lines) ? j.suggested_tmux_lines : [];
    if (sug.length) {
      bits.push(
        `<p class="plugins-help-suggest-label">Suggested in <code>plugins.tmux</code> (from README / defaults):</p><pre class="plugins-help-suggest" role="region" aria-label="Suggested tmux options">${sug.map((l) => escapeHtml(String(l))).join("\n")}</pre>`,
      );
    }
    if (!bits.length) {
      const err = j.error && String(j.error).trim();
      td.innerHTML = `<span class="plugins-help-muted">${escapeHtml(err || "No description.")}</span>`;
      return;
    }
    td.innerHTML = bits.join("");
  } catch (e) {
    td.innerHTML = `<span class="plugins-help-muted">${escapeHtml(String(e.message || e))}</span>`;
  }
}

async function loadPluginsPanel() {
  const paths = el("plugins-paths");
  const tbody = el("plugins-tbody");
  if (!tbody) return;
  try {
    const data = await fetchPluginsStatus();
    if (paths) {
      const fr = data.fragment_path || "—";
      const tc = data.tmux_conf || "—";
      const ok = data.tpm_bundled ? "yes" : "no";
      paths.innerHTML = `TPM bundled: <strong>${ok}</strong> · fragment <code>${escapeHtml(fr)}</code> · tmux.conf <code>${escapeHtml(tc)}</code>`;
    }
    tbody.innerHTML = "";
    const rows = Array.isArray(data.plugins) ? data.plugins : [];
    if (!data.tpm_bundled) {
      const tr = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = 5;
      td.textContent =
        "Bundled TPM missing. From the repo: git clone https://github.com/tmux-plugins/tpm.git src/dmux/vendor/tpm";
      tr.appendChild(td);
      tbody.appendChild(tr);
      return;
    }
    const aboutTasks = [];
    for (const p of rows) {
      const tr = document.createElement("tr");
      const spec = String(p.spec || "");
      const dir = String(p.directory || "");
      const inst = p.installed ? "yes" : "no";
      tr.innerHTML = `<td class="plugins-spec-col">${escapeHtml(spec)}</td><td class="plugins-about-cell" data-plugin-about></td><td class="mono">${escapeHtml(dir)}</td><td>${inst}</td><td class="plugins-actions"></td>`;
      const aboutTd = tr.querySelector("[data-plugin-about]");
      if (aboutTd) aboutTasks.push(fillPluginAboutCell(aboutTd, spec));
      const actions = tr.querySelector("td:last-child");
      const isTpm = spec === "tmux-plugins/tpm";

      if (spec && !isTpm) {
        const applyBtn = document.createElement("button");
        applyBtn.type = "button";
        applyBtn.className = "btn btn-secondary";
        applyBtn.textContent = "Apply config";
        applyBtn.title =
          "Write suggested tmux options for this plugin into plugins.tmux (from README / dmux defaults)";
        applyBtn.addEventListener("click", async () => {
          applyBtn.disabled = true;
          try {
            await postPluginsApplyDefaults(spec);
            toast(`Options for ${spec} written to plugins.tmux`);
            await loadPluginsPanel();
          } catch (e) {
            toast(String(e.message || e), "err");
          } finally {
            applyBtn.disabled = false;
          }
        });
        actions.appendChild(applyBtn);
      }

      if (!p.installed && spec) {
        const installBtn = document.createElement("button");
        installBtn.type = "button";
        installBtn.className = "btn btn-primary";
        installBtn.textContent = "Install";
        installBtn.addEventListener("click", async () => {
          installBtn.disabled = true;
          try {
            const data = await postPluginsInstall(spec);
            toast(
              data.output
                ? `Install: ${String(data.output).slice(0, 200)}`
                : `Installed ${spec}`,
            );
            await loadPluginsPanel();
          } catch (e) {
            toast(String(e.message || e), "err");
          } finally {
            installBtn.disabled = false;
          }
        });
        actions.appendChild(installBtn);
      }

      if (p.installed && spec && !isTpm) {
        const rm = document.createElement("button");
        rm.type = "button";
        rm.className = "btn btn-secondary";
        rm.textContent = "Remove";
        rm.addEventListener("click", async () => {
          try {
            const r = await fetch(
              apiUrl(`/api/v1/plugins?plugin=${encodeURIComponent(spec)}`),
              { method: "DELETE", headers: { Accept: "application/json" } },
            );
            const j = await r.json().catch(() => ({}));
            if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
            toast(`Removed ${spec}`);
            await loadPluginsPanel();
          } catch (e) {
            toast(String(e.message || e), "err");
          }
        });
        actions.appendChild(rm);
      }
      if (!actions.children.length) actions.textContent = "—";
      tbody.appendChild(tr);
    }
    await Promise.all(aboutTasks);
  } catch (e) {
    if (paths) paths.textContent = String(e.message || e);
  }
}

async function postPluginsAction(path) {
  const res = await fetch(apiUrl(`/api/v1/plugins/${path}`), {
    method: "POST",
    cache: "no-store",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: "{}",
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

/** Regenerate whole fragment, or one plugin when `pluginSpec` is set. */
async function postPluginsApplyDefaults(pluginSpec) {
  const body =
    pluginSpec && String(pluginSpec).trim()
      ? JSON.stringify({ plugin: String(pluginSpec).trim() })
      : "{}";
  const res = await fetch(apiUrl("/api/v1/plugins/apply-defaults"), {
    method: "POST",
    cache: "no-store",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

/** Full install (all missing) or one plugin when `spec` is set. */
async function postPluginsInstall(spec) {
  const body = spec ? JSON.stringify({ plugin: spec }) : "{}";
  const res = await fetch(apiUrl("/api/v1/plugins/install"), {
    method: "POST",
    cache: "no-store",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

function updateSyncMeta() {
  const m = el("sync-meta");
  if (m) {
    m.textContent = `Synced ${formatSyncTime()}`;
    m.title = "Time of last successful refresh (your browser’s local timezone).";
  }
}

function setLoading(loading) {
  const panel = el("loading-panel");
  if (panel) {
    panel.classList.toggle("hidden", !loading);
    panel.setAttribute("aria-busy", loading ? "true" : "false");
  }
  const btn = el("btn-refresh");
  if (btn) {
    btn.classList.toggle("loading", loading);
    btn.disabled = loading;
  }
}

function reconcileFilterSelection() {
  const list = filteredSessions();
  const q = getFilter();
  if (q && selectedName && !list.some((s) => s.name === selectedName)) {
    selectedName = list.length ? list[0].name : null;
  }
}

async function fetchSessions() {
  const res = await fetch(apiUrl("/api/v1/sessions"), {
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function focusPane(paneId) {
  const res = await fetch(apiUrl(`/api/v1/panes/${encodeURIComponent(paneId)}/focus`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  if (!res.ok) {
    const t = await res.text();
    throw new Error(t || `HTTP ${res.status}`);
  }
}

async function focusWindow(sessionName, windowIndex) {
  const res = await fetch(
    `/api/v1/sessions/${encodeURIComponent(sessionName)}/windows/${windowIndex}/focus`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    },
  );
  if (!res.ok) throw new Error(await res.text());
}

async function applyLayout(sessionName, windowIndex, kind) {
  const res = await fetch(`/api/v1/sessions/${encodeURIComponent(sessionName)}/layout`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind, window_index: windowIndex }),
  });
  if (!res.ok) {
    const j = await res.json().catch(() => ({}));
    throw new Error(j.error || (await res.text()));
  }
}

async function newWindow(sessionName) {
  const res = await fetch(apiUrl(`/api/v1/sessions/${encodeURIComponent(sessionName)}/windows`), {
    method: "POST",
    cache: "no-store",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: "{}",
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

/** @param vertical libtmux: true = tmux split-window -v (panes stacked); false = -h (side by side) */
async function splitPaneApi(paneId, vertical) {
  const res = await fetch(apiUrl(`/api/v1/panes/${encodeURIComponent(paneId)}/split`), {
    method: "POST",
    cache: "no-store",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ vertical }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

async function createSession(name, cwd) {
  const res = await fetch("/api/v1/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, cwd: cwd || undefined }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

async function deleteSessionApi(name) {
  const res = await fetch(apiUrl(`/api/v1/sessions/${encodeURIComponent(name)}`), {
    method: "DELETE",
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

async function deleteWindowApi(sessionName, windowIndex) {
  const res = await fetch(
    apiUrl(`/api/v1/sessions/${encodeURIComponent(sessionName)}/windows/${windowIndex}`),
    {
      method: "DELETE",
      cache: "no-store",
      headers: { Accept: "application/json" },
    },
  );
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

async function deletePaneApi(paneId) {
  const res = await fetch(apiUrl(`/api/v1/panes/${encodeURIComponent(paneId)}`), {
    method: "DELETE",
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

/** Applies tmux `select-pane -P` (foreground/background). Omit API call when both colours empty to avoid resetting tmux unintentionally. */
async function postPaneTmuxStyle(paneId, { foreground, background }) {
  const res = await fetch(apiUrl(`/api/v1/panes/${encodeURIComponent(paneId)}/style`), {
    method: "POST",
    cache: "no-store",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ foreground, background }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

async function saveSnapshot(label) {
  const res = await fetch("/api/v1/snapshots/save", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ label: label || "default" }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

const PANE_TILE_STYLE_KEY = "dmuxPaneTileStyles";

function loadPaneTileStyles() {
  try {
    const raw = localStorage.getItem(PANE_TILE_STYLE_KEY);
    if (!raw) return {};
    const o = JSON.parse(raw);
    return o && typeof o === "object" ? o : {};
  } catch {
    return {};
  }
}

function savePaneTileStyles(map) {
  try {
    localStorage.setItem(PANE_TILE_STYLE_KEY, JSON.stringify(map));
  } catch (e) {
    console.warn(e);
  }
}

/** @returns {{ fontFamily?: string, color?: string, backgroundColor?: string } | null} */
function getPaneTileStyle(paneId) {
  const m = loadPaneTileStyles();
  return m[paneId] || null;
}

function setPaneTileStyleRecord(paneId, rec) {
  const m = loadPaneTileStyles();
  const next = { ...m };
  const fontFamily = (rec.fontFamily && String(rec.fontFamily).trim()) || "";
  const color = (rec.color && String(rec.color).trim()) || "";
  const backgroundColor = (rec.backgroundColor && String(rec.backgroundColor).trim()) || "";
  if (!fontFamily && !color && !backgroundColor) {
    delete next[paneId];
  } else {
    next[paneId] = {};
    if (fontFamily) next[paneId].fontFamily = fontFamily;
    if (color) next[paneId].color = color;
    if (backgroundColor) next[paneId].backgroundColor = backgroundColor;
  }
  savePaneTileStyles(next);
}

function clearPaneTileStyle(paneId) {
  setPaneTileStyleRecord(paneId, { fontFamily: "", color: "", backgroundColor: "" });
}

function applyPaneTileAppearance(tile, paneId) {
  const st = getPaneTileStyle(paneId);
  const has = st && (st.fontFamily || st.color || st.backgroundColor);
  tile.classList.toggle("pane-tile--custom", Boolean(has));
  if (!st || !has) {
    tile.style.removeProperty("font-family");
    tile.style.removeProperty("color");
    tile.style.removeProperty("background-color");
    return;
  }
  if (st.fontFamily) tile.style.fontFamily = st.fontFamily;
  else tile.style.removeProperty("font-family");
  if (st.color) tile.style.color = st.color;
  else tile.style.removeProperty("color");
  if (st.backgroundColor) tile.style.backgroundColor = st.backgroundColor;
  else tile.style.removeProperty("background-color");
}

const PANE_STYLE_FG_SWATCH_DEFAULT = "#e0e0e0";
const PANE_STYLE_BG_SWATCH_DEFAULT = "#1e1e1e";

/** @param {string} s */
function normalizeHexForColorInput(s) {
  if (!s || typeof s !== "string") return null;
  const t = s.trim();
  const m = t.match(/^#([\da-fA-F]{3}|[\da-fA-F]{6})$/);
  if (!m) return null;
  let h = t.startsWith("#") ? t : `#${t}`;
  if (h.length === 4) {
    const r = h[1];
    const g = h[2];
    const b = h[3];
    h = `#${r}${r}${g}${g}${b}${b}`;
  }
  return h.toLowerCase();
}

function syncPaneStyleColorPickerFromText(textEl, pickerEl) {
  if (!textEl || !pickerEl) return;
  const hex = normalizeHexForColorInput(textEl.value || "");
  if (hex) pickerEl.value = hex;
}

function setupPaneStyleForm() {
  const fg = el("pane-style-fg");
  const bg = el("pane-style-bg");
  const fgp = el("pane-style-fg-color");
  const bgp = el("pane-style-bg-color");
  if (!fg || !bg || !fgp || !bgp) return;
  fgp.addEventListener("input", () => {
    fg.value = fgp.value;
  });
  bgp.addEventListener("input", () => {
    bg.value = bgp.value;
  });
  fg.addEventListener("input", () => {
    syncPaneStyleColorPickerFromText(fg, fgp);
  });
  bg.addEventListener("input", () => {
    syncPaneStyleColorPickerFromText(bg, bgp);
  });
}

function openPaneStyleModal(paneId) {
  const d = el("modal-pane-style");
  const hid = el("pane-style-pane-id");
  const disp = el("pane-style-id-display");
  const font = el("pane-style-font");
  const fg = el("pane-style-fg");
  const bg = el("pane-style-bg");
  const fgp = el("pane-style-fg-color");
  const bgp = el("pane-style-bg-color");
  if (!d || !hid || !font || !fg || !bg) {
    console.error("openPaneStyleModal: missing dialog or fields", { d, hid, font, fg, bg });
    toast("Appearance dialog not found — try a hard refresh (cache).", "err");
    return;
  }
  hid.value = paneId;
  if (disp) disp.textContent = paneId;
  const st = getPaneTileStyle(paneId);
  font.value = (st && st.fontFamily) || "";
  fg.value = (st && st.color) || "";
  bg.value = (st && st.backgroundColor) || "";
  if (fgp && bgp) {
    syncPaneStyleColorPickerFromText(fg, fgp);
    if (!normalizeHexForColorInput(fg.value || "")) fgp.value = PANE_STYLE_FG_SWATCH_DEFAULT;
    syncPaneStyleColorPickerFromText(bg, bgp);
    if (!normalizeHexForColorInput(bg.value || "")) bgp.value = PANE_STYLE_BG_SWATCH_DEFAULT;
  }
  try {
    d.showModal();
  } catch (e) {
    console.error(e);
    toast(String(e.message || e), "err");
    return;
  }
  requestAnimationFrame(() => font.focus());
}

/** Cell grid size for the window (tmux #{pane_left}+#{pane_width} etc.). */
function tmuxPaneGridDimensions(panes) {
  let gw = 1;
  let gh = 1;
  for (const p of panes) {
    const pw = Math.max(Number(p.width) || 0, 1);
    const ph = Math.max(Number(p.height) || 0, 1);
    const left = Number.isFinite(Number(p.left)) ? Number(p.left) : 0;
    const top = Number.isFinite(Number(p.top)) ? Number(p.top) : 0;
    gw = Math.max(gw, left + pw);
    gh = Math.max(gh, top + ph);
  }
  return { gridW: gw, gridH: gh };
}

/** False when multiple panes share the same top-left (no layout data) — use list fallback. */
function tmuxPanePositionsDistinct(panes) {
  if (panes.length <= 1) return true;
  const keys = new Set();
  for (const p of panes) {
    const left = Number.isFinite(Number(p.left)) ? Number(p.left) : 0;
    const top = Number.isFinite(Number(p.top)) ? Number(p.top) : 0;
    keys.add(`${left},${top}`);
  }
  return keys.size > 1;
}

/**
 * @param {HTMLElement} mosaic
 * @param {object} p
 * @param {"grid" | "list"} mode
 * @param {{ totalW: number }} listCtx
 */
function appendPaneToMosaic(mosaic, p, mode, listCtx) {
  const tile = document.createElement("button");
  tile.type = "button";
  tile.className = "pane-tile" + (p.active ? " active" : "");
  tile.setAttribute("aria-pressed", p.active ? "true" : "false");
  if (p.active) {
    tile.setAttribute("aria-current", "true");
  }
  tile.title = `Focus pane ${p.pane_id}`;
  applyPaneTileAppearance(tile, p.pane_id);

  const ph = document.createElement("div");
  ph.className = "pane-tile-head";
  const pt = document.createElement("span");
  pt.className = "pane-tile-title";
  pt.textContent = p.title || "(no title)";
  const pid = document.createElement("span");
  pid.className = "pane-tile-id";
  pid.textContent = p.pane_id;
  ph.appendChild(pt);
  ph.appendChild(pid);

  const path = document.createElement("div");
  path.className = "pane-tile-path";
  path.textContent = p.cwd || "";
  path.title = p.cwd || "";

  const dim = document.createElement("div");
  dim.className = "pane-tile-dim";
  dim.textContent = `${p.width || "?"}×${p.height || "?"}`;

  tile.appendChild(ph);
  tile.appendChild(path);
  tile.appendChild(dim);

  tile.addEventListener("click", async () => {
    try {
      await focusPane(p.pane_id);
      toast("Pane focused");
      await refresh({ silent: true });
    } catch (e) {
      toast(String(e.message || e), "err");
    }
  });

  const cog = document.createElement("button");
  cog.type = "button";
  cog.className = "pane-tile-cog";
  cog.setAttribute("aria-label", `Font and colors for pane ${p.pane_id}`);
  cog.title = "Font and colors (this browser only)";
  cog.textContent = "⚙";
  cog.addEventListener(
    "click",
    (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      openPaneStyleModal(p.pane_id);
    },
    true,
  );

  const del = document.createElement("button");
  del.type = "button";
  del.className = "pane-tile-del";
  del.setAttribute("aria-label", `Close pane ${p.pane_id}`);
  del.title = "Close pane (tmux kill-pane)";
  del.textContent = "×";
  del.addEventListener(
    "click",
    (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      if (
        !confirm(
          `Close pane ${p.pane_id}? tmux cannot remove the last pane in a window — close the window instead.`,
        )
      ) {
        return;
      }
      (async () => {
        try {
          await deletePaneApi(p.pane_id);
          clearPaneTileStyle(p.pane_id);
          toast("Pane closed");
          await refresh({ silent: true });
        } catch (e) {
          toast(String(e.message || e), "err");
        }
      })();
    },
    true,
  );

  const side = document.createElement("div");
  side.className = "pane-tile-side";
  side.appendChild(cog);
  side.appendChild(del);

  if (mode === "grid") {
    const cell = document.createElement("div");
    cell.className = "pane-mosaic-cell";
    cell.setAttribute("role", "listitem");
    const pw = Math.max(Number(p.width) || 0, 1);
    const ph = Math.max(Number(p.height) || 0, 1);
    const left = Number.isFinite(Number(p.left)) ? Number(p.left) : 0;
    const top = Number.isFinite(Number(p.top)) ? Number(p.top) : 0;
    cell.style.gridColumn = `${left + 1} / span ${pw}`;
    cell.style.gridRow = `${top + 1} / span ${ph}`;
    const inner = document.createElement("div");
    inner.className = "pane-mosaic-cell-inner";
    inner.appendChild(tile);
    inner.appendChild(side);
    cell.appendChild(inner);
    mosaic.appendChild(cell);
    return;
  }

  const row = document.createElement("div");
  row.className = "pane-tile-row";
  row.setAttribute("role", "listitem");
  const grow = Math.max(p.width || 1, 1);
  const totalW = listCtx.totalW || 1;
  row.style.flex = `${grow} 1 ${Math.max(100, (grow / totalW) * 200)}px`;
  row.appendChild(tile);
  row.appendChild(side);
  mosaic.appendChild(row);
}

function getFilter() {
  return (el("filter").value || "").trim().toLowerCase();
}

function filteredSessions() {
  const q = getFilter();
  if (!q) return sessionsData;
  return sessionsData.filter((s) => s.name.toLowerCase().includes(q));
}

function findSession(name) {
  return sessionsData.find((s) => s.name === name) || null;
}

function updateSessionCount() {
  const n = el("session-count");
  if (n) n.textContent = String(sessionsData.length);
}

function renderSidebar() {
  reconcileFilterSelection();
  const nav = el("session-nav");
  if (!nav) return;
  nav.innerHTML = "";
  const list = filteredSessions();
  if (!list.length) {
    const p = document.createElement("p");
    p.className = "sidebar-sub";
    p.style.padding = "0.5rem 0.35rem";
    p.textContent = sessionsData.length ? "No matches — clear filter" : "No sessions yet";
    nav.appendChild(p);
    return;
  }
  for (const s of list) {
    const wCount = s.windows?.length || 0;
    const pCount = s.windows?.reduce((n, w) => n + (w.panes?.length || 0), 0) || 0;
    const tip = `${wCount} windows · ${pCount} panes · ${s.attached ? "attached" : "detached"}`;

    const row = document.createElement("div");
    row.className = "session-item" + (s.name === selectedName ? " active" : "");
    row.dataset.name = s.name;

    const sel = document.createElement("button");
    sel.type = "button";
    sel.className = "session-item-select";
    sel.dataset.name = s.name;
    sel.title = tip;
    const left = document.createElement("span");
    left.className = "session-item-name";
    left.textContent = s.name;
    const right = document.createElement("span");
    right.className = "session-item-meta";
    if (s.attached) {
      right.textContent = "live";
      right.title = "Session has an attached client";
    } else {
      right.textContent = `W${wCount}`;
      right.title = `${wCount} window${wCount === 1 ? "" : "s"}, ${pCount} pane${pCount === 1 ? "" : "s"} (detached)`;
    }
    sel.appendChild(left);
    sel.appendChild(right);
    sel.addEventListener("click", () => selectSession(s.name));

    const ren = document.createElement("button");
    ren.type = "button";
    ren.className = "session-rename-btn";
    ren.setAttribute("aria-label", `Rename session ${s.name}`);
    ren.title = "Rename session";
    ren.textContent = "✎";
    ren.addEventListener("click", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      openRenameModal(s.name);
    });

    const del = document.createElement("button");
    del.type = "button";
    del.className = "session-del-btn";
    del.setAttribute("aria-label", `Kill session ${s.name}`);
    del.title = "Kill session (kill-session)";
    del.textContent = "×";
    del.addEventListener("click", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      if (!confirm(`Kill session "${s.name}"? All windows and panes in this session will close.`)) {
        return;
      }
      (async () => {
        try {
          await deleteSessionApi(s.name);
          if (selectedName === s.name) {
            selectedName = null;
          }
          toast("Session closed");
          await refresh({ silent: true });
        } catch (e) {
          toast(String(e.message || e), "err");
        }
      })();
    });

    row.appendChild(sel);
    row.appendChild(ren);
    row.appendChild(del);
    nav.appendChild(row);
  }
}

function openRenameModal(fromName) {
  const d = el("modal-rename");
  const from = el("rename-from");
  const to = el("rename-to");
  if (!d || !from || !to) return;
  from.value = fromName;
  to.value = fromName;
  d.showModal();
  requestAnimationFrame(() => {
    to.focus();
    to.select();
  });
}

async function renameSessionApi(fromName, toName) {
  const res = await fetch(apiUrl("/api/v1/sessions/rename"), {
    method: "PATCH",
    cache: "no-store",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ from: fromName, to: toName }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return data;
}

function renderDetail(session) {
  const detail = el("detail");
  const empty = el("empty-state");
  if (!detail || !empty) return;
  const skipTopbar = mainView === "plugins";
  if (!session) {
    detail.classList.add("hidden");
    empty.classList.remove("hidden");
    const hasSessions = sessionsData.length > 0;
    if (!skipTopbar) {
      const vt = el("view-title");
      const vm = el("view-meta");
      if (vt) vt.textContent = "Overview";
      if (vm) {
        vm.textContent = hasSessions
          ? "Choose a session in the sidebar"
          : "No tmux sessions on this server";
      }
    }
    const lead = empty.querySelector(".empty-lead");
    const hint = empty.querySelector(".empty-hint");
    if (lead) {
      lead.textContent = hasSessions ? "Select a session" : "No sessions yet";
    }
    if (hint) {
      hint.textContent = hasSessions
        ? "Pick a session from the list, or create a new one."
        : "Create a session with New session — then attach from a terminal with dmux attach.";
    }
    return;
  }
  empty.classList.add("hidden");
  detail.classList.remove("hidden");
  if (!skipTopbar) {
    const vtitle = el("view-title");
    const vmeta = el("view-meta");
    if (vtitle) vtitle.textContent = session.name;
    const winCount = session.windows?.length || 0;
    const paneCount = session.windows?.reduce((n, w) => n + (w.panes?.length || 0), 0) || 0;
    if (vmeta) {
      vmeta.textContent = `${winCount} window${winCount === 1 ? "" : "s"} · ${paneCount} pane${paneCount === 1 ? "" : "s"} · ${
        session.attached ? "attached to a client" : "detached"
      }`;
    }
  }

  detail.innerHTML = "";
  const windows = session.windows || [];
  for (const w of windows) {
    const block = document.createElement("section");
    block.className = "win-block";

    const head = document.createElement("div");
    head.className = "win-head";

    const titleCol = document.createElement("div");
    const wt = document.createElement("div");
    wt.className = "win-title";
    const activeBadge = w.active
      ? ' <span class="win-badge" title="Active window">Active</span>'
      : "";
    wt.innerHTML = `<span class="idx">#${w.index + 1}</span>${escapeHtml(w.name || "window")}${activeBadge}`;
    const wm = document.createElement("div");
    wm.className = "win-meta";
    wm.textContent = w.layout_name ? String(w.layout_name) : "layout";
    wm.title = w.layout_name ? String(w.layout_name) : "";
    titleCol.appendChild(wt);
    titleCol.appendChild(wm);

    const addTools = document.createElement("div");
    addTools.className = "win-add-tools";
    addTools.setAttribute("role", "toolbar");
    addTools.setAttribute("aria-label", `Add window or panes in window ${w.index + 1}`);
    const addLab = document.createElement("span");
    addLab.className = "tools-label";
    addLab.textContent = "Add";
    addTools.appendChild(addLab);

    const btnNewWin = document.createElement("button");
    btnNewWin.type = "button";
    btnNewWin.className = "btn-layout btn-add";
    btnNewWin.textContent = "Window";
    btnNewWin.title = "New window in this session";
    btnNewWin.addEventListener("click", async () => {
      try {
        await newWindow(session.name);
        toast("New window created");
        await refresh({ silent: true });
      } catch (e) {
        toast(String(e.message || e), "err");
      }
    });
    addTools.appendChild(btnNewWin);

    const panesList = w.panes || [];
    const paneForSplit = panesList.find((p) => p.active) || panesList[0];
    const mkSplit = (vertical, label, title) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "btn-layout btn-add";
      b.textContent = label;
      b.title = title;
      b.disabled = !paneForSplit;
      b.addEventListener("click", async () => {
        if (!paneForSplit) return;
        try {
          await splitPaneApi(paneForSplit.pane_id, vertical);
          toast(vertical ? "Split (stacked)" : "Split (side by side)");
          await refresh({ silent: true });
        } catch (e) {
          toast(String(e.message || e), "err");
        }
      });
      return b;
    };
    addTools.appendChild(mkSplit(true, "Pane ↓", "Split active pane — new pane below (tmux split-window -v)"));
    addTools.appendChild(mkSplit(false, "Pane →", "Split active pane — new pane to the right (tmux split-window -h)"));

    const tools = document.createElement("div");
    tools.className = "layout-tools";
    tools.setAttribute("role", "toolbar");
    tools.setAttribute("aria-label", `Window ${w.index + 1} layouts`);
    const lab = document.createElement("span");
    lab.className = "tools-label";
    lab.textContent = "Layout";
    tools.appendChild(lab);
    for (const L of LAYOUTS) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "btn-layout";
      b.textContent = L.label;
      b.title = `Apply ${L.kind} layout`;
      b.addEventListener("click", async () => {
        try {
          await applyLayout(session.name, w.index, L.kind);
          toast(`Layout: ${L.label}`);
          await refresh({ silent: true });
        } catch (e) {
          toast(String(e.message || e), "err");
        }
      });
      tools.appendChild(b);
    }
    const delWin = document.createElement("button");
    delWin.type = "button";
    delWin.className = "btn-layout btn-layout-danger";
    delWin.textContent = "Close win";
    delWin.title = "Kill window (kill-window)";
    delWin.addEventListener("click", async (ev) => {
      ev.preventDefault();
      const wn = w.name || "window";
      if (
        !confirm(
          `Close window #${w.index + 1} "${wn}"? If this is the only window, tmux may end the whole session.`,
        )
      ) {
        return;
      }
      try {
        await deleteWindowApi(session.name, w.index);
        toast("Window closed");
        await refresh({ silent: true });
      } catch (e) {
        toast(String(e.message || e), "err");
      }
    });
    tools.appendChild(delWin);
    const fw = document.createElement("button");
    fw.type = "button";
    fw.className = "btn-layout primary-action";
    fw.textContent = "Focus window";
    fw.title = "Select this window in tmux";
    fw.addEventListener("click", async () => {
      try {
        await focusWindow(session.name, w.index);
        toast("Window focused");
        await refresh({ silent: true });
      } catch (e) {
        toast(String(e.message || e), "err");
      }
    });
    tools.appendChild(fw);

    head.appendChild(titleCol);
    head.appendChild(addTools);
    head.appendChild(tools);
    block.appendChild(head);

    const panes = w.panes || [];
    const totalW = panes.reduce((s, p) => s + Math.max(p.width || 0, 1), 0) || 1;
    const useTmuxGrid = tmuxPanePositionsDistinct(panes);
    const { gridW, gridH } = tmuxPaneGridDimensions(panes);

    const mosaic = document.createElement("div");
    mosaic.className = "pane-mosaic" + (useTmuxGrid ? " pane-mosaic--tmux" : " pane-mosaic--list");
    mosaic.setAttribute("role", "list");
    if (useTmuxGrid) {
      mosaic.style.setProperty("--tmux-cols", String(gridW));
      mosaic.style.setProperty("--tmux-rows", String(gridH));
    }

    const mode = useTmuxGrid ? "grid" : "list";
    for (const p of panes) {
      appendPaneToMosaic(mosaic, p, mode, { totalW });
    }
    block.appendChild(mosaic);
    detail.appendChild(block);
  }
}

function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

function selectSession(name) {
  selectedName = name;
  renderSidebar();
  renderDetail(findSession(name));
  updateDocumentTitle();
  el("main")?.focus({ preventScroll: true });
}

/**
 * @param {{ silent?: boolean }} opts
 *   silent: fetch and re-render without skeleton overlay or disabling controls (tmux sync, auto-refresh).
 */
async function refresh(opts = {}) {
  const silent = opts.silent === true;
  const errEl = el("api-error");
  const gen = ++loadGeneration;
  if (!silent) {
    setLoading(true);
  }
  try {
    const data = await fetchSessions();
    if (gen !== loadGeneration) return;

    sessionsData = Array.isArray(data.sessions) ? data.sessions : [];

    if (!silent) {
      setLoading(false);
    }

    setConnectionStatus(true);
    if (errEl) {
      errEl.classList.add("hidden");
      errEl.textContent = "";
    }
    updateSessionCount();
    updateSyncMeta();

    if (selectedName && !findSession(selectedName)) {
      selectedName = sessionsData[0]?.name ?? null;
    }
    if (!selectedName && sessionsData.length) {
      selectedName = sessionsData[0].name;
    }
    reconcileFilterSelection();
    renderSidebar();
    renderDetail(selectedName ? findSession(selectedName) : null);
    updateDocumentTitle();
  } catch (e) {
    console.error(e);
    if (gen !== loadGeneration) return;
    setConnectionStatus(false);
    if (errEl) {
      errEl.textContent = `Could not reach the API: ${String(e.message || e)}. Is dmux ui running?`;
      errEl.classList.remove("hidden");
    }
    renderDetail(null);
    if (mainView !== "plugins") {
      const vt = el("view-title");
      const vm = el("view-meta");
      if (vt) vt.textContent = "Offline";
      if (vm) vm.textContent = "Check that the server is running";
    }
    updateDocumentTitle();
  } finally {
    if (gen === loadGeneration && !silent) {
      setLoading(false);
    }
  }
}

function setupAutoRefresh() {
  if (refreshTimer) clearInterval(refreshTimer);
  refreshTimer = null;
  if (el("auto-refresh").checked) {
    refreshTimer = setInterval(() => refresh({ silent: true }), 5000);
  }
}

function updateFilterClear() {
  const clear = el("filter-clear");
  const has = Boolean((el("filter").value || "").trim());
  clear.classList.toggle("hidden", !has);
}

el("filter").addEventListener("input", () => {
  updateFilterClear();
  renderSidebar();
  renderDetail(selectedName ? findSession(selectedName) : null);
});

el("filter-clear").addEventListener("click", () => {
  el("filter").value = "";
  updateFilterClear();
  renderSidebar();
  renderDetail(selectedName ? findSession(selectedName) : null);
});

el("btn-refresh").addEventListener("click", () => refresh());

el("auto-refresh").addEventListener("change", setupAutoRefresh);

el("btn-new-session").addEventListener("click", () => {
  const d = el("modal-new");
  d.showModal();
  const inp = d.querySelector('input[name="name"]');
  if (inp) {
    inp.value = "";
    inp.focus();
  }
});

el("modal-cancel").addEventListener("click", () => el("modal-new").close());

el("modal-rename-cancel")?.addEventListener("click", () => el("modal-rename")?.close());

el("pane-style-cancel")?.addEventListener("click", () => el("modal-pane-style")?.close());

el("pane-style-reset")?.addEventListener("click", () => {
  const paneId = String(el("pane-style-pane-id")?.value || "").trim();
  if (!paneId) return;
  (async () => {
    try {
      await postPaneTmuxStyle(paneId, { foreground: null, background: null });
    } catch (e) {
      toast(String(e.message || e), "err");
      return;
    }
    setPaneTileStyleRecord(paneId, { fontFamily: "", color: "", backgroundColor: "" });
    const font = el("pane-style-font");
    const fg = el("pane-style-fg");
    const bg = el("pane-style-bg");
    const fgp = el("pane-style-fg-color");
    const bgp = el("pane-style-bg-color");
    if (font) font.value = "";
    if (fg) fg.value = "";
    if (bg) bg.value = "";
    if (fgp) fgp.value = PANE_STYLE_FG_SWATCH_DEFAULT;
    if (bgp) bgp.value = PANE_STYLE_BG_SWATCH_DEFAULT;
    toast("Pane styles cleared (tmux + web tile)");
    el("modal-pane-style")?.close();
    renderDetail(selectedName ? findSession(selectedName) : null);
  })();
});

el("form-pane-style")?.addEventListener("submit", (ev) => {
  ev.preventDefault();
  const paneId = String(el("pane-style-pane-id")?.value || "").trim();
  if (!paneId) return;
  const font = String(el("pane-style-font")?.value || "").trim();
  const fg = String(el("pane-style-fg")?.value || "").trim();
  const bg = String(el("pane-style-bg")?.value || "").trim();
  (async () => {
    if (fg || bg) {
      try {
        await postPaneTmuxStyle(paneId, {
          foreground: fg || null,
          background: bg || null,
        });
      } catch (e) {
        toast(String(e.message || e), "err");
        return;
      }
    }
    setPaneTileStyleRecord(paneId, {
      fontFamily: el("pane-style-font")?.value,
      color: el("pane-style-fg")?.value,
      backgroundColor: el("pane-style-bg")?.value,
    });
    el("modal-pane-style")?.close();
    toast(fg || bg ? "Saved (tmux pane + web tile)" : "Saved (web tile font only)");
    renderDetail(selectedName ? findSession(selectedName) : null);
  })();
});

el("form-rename")?.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const submit = el("modal-rename-submit");
  const fromName = String(el("rename-from")?.value || "").trim();
  const toName = String(el("rename-to")?.value || "").trim();
  if (!fromName || !toName) return;
  if (toName === fromName) {
    el("modal-rename")?.close();
    return;
  }
  if (submit) submit.disabled = true;
  try {
    await renameSessionApi(fromName, toName);
    if (selectedName === fromName) {
      selectedName = toName;
    }
    toast(`Renamed to “${toName}”`);
    el("modal-rename")?.close();
    await refresh({ silent: true });
  } catch (e) {
    toast(String(e.message || e), "err");
  } finally {
    if (submit) submit.disabled = false;
  }
});

document.addEventListener("keydown", (ev) => {
  const tag = (ev.target && ev.target.tagName) || "";
  const inField = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
  const inDialog = ev.target && ev.target.closest && ev.target.closest("dialog[open]");

  if (ev.key === "/" && !inField && !inDialog && mainView === "sessions") {
    ev.preventDefault();
    el("filter").focus();
    el("filter").select();
  }
  if ((ev.key === "r" || ev.key === "R") && !inField && !inDialog) {
    ev.preventDefault();
    refresh();
  }
});

el("form-new").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const submit = el("modal-submit");
  const fd = new FormData(ev.target);
  const name = String(fd.get("name") || "").trim();
  const cwd = String(fd.get("cwd") || "").trim();
  if (!name) return;
  submit.disabled = true;
  try {
    await createSession(name, cwd);
    toast(`Session “${name}” created`);
    el("modal-new").close();
    selectedName = name;
    await refresh({ silent: true });
  } catch (e) {
    toast(String(e.message || e), "err");
  } finally {
    submit.disabled = false;
  }
});

el("btn-save-snapshot").addEventListener("click", async () => {
  const btn = el("btn-save-snapshot");
  btn.disabled = true;
  try {
    const r = await saveSnapshot("default");
    toast(`Snapshot saved (#${r.id})`);
  } catch (e) {
    toast(String(e.message || e), "err");
  } finally {
    btn.disabled = false;
  }
});

el("nav-sessions")?.addEventListener("click", async () => {
  if (mainView === "sessions") return;
  mainView = "sessions";
  applyMainView();
  await refresh({ silent: true });
});

el("nav-plugins")?.addEventListener("click", () => {
  if (mainView === "plugins") return;
  mainView = "plugins";
  const vt = el("view-title");
  const vm = el("view-meta");
  const sm = el("sync-meta");
  if (vt) vt.textContent = "Plugins (TPM)";
  if (vm) vm.textContent = "Bundled tmux-plugins/tpm · ~/.tmux/plugins";
  if (sm) sm.textContent = "";
  applyMainView();
});

el("form-add-plugin")?.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const inp = el("plugin-spec");
  const spec = String(inp?.value || "").trim();
  if (!spec) return;
  try {
    const res = await fetch(apiUrl("/api/v1/plugins"), {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ plugin: spec }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    toast(`Added ${spec}`);
    if (inp) inp.value = "";
    await loadPluginsPanel();
  } catch (e) {
    toast(String(e.message || e), "err");
  }
});

async function runPluginOp(btn, path, okMsg) {
  if (!btn) return;
  btn.disabled = true;
  try {
    const data =
      path === "install"
        ? await postPluginsInstall()
        : path === "apply-defaults"
          ? await postPluginsApplyDefaults()
          : await postPluginsAction(path);
    toast(data.output ? `${okMsg}: ${data.output.slice(0, 200)}` : okMsg);
    await loadPluginsPanel();
  } catch (e) {
    toast(String(e.message || e), "err");
  } finally {
    btn.disabled = false;
  }
}

el("btn-plugins-bootstrap")?.addEventListener("click", () =>
  runPluginOp(el("btn-plugins-bootstrap"), "bootstrap", "Bootstrap done"),
);
el("btn-plugins-apply-defaults")?.addEventListener("click", () =>
  runPluginOp(
    el("btn-plugins-apply-defaults"),
    "apply-defaults",
    "Suggested options written to plugins.tmux",
  ),
);
el("btn-plugins-install")?.addEventListener("click", () =>
  runPluginOp(el("btn-plugins-install"), "install", "Install finished"),
);
el("btn-plugins-update")?.addEventListener("click", () =>
  runPluginOp(el("btn-plugins-update"), "update", "Update finished"),
);
el("btn-plugins-clean")?.addEventListener("click", () =>
  runPluginOp(el("btn-plugins-clean"), "clean", "Clean finished"),
);
el("btn-plugins-source")?.addEventListener("click", () =>
  runPluginOp(el("btn-plugins-source"), "source", "Reloaded in tmux"),
);

async function savePluginsFragmentFromModal() {
  const saveBtn = el("modal-plugins-fragment-save");
  if (saveBtn) saveBtn.disabled = true;
  try {
    const content =
      typeof globalThis.dmuxPluginsFragmentGetValue === "function"
        ? globalThis.dmuxPluginsFragmentGetValue()
        : "";
    const res = await fetch(apiUrl("/api/v1/plugins/fragment"), {
      method: "PUT",
      cache: "no-store",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    });
    const j = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(j.error || `HTTP ${res.status}`);
    toast("plugins.tmux saved");
    if (typeof globalThis.dmuxPluginsFragmentMarkClean === "function") {
      globalThis.dmuxPluginsFragmentMarkClean();
    }
    await loadPluginsPanel();
  } catch (e) {
    toast(String(e.message || e), "err");
  } finally {
    if (saveBtn) saveBtn.disabled = false;
  }
}

async function openPluginsFragmentModal() {
  const modal = el("modal-plugins-fragment");
  const pathEl = el("modal-plugins-fragment-path");
  const host = el("modal-plugins-fragment-editor-host");
  if (!modal || !pathEl || !host) return;
  pathEl.textContent = "Loading…";
  try {
    const res = await fetch(apiUrl("/api/v1/plugins/fragment"), {
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
    const j = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(j.error || `HTTP ${res.status}`);
    pathEl.textContent = j.path || "—";
    const text = j.exists
      ? String(j.content ?? "")
      : "# New fragment — Save to write plugins.tmux\n";
    if (typeof globalThis.CodeMirror === "undefined" || typeof globalThis.dmuxInitPluginsFragmentEditor !== "function") {
      toast("CodeMirror editor did not load — check network (CDN)", "err");
      return;
    }
    globalThis.dmuxInitPluginsFragmentEditor(host);
    globalThis.dmuxPluginsFragmentSetContent(text);
    modal.showModal();
    requestAnimationFrame(() => {
      if (typeof globalThis.dmuxPluginsFragmentRefresh === "function") {
        globalThis.dmuxPluginsFragmentRefresh();
      }
    });
  } catch (e) {
    toast(String(e.message || e), "err");
    pathEl.textContent = "";
  }
}

function closePluginsFragmentModal() {
  if (
    typeof globalThis.dmuxPluginsFragmentIsDirty === "function" &&
    globalThis.dmuxPluginsFragmentIsDirty()
  ) {
    if (!confirm("Discard unsaved changes to plugins.tmux?")) return;
  }
  el("modal-plugins-fragment")?.close();
}

el("btn-plugins-view-fragment")?.addEventListener("click", () => {
  openPluginsFragmentModal();
});

el("modal-plugins-fragment-save")?.addEventListener("click", () => {
  savePluginsFragmentFromModal();
});

el("modal-plugins-fragment-close")?.addEventListener("click", () => {
  closePluginsFragmentModal();
});

el("modal-plugins-fragment")?.addEventListener("cancel", (ev) => {
  if (
    typeof globalThis.dmuxPluginsFragmentIsDirty === "function" &&
    globalThis.dmuxPluginsFragmentIsDirty()
  ) {
    if (!confirm("Discard unsaved changes to plugins.tmux?")) {
      ev.preventDefault();
    }
  }
});

globalThis.dmuxOnSavePluginsFragment = savePluginsFragmentFromModal;

updateFilterClear();
setupPaneStyleForm();
setupPluginSpecAutocomplete();
refresh();
setupAutoRefresh();
