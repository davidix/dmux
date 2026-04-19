/**
 * dmux web UI — sessions, windows, pane focus, layouts, snapshot save/restore.
 */

const LAYOUTS = [
  { kind: "grid", label: "Grid", icon: "bi-grid-3x3" },
  { kind: "vertical", label: "Vertical", icon: "bi-layout-split" },
  { kind: "horizontal", label: "Horizontal", icon: "bi-layout-three-columns" },
  { kind: "main-horizontal", label: "Main H", icon: "bi-window-desktop" },
  { kind: "main-vertical", label: "Main V", icon: "bi-columns-gap" },
];

let sessionsData = [];
let selectedName = null;
let refreshTimer = null;
let loadGeneration = 0;
/** @type {"sessions" | "plugins"} */
let mainView = "sessions";
/** Plugin catalog state — filled by ensurePluginCatalog(). */
let pluginCatalogSpecs = [];
/** @type {{spec:string, category:string, description:string, source?:string}[]} */
let pluginCatalogEntries = [];
let pluginCatalogLoadFailed = false;
let pluginCatalogLoadPromise = null;
let chordLeader = null;
let chordLeaderAt = 0;

/** Bundled list when /api/v1/plugins/catalog fails (curated subset of the awesome list). */
const PLUGIN_CATALOG_STATIC_FALLBACK = [
  { spec: "Freed-Wu/tmux-status-bar", category: "Status Bar", description: "Flexible status-bar framework. Requires tmux-powerline-compiler." },
  { spec: "catppuccin/tmux", category: "Themes", description: "Soothing pastel theme for tmux." },
  { spec: "dracula/tmux", category: "Themes", description: "Dracula dark theme for tmux." },
  { spec: "rose-pine/tmux", category: "Themes", description: "Pine-tinted minimalist theme." },
  { spec: "nordtheme/tmux", category: "Themes", description: "Nord arctic color theme." },
  { spec: "sainnhe/tmux-fzf", category: "General", description: "Use fzf to manage tmux environment." },
  { spec: "wfxr/tmux-fzf-url", category: "General", description: "Open URLs from terminal output via fzf." },
  { spec: "joshmedeski/sesh", category: "Sessions", description: "Smart session manager for the terminal." },
  { spec: "omerxx/tmux-sessionx", category: "Sessions", description: "Session manager with zoxide + fuzzy preview." },
  { spec: "MunifTanjim/tmux-suspend", category: "Sessions", description: "Suspend local tmux to use nested remote tmux." },
  { spec: "MunifTanjim/tmux-mode-indicator", category: "Status Bar", description: "Show currently active tmux mode." },
  { spec: "tmux-plugins/tpm", category: "General", description: "Tmux plugin manager." },
  { spec: "tmux-plugins/tmux-sensible", category: "General", description: "Basic tmux settings everyone can agree on." },
  { spec: "tmux-plugins/tmux-pain-control", category: "Navigation", description: "Standard pane key-bindings." },
  { spec: "tmux-plugins/tmux-yank", category: "Copy Mode", description: "Copy to system clipboard." },
  { spec: "tmux-plugins/tmux-resurrect", category: "Sessions", description: "Persist tmux environment across restarts." },
  { spec: "tmux-plugins/tmux-continuum", category: "Sessions", description: "Continuous saving + auto-restore on tmux start." },
  { spec: "tmux-plugins/tmux-battery", category: "Status Bar", description: "Battery percentage and icon indicator." },
  { spec: "tmux-plugins/tmux-cpu", category: "Status Bar", description: "CPU percentage and icon indicator." },
  { spec: "tmux-plugins/tmux-prefix-highlight", category: "Status Bar", description: "Highlights when you press the prefix key." },
  { spec: "tmux-plugins/tmux-sidebar", category: "General", description: "Sidebar with directory tree (IDE-like)." },
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
  const variant = type === "err" ? "err" : type === "warn" ? "warn" : "ok";
  t.className = `toast ${variant}`;
  t.setAttribute("role", variant === "ok" ? "status" : "alert");
  const icon = document.createElement("span");
  icon.className = "toast-icon";
  icon.setAttribute("aria-hidden", "true");
  icon.textContent = variant === "err" ? "✕" : variant === "warn" ? "⚠" : "✓";
  const msg = document.createElement("div");
  msg.className = "toast-msg";
  msg.textContent = message;
  t.appendChild(icon);
  t.appendChild(msg);
  wrap.appendChild(t);
  const dwell = variant === "warn" ? 9000 : 4800;
  setTimeout(() => {
    t.style.opacity = "0";
    t.style.transition = "opacity 0.25s ease";
    setTimeout(() => t.remove(), 260);
  }, dwell);
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
    block.classList.toggle("hidden", Boolean(ok));
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

async function switchMainView(nextView) {
  if (nextView !== "sessions" && nextView !== "plugins") return;
  if (mainView === nextView) return;
  mainView = nextView;
  if (nextView === "plugins") {
    const vt = el("view-title");
    const vm = el("view-meta");
    const sm = el("sync-meta");
    if (vt) vt.textContent = "Plugins (TPM)";
    if (vm) vm.textContent = "Bundled tmux-plugins/tpm · ~/.tmux/plugins";
    if (sm) sm.textContent = "";
    applyMainView();
    return;
  }
  applyMainView();
  await refresh({ silent: true });
}

async function fetchPluginsStatus() {
  const res = await fetch(apiUrl("/api/v1/plugins"), {
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

const PLUGINS_TABLE_COMPACT_LS = "dmux-plugins-table-compact";

function applyPluginsTableCompact(on) {
  const wrap = document.querySelector("#plugins-table-panel.plugins-table-wrap");
  const sw = el("plugins-table-compact");
  if (wrap) wrap.classList.toggle("plugins-table-wrap--compact", Boolean(on));
  if (sw) sw.checked = Boolean(on);
  try {
    localStorage.setItem(PLUGINS_TABLE_COMPACT_LS, on ? "1" : "0");
  } catch {
    /* ignore quota / private mode */
  }
}

function readPluginsTableCompactPreference() {
  try {
    return localStorage.getItem(PLUGINS_TABLE_COMPACT_LS) === "1";
  } catch {
    return false;
  }
}

function initPluginsTableCompact() {
  const sw = el("plugins-table-compact");
  if (!sw) return;
  applyPluginsTableCompact(readPluginsTableCompactPreference());
  sw.addEventListener("change", () => applyPluginsTableCompact(sw.checked));
}

/** Load GET /api/v1/plugins/catalog once; fills pluginCatalogEntries + pluginCatalogSpecs. */
function ensurePluginCatalog() {
  if (pluginCatalogLoadPromise) return pluginCatalogLoadPromise;
  const hint = el("plugin-spec-hint");
  const useFallback = () => {
    pluginCatalogEntries = PLUGIN_CATALOG_STATIC_FALLBACK.slice();
    pluginCatalogSpecs = pluginCatalogEntries.map((e) => e.spec);
    pluginCatalogLoadFailed = false;
  };
  pluginCatalogLoadPromise = (async () => {
    const setHintDefault = (count) => {
      if (!hint) return;
      hint.innerHTML =
        `${count} plugins from the <a href="https://github.com/tmux-plugins/list" target="_blank" rel="noopener noreferrer">tmux-plugins/list</a> awesome list, merged with the live <a href="https://github.com/tmux-plugins" target="_blank" rel="noopener noreferrer">tmux-plugins</a> org.`;
    };
    const setHintDegraded = (detail) => {
      if (!hint) return;
      hint.textContent = `Curated plugin list (${detail}). You can still type any user/repo (e.g. catppuccin/tmux).`;
    };
    try {
      const res = await fetch(apiUrl("/api/v1/plugins/catalog"), {
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
      const data = await res.json().catch(() => ({}));
      const apiEntries = Array.isArray(data.entries) ? data.entries : [];
      const apiPlugins = Array.isArray(data.plugins) ? data.plugins : [];
      if (apiEntries.length > 0) {
        pluginCatalogEntries = apiEntries.filter((e) => e && typeof e.spec === "string");
        pluginCatalogSpecs = pluginCatalogEntries.map((e) => e.spec);
      } else if (apiPlugins.length > 0) {
        pluginCatalogSpecs = apiPlugins;
        pluginCatalogEntries = apiPlugins.map((spec) => ({ spec, category: "Other", description: "" }));
      } else {
        useFallback();
      }
      pluginCatalogLoadFailed = pluginCatalogSpecs.length === 0;
      if (!res.ok) {
        useFallback();
        setHintDegraded(`API HTTP ${res.status}`);
        return;
      }
      if (data.error) {
        setHintDegraded(data.error);
      } else {
        setHintDefault(pluginCatalogSpecs.length);
      }
    } catch (e) {
      useFallback();
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

  function filterEntries(query) {
    const q = String(query || "").trim().toLowerCase();
    const src = pluginCatalogEntries.length
      ? pluginCatalogEntries
      : pluginCatalogSpecs.map((spec) => ({ spec, category: "Other", description: "" }));
    if (!src.length) return [];
    if (!q) return src.slice();
    return src.filter((e) => {
      if (e.spec && e.spec.toLowerCase().includes(q)) return true;
      if (e.category && e.category.toLowerCase().includes(q)) return true;
      if (e.description && e.description.toLowerCase().includes(q)) return true;
      return false;
    });
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
    let lastCat = null;
    let optIndex = 0;
    matches.forEach((entry) => {
      const spec = typeof entry === "string" ? entry : entry.spec;
      const cat = (typeof entry === "string" ? "" : entry.category) || "";
      const desc = (typeof entry === "string" ? "" : entry.description) || "";
      if (cat && cat !== lastCat) {
        const head = document.createElement("li");
        head.className = "plugin-spec-suggest-cat";
        head.setAttribute("role", "presentation");
        head.textContent = cat;
        list.appendChild(head);
        lastCat = cat;
      }
      const li = document.createElement("li");
      li.setAttribute("role", "option");
      li.setAttribute("id", `plugin-spec-opt-${optIndex++}`);
      li.className = "plugin-spec-suggest-item";
      li.dataset.spec = spec;
      const specEl = document.createElement("span");
      specEl.className = "plugin-spec-suggest-spec";
      specEl.textContent = spec;
      li.appendChild(specEl);
      if (desc) {
        const descEl = document.createElement("span");
        descEl.className = "plugin-spec-suggest-desc";
        descEl.textContent = desc;
        li.appendChild(descEl);
      }
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
    renderSuggestions(filterEntries(input.value));
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
    renderSuggestions(filterEntries(""));
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
      const target = items[highlight];
      input.value = target.dataset.spec || target.textContent || "";
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

/**
 * Render a persistent banner above the plugins table for known plugin warnings
 * (currently: Freed-Wu/tmux-status-bar missing the AOT compiler, which silently
 * empties status-left/right on every `tmux source-file`).
 */
function renderPluginsWarningBanner(data) {
  const banner = el("plugins-warning-banner");
  if (!banner) return;
  const warning =
    data && data.freed_wu_status_bar && typeof data.freed_wu_status_bar.warning === "string"
      ? data.freed_wu_status_bar.warning
      : null;
  if (!warning) {
    banner.classList.add("hidden");
    banner.textContent = "";
    banner.removeAttribute("data-warn-key");
    return;
  }
  banner.classList.remove("hidden");
  banner.innerHTML = `<span class="plugins-warning-icon" aria-hidden="true">⚠</span><span class="plugins-warning-text"></span>`;
  banner.querySelector(".plugins-warning-text").textContent = warning;
  banner.setAttribute("data-warn-key", "freed-wu-status-bar-compiler");
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
      const sock = data.tmux_socket;
      const sockBit =
        sock != null && String(sock).trim()
          ? ` · reload uses <code>tmux -S ${escapeHtml(String(sock))}</code>`
          : " · reload uses default tmux socket";
      paths.innerHTML = `TPM bundled: <strong>${ok}</strong> · fragment <code>${escapeHtml(fr)}</code> · tmux.conf <code>${escapeHtml(tc)}</code>${sockBit}`;
    }
    renderPluginsWarningBanner(data);
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
      tr.innerHTML = `<td class="plugins-spec-col">${escapeHtml(spec)}</td><td class="plugins-about-cell" data-plugin-about></td><td class="plugins-folder-cell mono">${escapeHtml(dir)}</td><td class="plugins-installed-cell">${escapeHtml(inst)}</td><td class="plugins-actions"><div class="plugins-actions-inner"></div></td>`;
      const aboutTd = tr.querySelector("[data-plugin-about]");
      if (aboutTd) aboutTasks.push(fillPluginAboutCell(aboutTd, spec));
      const actions = tr.querySelector(".plugins-actions-inner");
      const isTpm = spec === "tmux-plugins/tpm";

      if (spec && !isTpm) {
        const applyBtn = document.createElement("button");
        applyBtn.type = "button";
        applyBtn.className = "btn btn-sm btn-outline-secondary plugins-row-btn";
        applyBtn.innerHTML =
          '<i class="bi bi-file-earmark-plus me-1" aria-hidden="true"></i>Apply config';
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
        installBtn.className = "btn btn-sm btn-primary plugins-row-btn";
        installBtn.innerHTML =
          '<i class="bi bi-download me-1" aria-hidden="true"></i>Install';
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
        rm.className = "btn btn-sm btn-outline-danger plugins-row-btn";
        rm.innerHTML = '<i class="bi bi-trash me-1" aria-hidden="true"></i>Remove';
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
      if (!actions.children.length) {
        actions.innerHTML = '<span class="plugins-actions-empty text-secondary">—</span>';
      }
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

/** `tmux source-file` the managed plugins fragment (same socket as this UI). */
async function sourceFragmentInTmux() {
  const buttons = [el("btn-plugins-source"), el("modal-plugins-fragment-source")].filter(Boolean);
  buttons.forEach((b) => {
    b.disabled = true;
  });
  try {
    const data = await postPluginsAction("source");
    const okMsg = "Reloaded in tmux";
    toast(data.output ? `${okMsg}: ${String(data.output).slice(0, 200)}` : okMsg);
    if (data && typeof data.warning === "string" && data.warning) {
      toast(data.warning, "warn");
    }
    await loadPluginsPanel();
  } catch (e) {
    toast(String(e.message || e), "err");
  } finally {
    buttons.forEach((b) => {
      b.disabled = false;
    });
  }
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
/* =========================================================
 * Pane / window mutation helpers (v2 — extra tmux features)
 * ========================================================= */

async function postSendKeys(paneId, { text, enter, literal }) {
  const res = await fetch(apiUrl(`/api/v1/panes/${encodeURIComponent(paneId)}/send-keys`), {
    method: "POST",
    cache: "no-store",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify({ text, enter: !!enter, literal: !!literal }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

async function getPaneCapture(paneId, lines = 200) {
  const res = await fetch(
    apiUrl(`/api/v1/panes/${encodeURIComponent(paneId)}/capture?lines=${encodeURIComponent(lines)}`),
    { cache: "no-store", headers: { Accept: "application/json" } },
  );
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return String(data.text || "");
}

async function postZoomPane(paneId) {
  const res = await fetch(apiUrl(`/api/v1/panes/${encodeURIComponent(paneId)}/zoom`), {
    method: "POST",
    cache: "no-store",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: "{}",
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

async function postBreakPane(paneId) {
  const res = await fetch(apiUrl(`/api/v1/panes/${encodeURIComponent(paneId)}/break`), {
    method: "POST",
    cache: "no-store",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: "{}",
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

async function postSwapPane(paneId, direction) {
  const res = await fetch(apiUrl(`/api/v1/panes/${encodeURIComponent(paneId)}/swap`), {
    method: "POST",
    cache: "no-store",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify({ direction }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

async function postKillOtherPanes(paneId) {
  const res = await fetch(apiUrl(`/api/v1/panes/${encodeURIComponent(paneId)}/kill-others`), {
    method: "POST",
    cache: "no-store",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: "{}",
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

async function postResizePane(paneId, dx, dy) {
  const res = await fetch(apiUrl(`/api/v1/panes/${encodeURIComponent(paneId)}/resize`), {
    method: "POST",
    cache: "no-store",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify({ delta_x: dx, delta_y: dy }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

async function patchRenameWindow(sessionName, windowIndex, newName) {
  const res = await fetch(
    apiUrl(`/api/v1/sessions/${encodeURIComponent(sessionName)}/windows/${windowIndex}/rename`),
    {
      method: "PATCH",
      cache: "no-store",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ name: newName }),
    },
  );
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

async function postMoveWindow(sessionName, windowIndex, direction) {
  const res = await fetch(
    apiUrl(`/api/v1/sessions/${encodeURIComponent(sessionName)}/windows/${windowIndex}/move`),
    {
      method: "POST",
      cache: "no-store",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ direction }),
    },
  );
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

async function postSynchronizeWindow(sessionName, windowIndex, on) {
  const res = await fetch(
    apiUrl(
      `/api/v1/sessions/${encodeURIComponent(sessionName)}/windows/${windowIndex}/synchronize`,
    ),
    {
      method: "POST",
      cache: "no-store",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ on: !!on }),
    },
  );
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

async function fetchServerInfo() {
  try {
    const r = await fetch(apiUrl("/api/v1/server"), {
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
    if (!r.ok) return null;
    return await r.json();
  } catch {
    return null;
  }
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

/**
 * @param {string|null|undefined} label
 * @param {{ include_scrollback?: boolean, include_history?: boolean,
 *           scrollback_lines?: number, history_lines?: number }} [opts]
 */
async function saveSnapshot(label, opts) {
  /** @type {Record<string, unknown>} */
  const body = { label: label || "default" };
  if (opts && typeof opts === "object") {
    if (opts.include_scrollback) body.include_scrollback = true;
    if (opts.include_history) body.include_history = true;
    if (Number.isFinite(opts.scrollback_lines))
      body.scrollback_lines = Math.max(0, Math.min(20000, Math.floor(Number(opts.scrollback_lines))));
    if (Number.isFinite(opts.history_lines))
      body.history_lines = Math.max(0, Math.min(5000, Math.floor(Number(opts.history_lines))));
    if (opts.use_resurrect) body.use_resurrect = true;
  }
  const res = await fetch(apiUrl("/api/v1/snapshots/save"), {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

async function fetchSnapshotsList() {
  const res = await fetch(apiUrl("/api/v1/snapshots"), {
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return Array.isArray(data.snapshots) ? data.snapshots : [];
}

/** @param {{ id?: number, label?: string, kill_existing?: boolean }} body */
async function postSnapshotRestore(body) {
  const res = await fetch(apiUrl("/api/v1/snapshots/restore"), {
    method: "POST",
    cache: "no-store",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

/** @param {number | string} snapshotId */
async function deleteSnapshotById(snapshotId) {
  const res = await fetch(apiUrl(`/api/v1/snapshots/${encodeURIComponent(String(snapshotId))}`), {
    method: "DELETE",
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

async function fetchExistingSessionNames() {
  try {
    const data = await fetchSessions();
    const sessions = Array.isArray(data?.sessions) ? data.sessions : [];
    return new Set(sessions.map((s) => String(s?.name || "")).filter(Boolean));
  } catch {
    return new Set();
  }
}

async function fetchResurrectFiles() {
  const res = await fetch(apiUrl("/api/v1/snapshots/resurrect/files"), {
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return {
    saveDir: typeof data.save_dir === "string" ? data.save_dir : "",
    installed: Boolean(data.installed),
    files: Array.isArray(data.files) ? data.files : [],
  };
}

/** @param {string} path */
async function deleteResurrectFile(path) {
  const res = await fetch(apiUrl("/api/v1/snapshots/resurrect/files"), {
    method: "DELETE",
    cache: "no-store",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

async function refreshSnapshotRestoreListInModal() {
  const [snapshots, resurrect] = await Promise.all([
    fetchSnapshotsList(),
    fetchResurrectFiles().catch(() => ({ saveDir: "", installed: false, files: [] })),
  ]);
  renderSnapshotRestoreList(snapshots);
  renderResurrectFilesList(resurrect);
  updateSnapshotRestoreCount(snapshots.length, resurrect.files.length);
  if (snapshots.length === 0 && resurrect.files.length > 0) {
    el("snapshot-restore-empty")?.classList.add("hidden");
  }
}

/** @param {number} ts */
function formatSnapshotTime(ts) {
  if (typeof ts !== "number" || Number.isNaN(ts)) return "—";
  try {
    return new Date(ts * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  } catch {
    return String(ts);
  }
}

/** @param {Record<string, unknown>} s */
function snapshotSummaryLine(s) {
  const summary = s.summary && typeof s.summary === "object" ? s.summary : {};
  if (summary.parse_error) return "Could not read snapshot payload.";
  const sc = Number(summary.session_count) || 0;
  const wc = Number(summary.window_count) || 0;
  const pc = Number(summary.pane_count) || 0;
  const names = Array.isArray(summary.session_names) ? summary.session_names : [];
  const head = names.slice(0, 5).filter((x) => x && String(x).trim());
  const more = names.length > 5 ? "…" : "";
  const tail = head.length ? ` — ${head.join(", ")}${more}` : "";
  return `${sc} session${sc === 1 ? "" : "s"} · ${wc} window${wc === 1 ? "" : "s"} · ${pc} pane${pc === 1 ? "" : "s"}${tail}`;
}

/**
 * Append "rich state" badges (commands / scrollback / history) to a row title.
 * Reads `summary.has_commands`, `summary.has_scrollback`, `summary.has_history`,
 * `summary.history_lines`, `summary.scrollback_chars` from the API payload.
 * @param {HTMLElement} title
 * @param {Record<string, unknown>} s
 */
function appendSnapshotBadges(title, s) {
  const summary = (s && typeof s === "object" && s.summary && typeof s.summary === "object")
    ? /** @type {Record<string, unknown>} */ (s.summary)
    : {};
  const version = Number(summary.version) || 1;
  const mk = (text, cls, tooltip) => {
    const b = document.createElement("span");
    b.className = `badge rounded-pill ${cls}`;
    b.textContent = text;
    if (tooltip) b.title = tooltip;
    return b;
  };
  if (version >= 2) {
    title.appendChild(mk("v2", "text-bg-light border", "Snapshot schema v2 (rich pane state supported)"));
  }
  if (summary.has_commands) {
    title.appendChild(mk("commands", "text-bg-info-subtle text-info-emphasis border", "Pane foreground commands captured"));
  }
  if (summary.has_scrollback) {
    const kb = Math.max(1, Math.round(Number(summary.scrollback_chars || 0) / 1024));
    title.appendChild(mk(`scrollback · ${kb}KB`, "text-bg-warning-subtle text-warning-emphasis border", "Pane scrollback captured (replayable on restore)"));
  }
  if (summary.has_history) {
    const n = Number(summary.history_lines || 0);
    title.appendChild(mk(`history · ${n}`, "text-bg-success-subtle text-success-emphasis border", "Extracted command history captured"));
  }
  if (summary.has_resurrect) {
    const file = summary.resurrect_file ? String(summary.resurrect_file) : "tmux-resurrect file";
    title.appendChild(
      mk(
        "resurrect",
        "text-bg-primary-subtle text-primary-emphasis border",
        `tmux-resurrect snapshot recorded — ${file}`,
      ),
    );
  }
}

/** @param {number} sqliteCount @param {number} diskCount */
function updateSnapshotRestoreCount(sqliteCount, diskCount) {
  const countEl = el("snapshot-restore-count");
  if (!countEl) return;
  if (sqliteCount === 0 && diskCount === 0) {
    countEl.textContent = "No snapshots found";
    return;
  }
  const parts = [];
  parts.push(`${sqliteCount} dmux snapshot${sqliteCount === 1 ? "" : "s"}`);
  if (diskCount > 0) {
    parts.push(`${diskCount} tmux-resurrect file${diskCount === 1 ? "" : "s"}`);
  }
  countEl.textContent = parts.join(" · ");
}

/** @param {number} ts */
function formatResurrectFileTime(ts) {
  if (typeof ts !== "number" || Number.isNaN(ts)) return "—";
  try {
    return new Date(ts * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  } catch {
    return String(ts);
  }
}

/** @param {number} bytes */
function formatBytes(bytes) {
  const n = Number(bytes);
  if (!Number.isFinite(n) || n <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v < 10 && i > 0 ? v.toFixed(1) : Math.round(v)} ${units[i]}`;
}

/** @param {Record<string, unknown>} summary */
function resurrectSummaryLine(summary) {
  if (!summary || typeof summary !== "object") return "";
  if (summary.parse_error) return `Could not read snapshot: ${String(summary.parse_error)}`;
  const sc = Number(summary.session_count) || 0;
  const wc = Number(summary.window_count) || 0;
  const pc = Number(summary.pane_count) || 0;
  const names = Array.isArray(summary.session_names) ? summary.session_names : [];
  const head = names.slice(0, 5).filter((x) => x && String(x).trim());
  const more = names.length > 5 ? "…" : "";
  const tail = head.length ? ` — ${head.join(", ")}${more}` : "";
  const cur = summary.current_session ? ` · current ${String(summary.current_session)}` : "";
  return `${sc} session${sc === 1 ? "" : "s"} · ${wc} window${wc === 1 ? "" : "s"} · ${pc} pane${pc === 1 ? "" : "s"}${tail}${cur}`;
}

/** @param {{saveDir: string, installed: boolean, files: Array<Record<string, unknown>>}} payload */
function renderResurrectFilesList(payload) {
  const section = el("snapshot-restore-resurrect-section");
  const list = el("snapshot-restore-resurrect-list");
  const countEl = el("snapshot-restore-resurrect-count");
  const hint = el("snapshot-restore-resurrect-hint");
  if (!section || !list || !countEl) return;
  list.innerHTML = "";
  const files = Array.isArray(payload?.files) ? payload.files : [];
  const n = files.length;
  countEl.textContent = payload?.saveDir ? payload.saveDir : "";
  if (n === 0) {
    section.classList.add("hidden");
    return;
  }
  section.classList.remove("hidden");
  const installed = Boolean(payload?.installed);
  if (hint) {
    hint.textContent = installed
      ? "Files written by the tmux-resurrect plugin itself (or tmux-continuum auto-saves). Restoring one runs the plugin's scripts/restore.sh."
      : "Found tmux-resurrect snapshot files on disk, but the plugin isn't installed via TPM in this dmux. Install it (Plugins → Install) before restoring.";
  }
  for (const raw of files) {
    const f = raw && typeof raw === "object" ? raw : {};
    const path = String(f.path || "");
    const name = String(f.name || path || "");
    const summary = (f.summary && typeof f.summary === "object")
      ? /** @type {Record<string, unknown>} */ (f.summary)
      : {};
    const row = document.createElement("div");
    row.className =
      "snapshot-restore-item border-bottom d-flex flex-column flex-md-row gap-2 gap-md-3 align-items-start align-items-md-center justify-content-between p-2 p-md-3";
    row.setAttribute("role", "listitem");
    const left = document.createElement("div");
    left.className = "min-w-0 flex-grow-1";
    const title = document.createElement("div");
    title.className = "small fw-semibold d-flex flex-wrap align-items-center gap-2";
    const icon = document.createElement("span");
    icon.className = "snapshot-restore-meta text-secondary";
    icon.textContent = "resurrect";
    title.appendChild(icon);
    const nameEl = document.createElement("span");
    nameEl.className = "text-truncate";
    nameEl.textContent = name;
    title.appendChild(nameEl);
    if (f.is_last) {
      const last = document.createElement("span");
      last.className = "badge rounded-pill text-bg-primary-subtle text-primary-emphasis border";
      last.textContent = "last";
      last.title = "Plugin's `last` symlink points here — what scripts/restore.sh would replay by default";
      title.appendChild(last);
    }
    const cmds = Array.isArray(summary.commands) ? summary.commands : [];
    if (cmds.length > 0) {
      const cmdsBadge = document.createElement("span");
      cmdsBadge.className = "badge rounded-pill text-bg-info-subtle text-info-emphasis border";
      cmdsBadge.textContent = `commands · ${cmds.length}`;
      cmdsBadge.title = `Foreground commands captured: ${cmds.slice(0, 8).join(", ")}${cmds.length > 8 ? "…" : ""}`;
      title.appendChild(cmdsBadge);
    }
    // History indicator: tmux-resurrect only restores pane scrollback when a
    // matching pane_contents archive exists. dmux stashes one per snapshot,
    // but legacy/external saves only have the live archive (which matches
    // whichever save was most recent at capture time).
    const hasHist = Boolean(f.has_contents_archive);
    const histBadge = document.createElement("span");
    if (hasHist) {
      histBadge.className = "badge rounded-pill text-bg-success-subtle text-success-emphasis border";
      histBadge.textContent = "history";
      const histSize = Number(f.contents_archive_size) || 0;
      histBadge.title = `Includes per-snapshot pane scrollback (${formatBytes(histSize)}). Restore will replay history, processes and visible output.`;
    } else {
      histBadge.className = "badge rounded-pill text-bg-warning-subtle text-warning-emphasis border";
      histBadge.textContent = "no history";
      histBadge.title =
        "No matching pane_contents archive on disk for this snapshot. " +
        "Restore will recreate windows/panes and start configured processes, " +
        "but pane scrollback / visible output won't be replayed. " +
        "(tmux-resurrect keeps a single shared archive that's overwritten on every save.)";
    }
    title.appendChild(histBadge);
    const when = document.createElement("div");
    when.className = "snapshot-restore-meta text-secondary mt-1";
    when.textContent = `${formatResurrectFileTime(Number(f.mtime))} · ${formatBytes(Number(f.size))}`;
    const detail = document.createElement("p");
    detail.className = "small text-secondary mb-0 mt-1";
    detail.textContent = resurrectSummaryLine(summary);
    const pathEl = document.createElement("p");
    pathEl.className = "snapshot-restore-meta text-secondary mb-0 mt-1 text-break";
    pathEl.textContent = path;
    left.appendChild(title);
    left.appendChild(when);
    left.appendChild(detail);
    if (cmds.length > 0) {
      const cmdLine = document.createElement("p");
      cmdLine.className = "small text-secondary mb-0 mt-1";
      const head = cmds.slice(0, 6);
      const more = cmds.length > head.length ? ` +${cmds.length - head.length} more` : "";
      cmdLine.innerHTML = `<span class="text-secondary">apps:</span> <span class="font-monospace">${head.map(escapeHtml).join(", ")}</span>${more}`;
      left.appendChild(cmdLine);
    }
    left.appendChild(pathEl);
    const actions = document.createElement("div");
    actions.className = "snapshot-restore-actions flex-shrink-0";
    const restoreBtn = document.createElement("button");
    restoreBtn.type = "button";
    restoreBtn.className = "btn btn-sm btn-primary snapshot-restore-restore-btn";
    restoreBtn.textContent = "Restore";
    restoreBtn.disabled = !installed;
    if (!installed) restoreBtn.title = "tmux-resurrect plugin is not installed";
    restoreBtn.addEventListener("click", async () => {
      const kill = Boolean(el("snapshot-restore-kill")?.checked);
      const sessionNames = Array.isArray(summary.session_names) ? summary.session_names : [];
      // Warn early when the snapshot has sessions that already exist and
      // the user didn't tick "Kill conflicting sessions" — otherwise
      // tmux-resurrect's restore.sh would silently skip them and the
      // click would look like a no-op.
      if (!kill && sessionNames.length > 0) {
        const existing = await fetchExistingSessionNames();
        const conflicts = sessionNames.filter((s) => existing.has(String(s)));
        if (conflicts.length > 0) {
          const proceed = window.confirm(
            `tmux-resurrect won't overwrite sessions that already exist. ` +
            `These would be skipped: ${conflicts.join(", ")}.\n\n` +
            `OK to continue anyway, or Cancel to tick "Kill conflicting sessions" first.`,
          );
          if (!proceed) return;
        }
      }
      const allBtns = document.querySelectorAll(
        "#modal-snapshots-restore .snapshot-restore-restore-btn, #modal-snapshots-restore .snapshot-restore-delete-btn",
      );
      allBtns.forEach((b) => {
        b.disabled = true;
      });
      try {
        const r = await postSnapshotRestore({
          resurrect_file: path,
          kill_existing: kill,
        });
        const killed = Array.isArray(r?.killed_sessions) ? r.killed_sessions : [];
        const skipped = Array.isArray(r?.skipped_sessions) ? r.skipped_sessions : [];
        let msg = `Restored from ${name}`;
        if (killed.length) msg += ` (killed first: ${killed.join(", ")})`;
        if (skipped.length) msg += ` — skipped existing: ${skipped.join(", ")}`;
        if (!hasHist) msg += " · pane history not replayed (no archive)";
        toast(msg, skipped.length || !hasHist ? "warn" : undefined);
        el("modal-snapshots-restore")?.close();
        await refresh({ silent: true });
      } catch (e) {
        toast(String(e.message || e), "err");
      } finally {
        allBtns.forEach((b) => {
          b.disabled = false;
        });
        if (!installed) restoreBtn.disabled = true;
      }
    });
    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "btn btn-sm btn-outline-danger snapshot-restore-delete-btn";
    delBtn.title = `Delete ${name} from disk`;
    delBtn.setAttribute("aria-label", `Delete ${name}`);
    delBtn.innerHTML = '<i class="bi bi-trash" aria-hidden="true"></i>';
    delBtn.addEventListener("click", async () => {
      if (!window.confirm(`Delete ${name} from ${payload.saveDir}? This cannot be undone.`)) return;
      delBtn.disabled = true;
      restoreBtn.disabled = true;
      try {
        await deleteResurrectFile(path);
        toast(`Deleted ${name}`);
        await refreshSnapshotRestoreListInModal();
      } catch (e) {
        toast(String(e.message || e), "err");
        delBtn.disabled = false;
        if (installed) restoreBtn.disabled = false;
      }
    });
    actions.appendChild(restoreBtn);
    actions.appendChild(delBtn);
    row.appendChild(left);
    row.appendChild(actions);
    list.appendChild(row);
  }
}

/** @param {Record<string, unknown>[]} snapshots */
function renderSnapshotRestoreList(snapshots) {
  const list = el("snapshot-restore-list");
  const empty = el("snapshot-restore-empty");
  if (!list || !empty) return;
  list.innerHTML = "";
  const n = snapshots.length;
  empty.classList.toggle("hidden", n > 0);
  list.classList.toggle("hidden", n === 0);
  for (const raw of snapshots) {
    const s = raw && typeof raw === "object" ? raw : {};
    const id = s.id;
    const row = document.createElement("div");
    row.className =
      "snapshot-restore-item border-bottom d-flex flex-column flex-md-row gap-2 gap-md-3 align-items-start align-items-md-center justify-content-between p-2 p-md-3";
    row.setAttribute("role", "listitem");
    const left = document.createElement("div");
    left.className = "min-w-0 flex-grow-1";
    const title = document.createElement("div");
    title.className = "small fw-semibold d-flex flex-wrap align-items-center gap-2";
    const idSpan = document.createElement("span");
    idSpan.className = "snapshot-restore-meta text-secondary";
    idSpan.textContent = `#${id}`;
    title.appendChild(idSpan);
    const auto = Boolean(s.is_auto);
    if (auto) {
      const badge = document.createElement("span");
      badge.className = "badge rounded-pill text-bg-secondary";
      badge.textContent = "autosave";
      title.appendChild(badge);
    }
    const label = document.createElement("span");
    label.className = "text-truncate";
    label.textContent = String(s.label ?? "");
    title.appendChild(label);
    appendSnapshotBadges(title, s);
    const when = document.createElement("div");
    when.className = "snapshot-restore-meta text-secondary mt-1";
    when.textContent = formatSnapshotTime(Number(s.created_unix));
    const detail = document.createElement("p");
    detail.className = "small text-secondary mb-0 mt-1";
    detail.textContent = snapshotSummaryLine(s);
    left.appendChild(title);
    left.appendChild(when);
    left.appendChild(detail);
    const actions = document.createElement("div");
    actions.className = "snapshot-restore-actions flex-shrink-0";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-sm btn-primary snapshot-restore-restore-btn";
    btn.textContent = "Restore";
    btn.addEventListener("click", async () => {
      const kill = Boolean(el("snapshot-restore-kill")?.checked);
      const replay = Boolean(el("snapshot-restore-replay")?.checked);
      const relaunch = Boolean(el("snapshot-restore-relaunch")?.checked);
      const useResurrect = Boolean(el("snapshot-restore-use-resurrect")?.checked);
      const restoreBtns = list.querySelectorAll(".snapshot-restore-restore-btn");
      const delBtns = list.querySelectorAll(".snapshot-restore-delete-btn");
      restoreBtns.forEach((b) => {
        b.disabled = true;
      });
      delBtns.forEach((b) => {
        b.disabled = true;
      });
      try {
        await postSnapshotRestore({
          id,
          kill_existing: kill,
          replay_scrollback: replay,
          relaunch_commands: relaunch,
          use_resurrect: useResurrect,
        });
        toast("Snapshot restored");
        el("modal-snapshots-restore")?.close();
        await refresh({ silent: true });
      } catch (e) {
        toast(String(e.message || e), "err");
      } finally {
        restoreBtns.forEach((b) => {
          b.disabled = false;
        });
        delBtns.forEach((b) => {
          b.disabled = false;
        });
      }
    });
    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "btn btn-sm btn-outline-danger snapshot-restore-delete-btn";
    delBtn.title = "Delete this snapshot from SQLite";
    delBtn.setAttribute("aria-label", `Delete snapshot ${id}`);
    delBtn.innerHTML = '<i class="bi bi-trash" aria-hidden="true"></i>';
    delBtn.addEventListener("click", async () => {
      if (!window.confirm(`Delete snapshot #${id} from the database? This cannot be undone.`)) return;
      delBtn.disabled = true;
      btn.disabled = true;
      try {
        await deleteSnapshotById(id);
        toast(`Snapshot #${id} deleted`);
        await refreshSnapshotRestoreListInModal();
      } catch (e) {
        toast(String(e.message || e), "err");
      } finally {
        delBtn.disabled = false;
        btn.disabled = false;
      }
    });
    actions.appendChild(btn);
    actions.appendChild(delBtn);
    row.appendChild(left);
    row.appendChild(actions);
    list.appendChild(row);
  }
}

const SNAPSHOT_RESTORE_EMPTY_DEFAULT =
  "No snapshots yet. Use Save snapshot or run dmux save in a terminal.";

async function openSnapshotsRestoreModal() {
  const modal = el("modal-snapshots-restore");
  if (!modal) return;
  const emptyMsg = el("snapshot-restore-empty");
  if (emptyMsg) emptyMsg.textContent = SNAPSHOT_RESTORE_EMPTY_DEFAULT;
  const list = el("snapshot-restore-list");
  if (list) list.innerHTML = "";
  const resurrectSection = el("snapshot-restore-resurrect-section");
  resurrectSection?.classList.add("hidden");
  const countEl = el("snapshot-restore-count");
  if (countEl) countEl.textContent = "Loading…";
  modal.showModal();
  syncResurrectControl("snapshot-restore-use-resurrect", null, {
    defaultCheckedWhenInstalled: false,
  });
  try {
    const [snapshots, resurrect] = await Promise.all([
      fetchSnapshotsList(),
      fetchResurrectFiles().catch(() => ({ saveDir: "", installed: false, files: [] })),
    ]);
    renderSnapshotRestoreList(snapshots);
    renderResurrectFilesList(resurrect);
    updateSnapshotRestoreCount(snapshots.length, resurrect.files.length);
    if (snapshots.length === 0 && resurrect.files.length > 0) {
      emptyMsg?.classList.add("hidden");
    }
  } catch (e) {
    if (countEl) countEl.textContent = "Could not load snapshots";
    emptyMsg?.classList.remove("hidden");
    if (emptyMsg) emptyMsg.textContent = `${String(e.message || e)}`;
    el("snapshot-restore-list")?.classList.add("hidden");
    toast(String(e.message || e), "err");
  }
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

/** True if some pane exactly fills the tmux half-open interval [a, b) on this axis. */
function tmuxAxisSegmentIsRealPane(panes, axis, a, b) {
  if (b <= a) return false;
  if (axis === "x") {
    for (const p of panes) {
      const pw = Math.max(Number(p.width) || 0, 1);
      const left = Number.isFinite(Number(p.left)) ? Number(p.left) : 0;
      if (left === a && left + pw === b) return true;
    }
  } else {
    for (const p of panes) {
      const ph = Math.max(Number(p.height) || 0, 1);
      const top = Number.isFinite(Number(p.top)) ? Number(p.top) : 0;
      if (top === a && top + ph === b) return true;
    }
  }
  return false;
}

/**
 * Drop internal edges that only separate <minSpan cell-wide strips (tmux often
 * reports 1-cell gutters between panes). Does not remove a strip that is itself a pane.
 */
function collapseTmuxAxisEdges(sortedEdges, panes, axis, minSpan = 2) {
  if (sortedEdges.length <= 2) return sortedEdges.slice();
  const edges = sortedEdges.slice();
  for (let guard = 0; guard < 4096; guard++) {
    let merged = false;
    for (let i = 0; i < edges.length - 2; i++) {
      const seg = edges[i + 1] - edges[i];
      if (seg < minSpan && !tmuxAxisSegmentIsRealPane(panes, axis, edges[i], edges[i + 1])) {
        edges.splice(i + 1, 1);
        merged = true;
        break;
      }
    }
    if (!merged) break;
  }
  return edges;
}

/** 1-based CSS grid line at the start of the track that contains tmux cell c. */
function tmuxCellToGridStartLine(edges, c) {
  let j = 0;
  while (j + 1 < edges.length && edges[j + 1] <= c) {
    j++;
  }
  return j + 1;
}

/** 1-based CSS grid line at the end edge of the half-open tmux cell range [ , R). */
function tmuxCellToGridEndLine(edges, R) {
  let j = 0;
  while (j < edges.length && edges[j] < R) {
    j++;
  }
  return j + 1;
}

/**
 * Build a small CSS grid that matches tmux layout without one track per cell
 * (tmux uses large cell counts, e.g. width 120 — repeat(120,1fr) breaks the UI).
 * Tracks use fr weights proportional to tmux cell spans between unique edges.
 * @returns {{ columnTemplate: string, rowTemplate: string, aspectW: number, aspectH: number, getPlacement: (p: object) => { colStart: number, colEndLine: number, rowStart: number, rowEndLine: number } }}
 */
function buildTmuxGridLayout(panes) {
  if (!panes.length) {
    return {
      columnTemplate: "minmax(0, 1fr)",
      rowTemplate: "minmax(0, 1fr)",
      aspectW: 1,
      aspectH: 1,
      getPlacement: () => ({ colStart: 1, colEndLine: 2, rowStart: 1, rowEndLine: 2 }),
    };
  }
  const xs = new Set();
  const ys = new Set();
  for (const p of panes) {
    const pw = Math.max(Number(p.width) || 0, 1);
    const ph = Math.max(Number(p.height) || 0, 1);
    const left = Number.isFinite(Number(p.left)) ? Number(p.left) : 0;
    const top = Number.isFinite(Number(p.top)) ? Number(p.top) : 0;
    xs.add(left);
    xs.add(left + pw);
    ys.add(top);
    ys.add(top + ph);
  }
  const xEdges = collapseTmuxAxisEdges([...xs].sort((a, b) => a - b), panes, "x");
  const yEdges = collapseTmuxAxisEdges([...ys].sort((a, b) => a - b), panes, "y");
  const colSizes = [];
  for (let i = 0; i < xEdges.length - 1; i++) {
    colSizes.push(Math.max(xEdges[i + 1] - xEdges[i], 1));
  }
  const rowSizes = [];
  for (let i = 0; i < yEdges.length - 1; i++) {
    rowSizes.push(Math.max(yEdges[i + 1] - yEdges[i], 1));
  }
  const aspectW = Math.max(xEdges[xEdges.length - 1] - xEdges[0], 1);
  const aspectH = Math.max(yEdges[yEdges.length - 1] - yEdges[0], 1);
  /* Tracks scale with tmux cell counts but stay readable:
   *  - Columns use Nfr so they stretch to fill the container's width proportionally
   *    (no horizontal whitespace; widths reflect tmux cell ratios).
   *  - Rows are clamped in rem so a single 80×22 pane doesn't blow the mosaic
   *    up to >1000px tall (no vertical container to bound an Nfr row). */
  const COL_MIN_REM = 6;
  const ROW_MIN_REM = 7;
  const ROW_MAX_REM = 14;
  const colTrack = (w) => `minmax(${COL_MIN_REM}rem, ${w}fr)`;
  const rowTrack = (h) => {
    const max = Math.min(ROW_MAX_REM, Math.max(ROW_MIN_REM, h * 0.35));
    return `minmax(${ROW_MIN_REM}rem, ${max.toFixed(2)}rem)`;
  };
  const columnTemplate = colSizes.map(colTrack).join(" ");
  const rowTemplate = rowSizes.map(rowTrack).join(" ");
  function getPlacement(p) {
    const pw = Math.max(Number(p.width) || 0, 1);
    const ph = Math.max(Number(p.height) || 0, 1);
    const left = Number.isFinite(Number(p.left)) ? Number(p.left) : 0;
    const top = Number.isFinite(Number(p.top)) ? Number(p.top) : 0;
    const colStart = tmuxCellToGridStartLine(xEdges, left);
    const colEndLine = tmuxCellToGridEndLine(xEdges, left + pw);
    const rowStart = tmuxCellToGridStartLine(yEdges, top);
    const rowEndLine = tmuxCellToGridEndLine(yEdges, top + ph);
    return {
      colStart,
      colEndLine: Math.max(colEndLine, colStart + 1),
      rowStart,
      rowEndLine: Math.max(rowEndLine, rowStart + 1),
    };
  }
  return { columnTemplate, rowTemplate, aspectW, aspectH, getPlacement };
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

/* =========================================================
 * Popover menu primitive (one open at a time, click-away closes)
 * ========================================================= */

let _activePopover = null;

function closeActivePopover() {
  if (!_activePopover) return;
  const { el: popEl, anchor, onDocClick, onKey, onResize } = _activePopover;
  _activePopover = null;
  if (popEl && popEl.parentNode) popEl.parentNode.removeChild(popEl);
  document.removeEventListener("mousedown", onDocClick, true);
  document.removeEventListener("keydown", onKey, true);
  window.removeEventListener("resize", onResize, true);
  window.removeEventListener("scroll", onResize, true);
  if (anchor) {
    anchor.setAttribute("aria-expanded", "false");
  }
}

/**
 * @typedef {{label:string, icon?:string, danger?:boolean, divider?:boolean, section?:string, onClick?: () => void|Promise<void>}} PopoverItem
 */

/** Open a popover anchored to `anchorEl`. `items` is a flat list (use `divider:true` / `section:'…'` for groupings). */
function openPopover(anchorEl, items) {
  closeActivePopover();
  if (!anchorEl) return;
  const pop = document.createElement("div");
  pop.className = "dmux-popover";
  pop.setAttribute("role", "menu");
  for (const item of items) {
    if (item.divider) {
      const d = document.createElement("div");
      d.className = "dmux-popover-divider";
      pop.appendChild(d);
      continue;
    }
    if (item.section) {
      const s = document.createElement("div");
      s.className = "dmux-popover-section";
      s.textContent = item.section;
      pop.appendChild(s);
      continue;
    }
    const b = document.createElement("button");
    b.type = "button";
    b.className = "dmux-popover-item" + (item.danger ? " is-danger" : "");
    b.setAttribute("role", "menuitem");
    if (item.icon) {
      const ic = document.createElement("span");
      ic.className = "dmux-popover-icon";
      ic.setAttribute("aria-hidden", "true");
      ic.innerHTML = `<i class="bi ${item.icon}"></i>`;
      b.appendChild(ic);
    }
    const lab = document.createElement("span");
    lab.className = "dmux-popover-label";
    lab.textContent = item.label;
    b.appendChild(lab);
    b.addEventListener("click", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      const handler = item.onClick;
      closeActivePopover();
      if (typeof handler === "function") {
        try {
          const r = handler();
          if (r && typeof r.then === "function") {
            r.catch((e) => toast(String(e.message || e), "err"));
          }
        } catch (e) {
          toast(String(e.message || e), "err");
        }
      }
    });
    pop.appendChild(b);
  }
  document.body.appendChild(pop);
  const positionPopover = () => {
    const rect = anchorEl.getBoundingClientRect();
    const popRect = pop.getBoundingClientRect();
    const margin = 6;
    let top = rect.bottom + margin + window.scrollY;
    let left = rect.right - popRect.width + window.scrollX;
    if (left < 8) left = rect.left + window.scrollX;
    if (left + popRect.width > window.scrollX + window.innerWidth - 8) {
      left = window.scrollX + window.innerWidth - popRect.width - 8;
    }
    if (top + popRect.height > window.scrollY + window.innerHeight - 8) {
      top = rect.top + window.scrollY - popRect.height - margin;
    }
    pop.style.top = `${Math.max(8, top)}px`;
    pop.style.left = `${Math.max(8, left)}px`;
  };
  positionPopover();
  const onDocClick = (ev) => {
    if (pop.contains(ev.target) || anchorEl.contains(ev.target)) return;
    closeActivePopover();
  };
  const onKey = (ev) => {
    if (ev.key === "Escape") {
      ev.preventDefault();
      closeActivePopover();
      anchorEl.focus({ preventScroll: true });
    }
  };
  const onResize = () => positionPopover();
  document.addEventListener("mousedown", onDocClick, true);
  document.addEventListener("keydown", onKey, true);
  window.addEventListener("resize", onResize, true);
  window.addEventListener("scroll", onResize, true);
  anchorEl.setAttribute("aria-expanded", "true");
  _activePopover = { el: pop, anchor: anchorEl, onDocClick, onKey, onResize };
  const first = pop.querySelector(".dmux-popover-item");
  if (first) first.focus({ preventScroll: true });
}

/* =========================================================
 * Pane tile rendering
 * ========================================================= */

/** Build the action menu items for a pane (used by the kebab popover). */
function buildPaneMenuItems(pane, window, session) {
  const isOnly = (window.panes || []).length <= 1;
  return [
    { section: "Pane" },
    {
      label: pane.active ? "Re-focus pane" : "Focus pane",
      icon: "bi-bullseye",
      onClick: async () => {
        await focusPane(pane.pane_id);
        toast("Pane focused");
        await refresh({ silent: true });
      },
    },
    {
      label: window.zoomed ? "Unzoom window" : "Zoom pane",
      icon: window.zoomed ? "bi-fullscreen-exit" : "bi-arrows-fullscreen",
      onClick: async () => {
        await postZoomPane(pane.pane_id);
        toast(window.zoomed ? "Window unzoomed" : "Pane zoomed");
        await refresh({ silent: true });
      },
    },
    {
      label: "Send keys…",
      icon: "bi-keyboard",
      onClick: () => openSendKeysModal(pane.pane_id),
    },
    {
      label: "Capture output…",
      icon: "bi-eye",
      onClick: () => openCaptureModal(pane.pane_id),
    },
    {
      label: "Resize…",
      icon: "bi-arrows-angle-expand",
      onClick: () => openResizeModal(pane.pane_id),
    },
    { divider: true },
    { section: "Layout" },
    {
      label: "Swap ↑ (previous)",
      icon: "bi-arrow-up",
      onClick: async () => {
        await postSwapPane(pane.pane_id, "up");
        toast("Pane swapped up");
        await refresh({ silent: true });
      },
    },
    {
      label: "Swap ↓ (next)",
      icon: "bi-arrow-down",
      onClick: async () => {
        await postSwapPane(pane.pane_id, "down");
        toast("Pane swapped down");
        await refresh({ silent: true });
      },
    },
    {
      label: "Break out into new window",
      icon: "bi-box-arrow-up-right",
      onClick: async () => {
        if (isOnly) {
          toast("Only one pane in this window — nothing to break out.", "warn");
          return;
        }
        await postBreakPane(pane.pane_id);
        toast("Pane broken out into a new window");
        await refresh({ silent: true });
      },
    },
    { divider: true },
    {
      label: "Appearance (web tile)…",
      icon: "bi-palette",
      onClick: () => openPaneStyleModal(pane.pane_id),
    },
    { divider: true },
    {
      label: "Close pane",
      icon: "bi-x-circle",
      danger: true,
      onClick: async () => {
        if (
          !confirm(
            `Close pane ${pane.pane_id}? tmux cannot remove the last pane in a window — close the window instead.`,
          )
        ) {
          return;
        }
        await deletePaneApi(pane.pane_id);
        clearPaneTileStyle(pane.pane_id);
        toast("Pane closed");
        await refresh({ silent: true });
      },
    },
    {
      label: "Close all OTHER panes here",
      icon: "bi-x-octagon",
      danger: true,
      onClick: async () => {
        if (isOnly) {
          toast("No other panes to close.", "warn");
          return;
        }
        if (
          !confirm(
            `Close every other pane in window “${window.name || "window"}” and keep ${pane.pane_id}?`,
          )
        ) {
          return;
        }
        await postKillOtherPanes(pane.pane_id);
        toast("Other panes closed");
        await refresh({ silent: true });
      },
    },
  ];
}

/**
 * @param {HTMLElement} mosaic
 * @param {object} p
 * @param {"grid" | "list"} mode
 * @param {{ totalW: number, window: object, session: object, tmuxLayout?: ReturnType<typeof buildTmuxGridLayout> }} listCtx
 */
function appendPaneToMosaic(mosaic, p, mode, listCtx) {
  const tile = document.createElement("button");
  tile.type = "button";
  tile.className = "pane-tile" + (p.active ? " active" : "");
  tile.setAttribute("aria-pressed", p.active ? "true" : "false");
  if (p.active) {
    tile.setAttribute("aria-current", "true");
  }
  tile.title = `Focus pane ${p.pane_id} (${p.command || "shell"})`;
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

  const metaRow = document.createElement("div");
  metaRow.className = "pane-tile-meta-row";
  if (p.command) {
    const cmd = document.createElement("span");
    cmd.className = "pane-tile-cmd";
    cmd.textContent = p.command;
    cmd.title = `Foreground process: ${p.command}${p.pid ? " (pid " + p.pid + ")" : ""}`;
    metaRow.appendChild(cmd);
  }
  if (listCtx.window && listCtx.window.zoomed && p.active) {
    const zoom = document.createElement("span");
    zoom.className = "pane-tile-zoom";
    zoom.textContent = "ZOOM";
    zoom.title = "Window is zoomed (other panes are hidden in tmux until unzoomed)";
    metaRow.appendChild(zoom);
  }
  const dim = document.createElement("span");
  dim.className = "pane-tile-dim";
  dim.textContent = `${p.width || "?"}×${p.height || "?"}`;
  dim.style.marginLeft = "auto";
  metaRow.appendChild(dim);

  tile.appendChild(ph);
  tile.appendChild(path);
  tile.appendChild(metaRow);

  tile.addEventListener("click", async () => {
    try {
      await focusPane(p.pane_id);
      toast("Pane focused");
      await refresh({ silent: true });
    } catch (e) {
      toast(String(e.message || e), "err");
    }
  });

  const zoomBtn = document.createElement("button");
  zoomBtn.type = "button";
  zoomBtn.className = "pane-tile-zoom-btn" + (listCtx.window && listCtx.window.zoomed ? " is-on" : "");
  zoomBtn.setAttribute(
    "aria-label",
    listCtx.window && listCtx.window.zoomed
      ? `Unzoom window for pane ${p.pane_id}`
      : `Zoom pane ${p.pane_id}`,
  );
  zoomBtn.title =
    listCtx.window && listCtx.window.zoomed ? "Unzoom (resize-pane -Z)" : "Zoom pane (resize-pane -Z)";
  zoomBtn.innerHTML = listCtx.window && listCtx.window.zoomed
    ? '<i class="bi bi-fullscreen-exit"></i>'
    : '<i class="bi bi-arrows-fullscreen"></i>';
  zoomBtn.addEventListener(
    "click",
    (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      (async () => {
        try {
          await postZoomPane(p.pane_id);
          toast(listCtx.window && listCtx.window.zoomed ? "Unzoomed" : "Zoomed");
          await refresh({ silent: true });
        } catch (e) {
          toast(String(e.message || e), "err");
        }
      })();
    },
    true,
  );

  const menuBtn = document.createElement("button");
  menuBtn.type = "button";
  menuBtn.className = "pane-tile-menu";
  menuBtn.setAttribute("aria-label", `More actions for pane ${p.pane_id}`);
  menuBtn.setAttribute("aria-haspopup", "menu");
  menuBtn.setAttribute("aria-expanded", "false");
  menuBtn.title = "More pane actions (send keys, capture, resize…)";
  menuBtn.textContent = "⋯";
  menuBtn.addEventListener(
    "click",
    (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      openPopover(menuBtn, buildPaneMenuItems(p, listCtx.window || {}, listCtx.session || {}));
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
  side.appendChild(zoomBtn);
  side.appendChild(menuBtn);
  side.appendChild(del);

  if (mode === "grid") {
    const cell = document.createElement("div");
    cell.className = "pane-mosaic-cell";
    cell.setAttribute("role", "listitem");
    const tl = listCtx.tmuxLayout;
    if (tl && typeof tl.getPlacement === "function") {
      const { colStart, colEndLine, rowStart, rowEndLine } = tl.getPlacement(p);
      cell.style.gridColumn = `${colStart} / ${colEndLine}`;
      cell.style.gridRow = `${rowStart} / ${rowEndLine}`;
    } else {
      const pw = Math.max(Number(p.width) || 0, 1);
      const ph = Math.max(Number(p.height) || 0, 1);
      const left = Number.isFinite(Number(p.left)) ? Number(p.left) : 0;
      const top = Number.isFinite(Number(p.top)) ? Number(p.top) : 0;
      cell.style.gridColumn = `${left + 1} / span ${pw}`;
      cell.style.gridRow = `${top + 1} / span ${ph}`;
    }
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

  detail.appendChild(renderSessionWindowsStrip(session, windows));

  for (const w of windows) {
    detail.appendChild(renderWindowBlock(session, w, windows.length));
  }
}

/** Top strip: optional window tabs + session-level “New window” (outside each window card). */
function renderSessionWindowsStrip(session, windows) {
  const strip = document.createElement("div");
  strip.className = "session-windows-strip";

  if (windows.length > 1) {
    strip.appendChild(renderWindowTabs(session, windows));
  } else {
    const w0 = windows[0];
    const hint = document.createElement("span");
    hint.className = "session-windows-strip-hint";
    if (w0) {
      const nm = w0.name ? String(w0.name) : "window";
      hint.textContent = `Window #1 · ${nm}`;
    } else {
      hint.textContent = "No windows in this session yet";
    }
    strip.appendChild(hint);
  }

  const actions = document.createElement("div");
  actions.className = "session-windows-strip-actions";
  actions.appendChild(makeNewWindowButton(session));
  strip.appendChild(actions);
  return strip;
}

function makeNewWindowButton(session) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "btn btn-sm btn-outline-primary session-new-window-btn";
  btn.innerHTML = '<i class="bi bi-window-plus me-1" aria-hidden="true"></i>New window';
  btn.title = "New window in this session (Ctrl+B c)";
  btn.setAttribute("aria-label", `New window in session ${session.name}`);
  btn.addEventListener("click", async () => {
    try {
      await newWindow(session.name);
      toast("New window created");
      await refresh({ silent: true });
    } catch (e) {
      toast(String(e.message || e), "err");
    }
  });
  return btn;
}

/** Tab bar that scrolls to (and focuses) a window's block (used inside session strip). */
function renderWindowTabs(session, windows) {
  const bar = document.createElement("nav");
  bar.className = "win-tabs";
  bar.setAttribute("aria-label", `Windows in ${session.name}`);
  const activeWin = windows.find((w) => w.active) || windows[0];
  for (const w of windows) {
    const tab = document.createElement("a");
    tab.href = `#win-${session.name}-${w.index}`;
    const isActive = w === activeWin;
    tab.className =
      "win-tab" +
      (isActive ? " is-active" : "") +
      (w.zoomed ? " is-zoomed" : "") +
      (w.synchronized ? " is-sync" : "");
    tab.title = `Jump to window #${w.index + 1} (${(w.panes || []).length} panes)${
      w.zoomed ? " · zoomed" : ""
    }${w.synchronized ? " · synchronized" : ""}`;
    const idx = document.createElement("span");
    idx.className = "win-tab-idx";
    idx.textContent = `#${w.index + 1}`;
    const name = document.createElement("span");
    name.className = "win-tab-name";
    name.textContent = w.name || "window";
    const pill = document.createElement("span");
    pill.className = "win-tab-pill";
    pill.textContent = String((w.panes || []).length);
    tab.appendChild(idx);
    tab.appendChild(name);
    tab.appendChild(pill);
    tab.addEventListener("click", (ev) => {
      ev.preventDefault();
      const target = el(`win-${session.name}-${w.index}`);
      if (target) {
        target.scrollIntoView({ behavior: "smooth", block: "start" });
      }
      (async () => {
        try {
          await focusWindow(session.name, w.index);
          await refresh({ silent: true });
        } catch (e) {
          toast(String(e.message || e), "err");
        }
      })();
    });
    bar.appendChild(tab);
  }
  return bar;
}

function renderWindowBlock(session, w, totalWindows) {
  const block = document.createElement("section");
  block.className = "win-block";
  block.id = `win-${session.name}-${w.index}`;

  block.appendChild(renderWindowHead(session, w, totalWindows));
  block.appendChild(renderWindowMosaic(session, w));
  return block;
}

function renderWindowHead(session, w, totalWindows) {
  const head = document.createElement("div");
  head.className = "win-head";

  /* Row 1: name + status chips + overflow menu */
  const row1 = document.createElement("div");
  row1.className = "win-head-row";

  const nameWrap = document.createElement("div");
  nameWrap.className = "win-name-wrap";
  const wt = document.createElement("div");
  wt.className = "win-title";
  wt.innerHTML = `<span class="idx">#${w.index + 1}</span>${escapeHtml(w.name || "window")}`;
  nameWrap.appendChild(wt);

  const renameBtn = document.createElement("button");
  renameBtn.type = "button";
  renameBtn.className = "win-rename-btn";
  renameBtn.title = "Rename window";
  renameBtn.setAttribute("aria-label", `Rename window ${w.name || "window"}`);
  renameBtn.innerHTML = '<i class="bi bi-pencil"></i>';
  renameBtn.addEventListener("click", () => openRenameWindowModal(session.name, w.index, w.name || ""));
  nameWrap.appendChild(renameBtn);
  row1.appendChild(nameWrap);

  const status = document.createElement("div");
  status.className = "win-status-cluster";
  if (w.active) {
    status.appendChild(makeChip("Active", "win-chip-active", "bi-broadcast"));
  }
  if (w.zoomed) {
    status.appendChild(makeChip("Zoomed", "win-chip-zoom", "bi-arrows-fullscreen"));
  }
  if (w.synchronized) {
    status.appendChild(makeChip("Sync", "win-chip-sync", "bi-keyboard"));
  }
  if (w.layout_name) {
    const layoutChip = document.createElement("span");
    layoutChip.className = "win-chip win-chip-layout";
    layoutChip.textContent = String(w.layout_name);
    layoutChip.title = `tmux layout string: ${w.layout_name}`;
    status.appendChild(layoutChip);
  }
  const paneCount = (w.panes || []).length;
  status.appendChild(
    makeChip(`${paneCount} pane${paneCount === 1 ? "" : "s"}`, "", "bi-grid-3x3"),
  );
  row1.appendChild(status);

  const moreBtn = document.createElement("button");
  moreBtn.type = "button";
  moreBtn.className = "btn btn-sm btn-outline-secondary win-more-btn";
  moreBtn.setAttribute("aria-label", `More actions for window ${w.index + 1}`);
  moreBtn.setAttribute("aria-haspopup", "menu");
  moreBtn.setAttribute("aria-expanded", "false");
  moreBtn.innerHTML = '<i class="bi bi-three-dots"></i>';
  moreBtn.title = "More window actions";
  moreBtn.addEventListener("click", (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    openPopover(moreBtn, buildWindowMenuItems(session, w, totalWindows));
  });
  row1.appendChild(moreBtn);

  head.appendChild(row1);

  /* Row 2: Pane split menu + Layout menu + Sync toggle + Focus window primary */
  const row2 = document.createElement("div");
  row2.className = "win-head-row win-head-row-toolbar";

  const panesList = w.panes || [];
  const paneForSplit = panesList.find((p) => p.active) || panesList[0];

  /* Split panes — dropdown */
  const splitGroup = document.createElement("div");
  splitGroup.className = "win-toolbar-group win-toolbar-group--menu";
  splitGroup.setAttribute("role", "group");
  splitGroup.setAttribute("aria-label", `Split panes in window ${w.index + 1}`);
  const splitBtn = document.createElement("button");
  splitBtn.type = "button";
  splitBtn.className = "win-toolbar-menu-btn";
  splitBtn.setAttribute("aria-haspopup", "menu");
  splitBtn.setAttribute("aria-expanded", "false");
  splitBtn.setAttribute("aria-label", `Split panes in window ${w.index + 1}`);
  splitBtn.innerHTML =
    '<span class="win-toolbar-menu-label">Split</span><i class="bi bi-chevron-down win-toolbar-menu-chevron" aria-hidden="true"></i>';
  splitBtn.title = "Add a pane by splitting the active pane";
  splitBtn.disabled = !paneForSplit;
  splitBtn.addEventListener("click", (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    if (!paneForSplit) return;
    openPopover(splitBtn, [
      { section: "Split active pane" },
      {
        label: "Below (stacked)",
        icon: "bi-layout-split",
        onClick: async () => {
          try {
            await splitPaneApi(paneForSplit.pane_id, true);
            toast("Split (stacked)");
            await refresh({ silent: true });
          } catch (e) {
            toast(String(e.message || e), "err");
          }
        },
      },
      {
        label: "To the right",
        icon: "bi-layout-three-columns",
        onClick: async () => {
          try {
            await splitPaneApi(paneForSplit.pane_id, false);
            toast("Split (side by side)");
            await refresh({ silent: true });
          } catch (e) {
            toast(String(e.message || e), "err");
          }
        },
      },
    ]);
  });
  splitGroup.appendChild(splitBtn);
  row2.appendChild(splitGroup);

  /* Layout presets — dropdown */
  const layoutGroup = document.createElement("div");
  layoutGroup.className = "win-toolbar-group win-toolbar-group--menu";
  layoutGroup.setAttribute("role", "group");
  layoutGroup.setAttribute("aria-label", `Window ${w.index + 1} layouts`);
  const layoutBtn = document.createElement("button");
  layoutBtn.type = "button";
  layoutBtn.className = "win-toolbar-menu-btn";
  layoutBtn.setAttribute("aria-haspopup", "menu");
  layoutBtn.setAttribute("aria-expanded", "false");
  layoutBtn.innerHTML =
    '<span class="win-toolbar-menu-label">Layout</span><i class="bi bi-chevron-down win-toolbar-menu-chevron" aria-hidden="true"></i>';
  layoutBtn.title = w.layout_name
    ? `Current tmux layout string (see chip above). Apply a named preset via select-layout.`
    : "Apply a tmux layout preset (select-layout)";
  layoutBtn.setAttribute("aria-label", `Layout presets for window ${w.index + 1}`);
  layoutBtn.addEventListener("click", (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    openPopover(layoutBtn, [
      { section: "Apply layout" },
      ...LAYOUTS.map((L) => ({
        label: L.label,
        icon: L.icon,
        onClick: async () => {
          try {
            await applyLayout(session.name, w.index, L.kind);
            toast(`Layout: ${L.label}`);
            await refresh({ silent: true });
          } catch (e) {
            toast(String(e.message || e), "err");
          }
        },
      })),
    ]);
  });
  layoutGroup.appendChild(layoutBtn);
  row2.appendChild(layoutGroup);

  /* Sync toggle */
  const syncBtn = document.createElement("button");
  syncBtn.type = "button";
  syncBtn.className = "win-sync-toggle" + (w.synchronized ? " is-on" : "");
  syncBtn.setAttribute("aria-pressed", w.synchronized ? "true" : "false");
  syncBtn.title = w.synchronized
    ? "Synchronize panes is ON — keystrokes mirror to every pane in this window"
    : "Toggle synchronize-panes — type once, run in every pane";
  syncBtn.innerHTML = `<span class="win-sync-glyph" aria-hidden="true">⇆</span>Sync ${w.synchronized ? "On" : "Off"}`;
  syncBtn.addEventListener("click", async () => {
    try {
      await postSynchronizeWindow(session.name, w.index, !w.synchronized);
      toast(`Synchronize-panes: ${!w.synchronized ? "on" : "off"}`);
      await refresh({ silent: true });
    } catch (e) {
      toast(String(e.message || e), "err");
    }
  });
  row2.appendChild(syncBtn);

  const spacer = document.createElement("div");
  spacer.className = "win-toolbar-spacer";
  row2.appendChild(spacer);

  /* Focus button */
  const fw = document.createElement("button");
  fw.type = "button";
  fw.className = "btn-layout primary-action";
  fw.innerHTML = '<i class="bi bi-bullseye me-1"></i>Focus window';
  fw.title = "Select this window in tmux (select-window)";
  fw.addEventListener("click", async () => {
    try {
      await focusWindow(session.name, w.index);
      toast("Window focused");
      await refresh({ silent: true });
    } catch (e) {
      toast(String(e.message || e), "err");
    }
  });
  row2.appendChild(fw);

  head.appendChild(row2);
  return head;
}

function makeChip(label, extraClass, icon) {
  const c = document.createElement("span");
  c.className = "win-chip" + (extraClass ? ` ${extraClass}` : "");
  if (icon) {
    c.innerHTML = `<i class="bi ${icon} win-chip-icon" aria-hidden="true"></i>${escapeHtml(label)}`;
  } else {
    c.textContent = label;
  }
  return c;
}

function buildWindowMenuItems(session, w, totalWindows) {
  return [
    { section: "Window" },
    {
      label: "Rename window…",
      icon: "bi-pencil",
      onClick: () => openRenameWindowModal(session.name, w.index, w.name || ""),
    },
    {
      label: "Move ← (swap with previous)",
      icon: "bi-arrow-left",
      onClick: async () => {
        if (w.index <= 0) {
          toast("Already the first window.", "warn");
          return;
        }
        await postMoveWindow(session.name, w.index, "left");
        toast("Window moved left");
        await refresh({ silent: true });
      },
    },
    {
      label: "Move → (swap with next)",
      icon: "bi-arrow-right",
      onClick: async () => {
        if (w.index >= totalWindows - 1) {
          toast("Already the last window.", "warn");
          return;
        }
        await postMoveWindow(session.name, w.index, "right");
        toast("Window moved right");
        await refresh({ silent: true });
      },
    },
    {
      label: w.synchronized ? "Disable synchronize-panes" : "Enable synchronize-panes",
      icon: "bi-keyboard",
      onClick: async () => {
        await postSynchronizeWindow(session.name, w.index, !w.synchronized);
        toast(`Synchronize-panes: ${!w.synchronized ? "on" : "off"}`);
        await refresh({ silent: true });
      },
    },
    { divider: true },
    {
      label: "Close window",
      icon: "bi-x-circle",
      danger: true,
      onClick: async () => {
        const wn = w.name || "window";
        if (
          !confirm(
            `Close window #${w.index + 1} "${wn}"? If this is the only window, tmux may end the whole session.`,
          )
        ) {
          return;
        }
        await deleteWindowApi(session.name, w.index);
        toast("Window closed");
        await refresh({ silent: true });
      },
    },
  ];
}

function renderWindowMosaic(session, w) {
  const panes = w.panes || [];
  const totalW = panes.reduce((s, p) => s + Math.max(p.width || 0, 1), 0) || 1;
  const useTmuxGrid = tmuxPanePositionsDistinct(panes);
  const tmuxLayout = useTmuxGrid ? buildTmuxGridLayout(panes) : null;

  const mosaic = document.createElement("div");
  mosaic.className = "pane-mosaic" + (useTmuxGrid ? " pane-mosaic--tmux" : " pane-mosaic--list");
  mosaic.setAttribute("role", "list");
  if (tmuxLayout) {
    mosaic.style.gridTemplateColumns = tmuxLayout.columnTemplate;
    mosaic.style.gridTemplateRows = tmuxLayout.rowTemplate;
    mosaic.style.setProperty("--tmux-aspect-w", String(tmuxLayout.aspectW));
    mosaic.style.setProperty("--tmux-aspect-h", String(tmuxLayout.aspectH));
  }

  const mode = useTmuxGrid ? "grid" : "list";
  const listCtx = { totalW, window: w, session, tmuxLayout };
  for (const p of panes) {
    appendPaneToMosaic(mosaic, p, mode, listCtx);
  }
  return mosaic;
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
  const now = Date.now();
  if (!inField && !inDialog && chordLeader && now - chordLeaderAt > 1200) {
    chordLeader = null;
  }

  if (!inField && !inDialog && chordLeader === "g") {
    if (ev.key === "s" || ev.key === "S") {
      ev.preventDefault();
      chordLeader = null;
      switchMainView("sessions");
      return;
    }
    if (ev.key === "p" || ev.key === "P") {
      ev.preventDefault();
      chordLeader = null;
      switchMainView("plugins");
      return;
    }
  }

  if (ev.key === "/" && !inField && !inDialog && mainView === "sessions") {
    ev.preventDefault();
    el("filter").focus();
    el("filter").select();
    chordLeader = null;
  }
  if ((ev.key === "r" || ev.key === "R") && !inField && !inDialog) {
    ev.preventDefault();
    refresh();
    chordLeader = null;
  }
  if ((ev.key === "n" || ev.key === "N") && !inField && !inDialog && mainView === "sessions") {
    ev.preventDefault();
    el("btn-new-session")?.click();
    chordLeader = null;
  }
  if ((ev.key === "g" || ev.key === "G") && !inField && !inDialog) {
    ev.preventDefault();
    chordLeader = "g";
    chordLeaderAt = now;
    return;
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

function bindOpenSnapshotsRestoreModal(btn) {
  btn?.addEventListener("click", () => {
    closeSnapMenu();
    openSnapshotsRestoreModal();
  });
}
bindOpenSnapshotsRestoreModal(el("btn-restore-snapshot"));

el("modal-snapshots-restore-close")?.addEventListener("click", () => {
  el("modal-snapshots-restore")?.close();
});

el("modal-snapshots-restore-dismiss")?.addEventListener("click", () => {
  el("modal-snapshots-restore")?.close();
});

// ---------- snapshot split-button dropdown ----------

function closeSnapMenu() {
  const menu = el("snap-menu");
  const toggle = el("btn-snap-menu-toggle");
  menu?.classList.add("hidden");
  toggle?.setAttribute("aria-expanded", "false");
}

function openSnapMenu() {
  const menu = el("snap-menu");
  const toggle = el("btn-snap-menu-toggle");
  menu?.classList.remove("hidden");
  toggle?.setAttribute("aria-expanded", "true");
  el("btn-restore-snapshot")?.focus();
}

el("btn-snap-menu-toggle")?.addEventListener("click", (ev) => {
  ev.stopPropagation();
  const menu = el("snap-menu");
  if (menu?.classList.contains("hidden")) {
    openSnapMenu();
  } else {
    closeSnapMenu();
  }
});

// Save via the dropdown item (duplicates the main btn-save-snapshot)
el("btn-save-snapshot-menu")?.addEventListener("click", async () => {
  closeSnapMenu();
  const btn = el("btn-save-snapshot");
  if (btn) btn.disabled = true;
  try {
    const r = await saveSnapshot("default");
    toast(`Snapshot saved (#${r.id})`);
  } catch (e) {
    toast(String(e.message || e), "err");
  } finally {
    if (btn) btn.disabled = false;
  }
});

// ---------- rich snapshot dialog ----------

async function fetchResurrectStatus() {
  try {
    const res = await fetch(apiUrl("/api/v1/snapshots/resurrect"), {
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

async function syncResurrectControl(checkboxId, hintId, options = {}) {
  const cb = /** @type {HTMLInputElement|null} */ (el(checkboxId));
  const hint = el(hintId);
  if (!cb) return null;
  const status = await fetchResurrectStatus();
  if (!status) return null;
  const installed = Boolean(status.installed);
  const configured = Boolean(status.configured);
  cb.disabled = !installed;
  if (options.defaultCheckedWhenInstalled && installed) cb.checked = true;
  if (!installed) cb.checked = false;
  if (hint) {
    let msg = "";
    if (!configured) {
      msg = "Plugin not in plugins.tmux — open Plugins → Add to enable this.";
    } else if (!installed) {
      msg = "Listed in plugins.tmux but not installed yet — open Plugins → Install.";
    } else {
      const where = status.save_dir ? ` Saves to ${status.save_dir}.` : "";
      msg = `tmux-resurrect installed.${where}`;
    }
    hint.textContent = msg;
    hint.classList.toggle("hidden", !msg);
  }
  return status;
}

async function openSnapshotSaveRichModal() {
  closeSnapMenu();
  const modal = el("modal-snapshot-save-rich");
  if (!modal) return;
  const labelInput = /** @type {HTMLInputElement|null} */ (el("snapshot-save-label-input"));
  if (labelInput && !labelInput.value.trim()) labelInput.value = "default";
  modal.showModal();
  setTimeout(() => labelInput?.focus(), 0);
  syncResurrectControl("snapshot-save-use-resurrect", "snapshot-save-resurrect-hint", {
    defaultCheckedWhenInstalled: true,
  });
}

el("btn-save-snapshot-rich")?.addEventListener("click", openSnapshotSaveRichModal);
el("modal-snapshot-save-rich-dismiss")?.addEventListener("click", () => {
  el("modal-snapshot-save-rich")?.close();
});
el("modal-snapshot-save-rich-cancel")?.addEventListener("click", () => {
  el("modal-snapshot-save-rich")?.close();
});

el("form-snapshot-save-rich")?.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const submit = /** @type {HTMLButtonElement|null} */ (el("modal-snapshot-save-rich-submit"));
  const labelInput = /** @type {HTMLInputElement|null} */ (el("snapshot-save-label-input"));
  const sbCheck = /** @type {HTMLInputElement|null} */ (el("snapshot-save-include-scrollback"));
  const histCheck = /** @type {HTMLInputElement|null} */ (el("snapshot-save-include-history"));
  const sbLines = /** @type {HTMLInputElement|null} */ (el("snapshot-save-scrollback-lines"));
  const histLines = /** @type {HTMLInputElement|null} */ (el("snapshot-save-history-lines"));
  const resurrectCheck = /** @type {HTMLInputElement|null} */ (el("snapshot-save-use-resurrect"));
  const label = (labelInput?.value || "default").trim() || "default";
  const include_scrollback = Boolean(sbCheck?.checked);
  const include_history = Boolean(histCheck?.checked);
  const scrollback_lines = Number.parseInt(sbLines?.value || "2000", 10);
  const history_lines = Number.parseInt(histLines?.value || "200", 10);
  const use_resurrect = Boolean(resurrectCheck?.checked && !resurrectCheck.disabled);
  if (submit) submit.disabled = true;
  try {
    const r = await saveSnapshot(label, {
      include_scrollback,
      include_history,
      scrollback_lines,
      history_lines,
      use_resurrect,
    });
    const tags = [];
    if (r.include_scrollback) tags.push("scrollback");
    if (r.include_history) tags.push("history");
    if (r.use_resurrect) tags.push("resurrect");
    const suffix = tags.length ? ` — ${tags.join(" + ")}` : "";
    toast(`Snapshot saved (#${r.id})${suffix}`);
    el("modal-snapshot-save-rich")?.close();
  } catch (e) {
    toast(String(e.message || e), "err");
  } finally {
    if (submit) submit.disabled = false;
  }
});

// Close dropdown when clicking outside
document.addEventListener("click", (ev) => {
  if (!el("snap-menu")?.classList.contains("hidden")) {
    if (!ev.target?.closest?.(".dmux-snap-split") && !ev.target?.closest?.("#snap-menu")) {
      closeSnapMenu();
    }
  }
});

// Keyboard: Escape closes the dropdown
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && !el("snap-menu")?.classList.contains("hidden")) {
    closeSnapMenu();
    el("btn-snap-menu-toggle")?.focus();
  }
});

el("nav-sessions")?.addEventListener("click", async () => {
  await switchMainView("sessions");
});

el("nav-plugins")?.addEventListener("click", () => {
  switchMainView("plugins");
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
    if (path === "bootstrap" || path === "remove-tmux-hook") {
      const detail = data.detail && String(data.detail).trim();
      const tc = data.tmux_conf && String(data.tmux_conf).trim();
      const parts = [okMsg];
      if (detail) parts.push(detail);
      else if (tc) parts.push(tc);
      toast(parts.join(" — "));
    } else {
      toast(data.output ? `${okMsg}: ${data.output.slice(0, 200)}` : okMsg);
    }
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
el("btn-plugins-remove-tmux-hook")?.addEventListener("click", () =>
  runPluginOp(el("btn-plugins-remove-tmux-hook"), "remove-tmux-hook", "tmux.conf hook"),
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
el("btn-plugins-source")?.addEventListener("click", () => sourceFragmentInTmux());
el("modal-plugins-fragment-source")?.addEventListener("click", () => sourceFragmentInTmux());

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
    const savedPath = j.path && String(j.path).trim();
    toast(savedPath ? `Saved: ${savedPath}` : "plugins.tmux saved");
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
    const absPath = j.path && String(j.path).trim() ? String(j.path) : "—";
    pathEl.textContent = absPath;
    const saveBtn = el("modal-plugins-fragment-save");
    if (saveBtn) {
      saveBtn.title =
        absPath !== "—"
          ? `Write editor contents to this file: ${absPath}`
          : "Save plugins.tmux";
    }
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

/* =========================================================
 * Send-keys modal wiring
 * ========================================================= */

function openSendKeysModal(paneId) {
  const d = el("modal-send-keys");
  const hid = el("send-keys-pane-id");
  const txt = el("send-keys-text");
  const enter = el("send-keys-enter");
  const literal = el("send-keys-literal");
  const disp = el("send-keys-pane-display");
  if (!d || !hid || !txt) return;
  hid.value = paneId;
  if (disp) disp.textContent = `Pane: ${paneId}`;
  txt.value = "";
  if (enter) enter.checked = true;
  if (literal) literal.checked = false;
  try {
    d.showModal();
  } catch (e) {
    toast(String(e.message || e), "err");
    return;
  }
  requestAnimationFrame(() => txt.focus());
}

el("send-keys-cancel")?.addEventListener("click", () => el("modal-send-keys")?.close());

document.querySelectorAll("[data-send-key]").forEach((btn) => {
  btn.addEventListener("click", async (ev) => {
    ev.preventDefault();
    const paneId = String(el("send-keys-pane-id")?.value || "").trim();
    if (!paneId) return;
    const key = String(btn.getAttribute("data-send-key") || "");
    const literal = (btn.getAttribute("data-send-literal") || "false") === "true";
    const enter = (btn.getAttribute("data-send-enter") || "false") === "true";
    if (!key) return;
    btn.disabled = true;
    try {
      await postSendKeys(paneId, { text: key, enter, literal });
      toast(`Sent ${key}`);
    } catch (e) {
      toast(String(e.message || e), "err");
    } finally {
      btn.disabled = false;
    }
  });
});

el("form-send-keys")?.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const submit = el("send-keys-submit");
  const paneId = String(el("send-keys-pane-id")?.value || "").trim();
  const text = String(el("send-keys-text")?.value ?? "");
  if (!paneId || !text) return;
  const enter = !!el("send-keys-enter")?.checked;
  const literal = !!el("send-keys-literal")?.checked;
  if (submit) submit.disabled = true;
  try {
    await postSendKeys(paneId, { text, enter, literal });
    toast(`Sent to ${paneId}`);
    el("modal-send-keys")?.close();
  } catch (e) {
    toast(String(e.message || e), "err");
  } finally {
    if (submit) submit.disabled = false;
  }
});

/* =========================================================
 * Capture modal wiring
 * ========================================================= */

let _captureFollowTimer = null;
let _captureCurrentPane = null;

function captureLines() {
  const v = parseInt(el("capture-lines")?.value || "200", 10);
  return Number.isFinite(v) && v >= 0 ? v : 200;
}

async function refreshCapture() {
  const out = el("capture-output");
  const meta = el("capture-meta");
  if (!out || !_captureCurrentPane) return;
  try {
    const t = await getPaneCapture(_captureCurrentPane, captureLines());
    out.textContent = t || "(empty)";
    if (meta) {
      meta.innerHTML = `<span><strong>${escapeHtml(_captureCurrentPane)}</strong></span><span>${t.split("\n").length} lines</span><span>${formatSyncTime()}</span>`;
    }
  } catch (e) {
    out.textContent = `Error: ${String(e.message || e)}`;
  }
}

function stopCaptureFollow() {
  if (_captureFollowTimer) clearInterval(_captureFollowTimer);
  _captureFollowTimer = null;
}

function openCaptureModal(paneId) {
  const d = el("modal-capture");
  if (!d) return;
  _captureCurrentPane = paneId;
  const follow = el("capture-follow");
  if (follow) follow.checked = false;
  stopCaptureFollow();
  try {
    d.showModal();
  } catch (e) {
    toast(String(e.message || e), "err");
    return;
  }
  refreshCapture();
}

el("capture-close")?.addEventListener("click", () => {
  stopCaptureFollow();
  _captureCurrentPane = null;
  el("modal-capture")?.close();
});
el("modal-capture")?.addEventListener("close", () => {
  stopCaptureFollow();
  _captureCurrentPane = null;
});
el("capture-refresh")?.addEventListener("click", () => refreshCapture());
el("capture-follow")?.addEventListener("change", (ev) => {
  stopCaptureFollow();
  if (ev.target.checked && _captureCurrentPane) {
    _captureFollowTimer = setInterval(refreshCapture, 1000);
    refreshCapture();
  }
});
el("capture-lines")?.addEventListener("change", () => refreshCapture());
el("capture-copy")?.addEventListener("click", async () => {
  const out = el("capture-output");
  if (!out) return;
  try {
    await navigator.clipboard.writeText(out.textContent || "");
    toast("Capture copied to clipboard");
  } catch (e) {
    toast(`Copy failed: ${String(e.message || e)}`, "err");
  }
});

/* =========================================================
 * Resize modal wiring (d-pad)
 * ========================================================= */

function openResizeModal(paneId) {
  const d = el("modal-resize");
  const hid = el("resize-pane-id");
  const disp = el("resize-pane-display");
  if (!d || !hid) return;
  hid.value = paneId;
  if (disp) disp.textContent = `Pane: ${paneId}`;
  try {
    d.showModal();
  } catch (e) {
    toast(String(e.message || e), "err");
  }
}

el("resize-close")?.addEventListener("click", () => el("modal-resize")?.close());

document.querySelectorAll("#modal-resize [data-resize-dir]").forEach((btn) => {
  btn.addEventListener("click", async (ev) => {
    ev.preventDefault();
    const paneId = String(el("resize-pane-id")?.value || "").trim();
    if (!paneId) return;
    const step = Math.max(1, parseInt(el("resize-step")?.value || "5", 10) || 5);
    const dir = btn.getAttribute("data-resize-dir");
    let dx = 0;
    let dy = 0;
    if (dir === "left") dx = -step;
    else if (dir === "right") dx = step;
    else if (dir === "up") dy = -step;
    else if (dir === "down") dy = step;
    btn.disabled = true;
    try {
      await postResizePane(paneId, dx, dy);
      await refresh({ silent: true });
    } catch (e) {
      toast(String(e.message || e), "err");
    } finally {
      btn.disabled = false;
    }
  });
});

/* =========================================================
 * Rename-window modal wiring
 * ========================================================= */

function openRenameWindowModal(sessionName, windowIndex, currentName) {
  const d = el("modal-rename-window");
  const sess = el("rename-win-session");
  const idx = el("rename-win-index");
  const inp = el("rename-win-name");
  const disp = el("rename-win-display");
  if (!d || !sess || !idx || !inp) return;
  sess.value = sessionName;
  idx.value = String(windowIndex);
  inp.value = currentName || "";
  if (disp) disp.textContent = `${sessionName} · window #${windowIndex + 1}`;
  try {
    d.showModal();
  } catch (e) {
    toast(String(e.message || e), "err");
    return;
  }
  requestAnimationFrame(() => {
    inp.focus();
    inp.select();
  });
}

el("rename-win-cancel")?.addEventListener("click", () => el("modal-rename-window")?.close());

el("form-rename-window")?.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const submit = el("rename-win-submit");
  const sess = String(el("rename-win-session")?.value || "").trim();
  const idx = parseInt(String(el("rename-win-index")?.value || ""), 10);
  const name = String(el("rename-win-name")?.value || "").trim();
  if (!sess || !Number.isFinite(idx) || !name) return;
  if (submit) submit.disabled = true;
  try {
    await patchRenameWindow(sess, idx, name);
    toast(`Renamed to “${name}”`);
    el("modal-rename-window")?.close();
    await refresh({ silent: true });
  } catch (e) {
    toast(String(e.message || e), "err");
  } finally {
    if (submit) submit.disabled = false;
  }
});

/* =========================================================
 * Server info in topbar (lazy, on first refresh)
 * ========================================================= */

async function loadServerInfo() {
  const info = await fetchServerInfo();
  if (!info) return;
  const meta = el("sync-meta");
  if (!meta) return;
  const v = info.version ? String(info.version) : "";
  const sock = info.socket_path ? `socket ${info.socket_path}` : "default socket";
  const clients = info.clients ? `${info.clients} client${info.clients === "1" ? "" : "s"}` : "0 clients";
  const extra = `<span class="server-info">· ${escapeHtml(v)} · ${escapeHtml(sock)} · ${escapeHtml(clients)}</span>`;
  // Append once; remove existing tail if present.
  const html = meta.innerHTML;
  const idx = html.indexOf('<span class="server-info">');
  meta.innerHTML = (idx >= 0 ? html.slice(0, idx) : html) + extra;
}

updateFilterClear();
setupPaneStyleForm();
setupPluginSpecAutocomplete();
initPluginsTableCompact();
refresh().then(() => loadServerInfo());
setupAutoRefresh();
