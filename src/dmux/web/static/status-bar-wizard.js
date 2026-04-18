/**
 * Wizard for Freed-Wu/tmux-status-bar — builds JIT #{status-left:…} / #{status-right:…} / window lines.
 * Depends on globals: apiUrl, toast (from app.js).
 */
(function () {
  const SPEC = "Freed-Wu/tmux-status-bar";

  const PRESET = {
    leftReadmePaste:
      'set -g status-left "#{status-left:#fffafa,black,#S;blue,green,#{pane_current_command};}"',
    leftAotPaste: 'set -g status-left "#{status-left:#fffafa,black,#S}"',
    rightSessionRows: [{ fg: "white", bg: "colour04", text: "#{session_name}" }],
    rightPrefixPaste:
      'set -g status-right "#{status-right:white,colour04,#{prefix_highlight}#[bg=colour04];}"',
    winIndexNameRows: [{ fg: "colour235", bg: "colour252", text: "#{window_index} #{window_name}" }],
  };

  const $ = (id) => document.getElementById(id);

  function escapeSeg(s) {
    return String(s).replace(/\\/g, "\\\\").replace(/"/g, '\\"');
  }

  function buildInner(rows, leadPercentS) {
    const parts = rows.filter((r) => String(r.fg || "").trim() || String(r.bg || "").trim() || String(r.text || "").trim());
    if (!parts.length) return "";
    const segs = parts
      .map((r) =>
        [escapeSeg(String(r.fg || "").trim()), escapeSeg(String(r.bg || "").trim()), escapeSeg(String(r.text || ""))].join(
          ",",
        ),
      )
      .join(";");
    /* No "%s;" lead: tmux parses status-* with strftime and turns %s into Unix time. */
    void leadPercentS;
    return segs;
  }

  function buildLine(innerFn, tmuxOption, rows, lead) {
    const inner = buildInner(rows, lead);
    if (!inner) return "";
    return "set -g " + tmuxOption + ' "#{' + innerFn + ":" + inner + '}"';
  }

  function readRows(tbodyId) {
    const tb = $(tbodyId);
    if (!tb) return [];
    const out = [];
    tb.querySelectorAll("tr").forEach((tr) => {
      const fg = tr.querySelector('input[data-part="fg"]');
      const bg = tr.querySelector('input[data-part="bg"]');
      const tx = tr.querySelector('input[data-part="text"]');
      if (fg && bg && tx) out.push({ fg: fg.value, bg: bg.value, text: tx.value });
    });
    return out;
  }

  function addRow(tbodyId, row) {
    const tb = $(tbodyId);
    if (!tb) return;
    const tr = document.createElement("tr");
    const r = row || { fg: "", bg: "", text: "" };
    tr.innerHTML = `
      <td><input type="text" data-part="fg" autocomplete="off" spellcheck="false" /></td>
      <td><input type="text" data-part="bg" autocomplete="off" spellcheck="false" /></td>
      <td><input type="text" data-part="text" autocomplete="off" spellcheck="false" /></td>
      <td class="sbw-remove"><button type="button" class="btn btn-sm btn-outline-secondary sbw-row-remove" title="Remove segment">×</button></td>
    `;
    tr.querySelector('[data-part="fg"]').value = r.fg;
    tr.querySelector('[data-part="bg"]').value = r.bg;
    tr.querySelector('[data-part="text"]').value = r.text;
    tr.querySelector(".sbw-row-remove").addEventListener("click", () => {
      tr.remove();
      updatePreview();
    });
    tr.querySelectorAll("input").forEach((inp) => inp.addEventListener("input", updatePreview));
    tb.appendChild(tr);
  }

  function clearTbody(tbodyId) {
    const tb = $(tbodyId);
    if (tb) tb.innerHTML = "";
  }

  function syncLeftRightPasteVisibility() {
    const lv = $("sbw-left-input").value === "visual";
    $("sbw-left-visual").classList.toggle("hidden", !lv);
    $("sbw-left-paste-wrap").classList.toggle("hidden", lv);

    const rv = $("sbw-right-input").value === "visual";
    $("sbw-right-visual").classList.toggle("hidden", !rv);
    $("sbw-right-paste-wrap").classList.toggle("hidden", rv);
  }

  function syncWinVisibility() {
    const mode = $("sbw-win-mode").value;
    const off = mode === "off";
    const usePaste = $("sbw-win-use-paste").checked;
    document.querySelectorAll(".sbw-win-opt").forEach((n) => n.classList.toggle("hidden", off));
    if (off) return;
    $("sbw-win-visual").classList.toggle("hidden", usePaste);
    $("sbw-win-paste-wrap").classList.toggle("hidden", !usePaste);
  }

  function collectLines() {
    const out = [];
    out.push("# --- dmux wizard: Freed-Wu/tmux-status-bar ---");
    out.push("# https://github.com/Freed-Wu/tmux-status-bar");

    if ($("sbw-left-input").value === "paste") {
      const t = $("sbw-left-paste").value.trim();
      if (t) out.push(t);
    } else {
      const line = buildLine("status-left", "status-left", readRows("sbw-left-tbody"), $("sbw-left-lead").checked);
      if (line) out.push(line);
    }

    if ($("sbw-right-input").value === "paste") {
      const t = $("sbw-right-paste").value.trim();
      if (t) out.push(t);
    } else {
      const line = buildLine("status-right", "status-right", readRows("sbw-right-tbody"), $("sbw-right-lead").checked);
      if (line) out.push(line);
    }

    const wm = $("sbw-win-mode").value;
    if (wm !== "off") {
      if ($("sbw-win-use-paste").checked) {
        const t = $("sbw-win-paste").value.trim();
        if (t) out.push(t);
      } else {
        const inner = wm === "left" ? "window-status-current-format-left" : "window-status-current-format-right";
        const line = buildLine(inner, "window-status-current-format", readRows("sbw-win-tbody"), $("sbw-win-lead").checked);
        if (line) out.push(line);
      }
    }

    return out;
  }

  function updatePreview() {
    const pre = $("sbw-preview");
    if (!pre) return;
    try {
      pre.textContent = collectLines().join("\n");
    } catch (e) {
      pre.textContent = String(e.message || e);
    }
  }

  let pluginInstalled = false;

  async function refreshPluginStatus() {
    const msg = $("sbw-plugin-msg");
    if (!msg) return;
    pluginInstalled = false;
    try {
      const res = await fetch(new URL("/api/v1/plugins", window.location.href).href, {
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      const data = await res.json().catch(() => ({}));
      const rows = Array.isArray(data.plugins) ? data.plugins : [];
      pluginInstalled = rows.some((r) => String(r.spec || "") === SPEC);
      msg.textContent = pluginInstalled
        ? `${SPEC} is in your plugin list — Apply will update its block in plugins.tmux.`
        : `Add ${SPEC} to the plugin list before applying (use Add plugin above), or Apply will fail.`;
    } catch {
      msg.textContent = "Could not check plugin list.";
    }
  }

  const OPTS_MARKER_PREFIX = "# dmux:opts:";

  function extractBlock(content, spec) {
    const lines = content.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
    const marker = `${OPTS_MARKER_PREFIX}${spec}`;
    const mi = lines.findIndex((l) => l.trim() === marker);
    if (mi >= 0) {
      const out = [];
      for (let j = mi + 1; j < lines.length; j++) {
        const s = lines[j];
        if (s.startsWith(OPTS_MARKER_PREFIX)) break;
        out.push(s);
      }
      return out;
    }
    const needle = `set -g @plugin '${spec}'`;
    const i = lines.findIndex((l) => l.trim() === needle);
    if (i < 0) return [];
    const out = [];
    for (let j = i + 1; j < lines.length; j++) {
      const s = lines[j];
      if (/^set -g @plugin '/.test(s.trim()) || /^run '/.test(s.trim())) break;
      out.push(s);
    }
    return out;
  }

  async function importFromFragment() {
    try {
      const res = await fetch(new URL("/api/v1/plugins/fragment", window.location.href).href, {
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      const data = await res.json().catch(() => ({}));
      const content = typeof data.content === "string" ? data.content : "";
      const block = extractBlock(content, SPEC);
      if (!block.length) {
        toast("No block found for " + SPEC + " in plugins.tmux", "err");
        return;
      }
      let gotL = false;
      let gotR = false;
      let gotW = false;
      for (const raw of block) {
        const line = raw.trim();
        if (!line || line.startsWith("#")) continue;
        if (line.startsWith("set -g status-left")) {
          $("sbw-left-input").value = "paste";
          $("sbw-left-paste").value = line;
          gotL = true;
        } else if (line.startsWith("set -g status-right")) {
          $("sbw-right-input").value = "paste";
          $("sbw-right-paste").value = line;
          gotR = true;
        } else if (line.startsWith("set -g window-status-current-format")) {
          $("sbw-win-use-paste").checked = true;
          $("sbw-win-paste").value = line;
          if (line.includes("window-status-current-format-left")) $("sbw-win-mode").value = "left";
          else if (line.includes("window-status-current-format-right")) $("sbw-win-mode").value = "right";
          else $("sbw-win-mode").value = "left";
          gotW = true;
        }
      }
      syncLeftRightPasteVisibility();
      syncWinVisibility();
      updatePreview();
      toast(
        gotL || gotR || gotW
          ? "Imported lines into paste fields — review and Apply."
          : "Block found but no set -g status/window lines recognized.",
      );
    } catch (e) {
      toast(String(e.message || e), "err");
    }
  }

  function resetDefaults() {
    $("sbw-left-input").value = "visual";
    $("sbw-right-input").value = "visual";
    $("sbw-left-lead").checked = false;
    $("sbw-right-lead").checked = false;
    $("sbw-win-mode").value = "off";
    $("sbw-win-lead").checked = false;
    $("sbw-win-use-paste").checked = false;
    $("sbw-left-paste").value = "";
    $("sbw-right-paste").value = "";
    $("sbw-win-paste").value = "";

    clearTbody("sbw-left-tbody");
    addRow("sbw-left-tbody", { fg: "#fffafa", bg: "black", text: "a" });
    addRow("sbw-left-tbody", { fg: "black", bg: "green", text: "" });
    addRow("sbw-left-tbody", { fg: "blue", bg: "green", text: "b" });

    clearTbody("sbw-right-tbody");
    addRow("sbw-right-tbody", { fg: "white", bg: "colour04", text: "#{session_name}" });

    clearTbody("sbw-win-tbody");
    addRow("sbw-win-tbody", { fg: "colour235", bg: "colour252", text: "#{window_index} #{window_name}" });

    syncLeftRightPasteVisibility();
    syncWinVisibility();
    updatePreview();
  }

  function applyPreset(target, name) {
    if (target === "left") {
      if (name === "readme-demo") {
        $("sbw-left-input").value = "paste";
        $("sbw-left-paste").value = PRESET.leftReadmePaste;
      } else if (name === "aot-simple") {
        $("sbw-left-input").value = "paste";
        $("sbw-left-paste").value = PRESET.leftAotPaste;
      }
    } else if (target === "right") {
      if (name === "session") {
        $("sbw-right-input").value = "visual";
        clearTbody("sbw-right-tbody");
        PRESET.rightSessionRows.forEach((r) => addRow("sbw-right-tbody", r));
        $("sbw-right-lead").checked = false;
      } else if (name === "prefix-time") {
        $("sbw-right-input").value = "paste";
        $("sbw-right-paste").value = PRESET.rightPrefixPaste;
      }
    }
    syncLeftRightPasteVisibility();
    updatePreview();
  }

  async function applyWizard() {
    const lines = collectLines();
    if (!pluginInstalled) {
      if (!window.confirm(`${SPEC} is not in your plugin list. Add it first, then apply. Cancel to stop.`)) return;
    }
    try {
      const res = await fetch(new URL("/api/v1/plugins/plugin-lines", window.location.href).href, {
        method: "POST",
        cache: "no-store",
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: JSON.stringify({ plugin: SPEC, lines }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      toast("plugins.tmux updated for " + SPEC);
      $("modal-status-bar-wizard")?.close();
    } catch (e) {
      toast(String(e.message || e), "err");
    }
  }

  function openWizard() {
    resetDefaults();
    refreshPluginStatus();
    $("modal-status-bar-wizard")?.showModal();
  }

  function init() {
    const dlg = $("modal-status-bar-wizard");
    const openBtn = $("btn-plugins-status-bar-wizard");
    if (!dlg || !openBtn) return;

    openBtn.addEventListener("click", () => {
      openWizard();
    });

    $("sbw-close")?.addEventListener("click", () => dlg.close());
    $("sbw-apply")?.addEventListener("click", () => applyWizard());
    $("sbw-reset")?.addEventListener("click", () => {
      resetDefaults();
      refreshPluginStatus();
    });
    $("sbw-import")?.addEventListener("click", () => importFromFragment());

    ["sbw-left-input", "sbw-right-input", "sbw-win-mode", "sbw-win-use-paste"].forEach((id) => {
      $(id)?.addEventListener("change", () => {
        syncLeftRightPasteVisibility();
        syncWinVisibility();
        updatePreview();
      });
    });

    [
      "sbw-left-lead",
      "sbw-right-lead",
      "sbw-win-lead",
      "sbw-left-paste",
      "sbw-right-paste",
      "sbw-win-paste",
    ].forEach((id) => {
      $(id)?.addEventListener("input", updatePreview);
    });

    document.querySelectorAll(".sbw-add-row").forEach((btn) => {
      btn.addEventListener("click", () => {
        addRow(btn.getAttribute("data-sbw-tbody"), { fg: "", bg: "", text: "" });
        updatePreview();
      });
    });

    document.querySelectorAll(".sbw-preset").forEach((btn) => {
      btn.addEventListener("click", () => {
        applyPreset(btn.getAttribute("data-target"), btn.getAttribute("data-preset"));
      });
    });

    $("sbw-preset-win-simple")?.addEventListener("click", () => {
      $("sbw-win-mode").value = "left";
      $("sbw-win-use-paste").checked = false;
      clearTbody("sbw-win-tbody");
      PRESET.winIndexNameRows.forEach((r) => addRow("sbw-win-tbody", r));
      syncWinVisibility();
      updatePreview();
    });

  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
