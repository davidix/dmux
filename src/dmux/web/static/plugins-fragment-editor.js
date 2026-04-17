/**
 * CodeMirror-based plugins.tmux editor.
 */
(function (global) {
  let cmInstance = null;
  let dirty = false;

  global.dmuxInitPluginsFragmentEditor = function (host) {
    if (!global.CodeMirror || !host) return null;
    if (cmInstance) return cmInstance;
    cmInstance = CodeMirror(host, {
      lineNumbers: true,
      lineWrapping: true,
      matchBrackets: true,
      styleActiveLine: true,
      theme: "default",
      indentUnit: 2,
      tabSize: 2,
      extraKeys: {
        "Ctrl-S": () => {
          if (typeof global.dmuxOnSavePluginsFragment === "function") {
            global.dmuxOnSavePluginsFragment();
          }
        },
        Tab: (cm) => cm.replaceSelection("  "),
      },
    });
    cmInstance.on("change", () => {
      dirty = true;
    });
    return cmInstance;
  };

  global.dmuxPluginsFragmentSetContent = function (text) {
    if (!cmInstance) return;
    cmInstance.setValue(text || "");
    dirty = false;
  };

  global.dmuxPluginsFragmentIsDirty = function () {
    return dirty;
  };

  global.dmuxPluginsFragmentMarkClean = function () {
    dirty = false;
  };

  global.dmuxPluginsFragmentGetValue = function () {
    return cmInstance ? cmInstance.getValue() : "";
  };

  global.dmuxPluginsFragmentRefresh = function () {
    if (cmInstance) cmInstance.refresh();
  };
})(typeof window !== "undefined" ? window : globalThis);
