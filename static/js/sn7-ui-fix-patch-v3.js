/* SN7 Core UI patch v3
 * Goal:
 * 1) Restore the last tab without a visible F5 flash.
 * 2) Make mobile tap/click feedback extremely subtle.
 *
 * IMPORTANT:
 * For the zero-flash restore to work, load this file in <head>,
 * before the dashboard body is rendered:
 * <script src="/static/js/sn7-ui-fix-patch-v3.js"></script>
 */
(function () {
  "use strict";

  var TAB_KEY = "sn7-core-active-tab";
  var STYLE_ID = "sn7-ui-patch-v3-style";
  var ROOT_CLASS = "sn7-v3-boot";
  var revealed = false;

  // This runs immediately when the script is loaded.
  // The matching CSS hides the page until the saved tab is restored.
  try {
    document.documentElement.classList.add(ROOT_CLASS);
  } catch (_) {}

  function installStyles() {
    if (document.getElementById(STYLE_ID)) return;

    var style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent =
      "html." + ROOT_CLASS + " body{visibility:hidden!important;}" +
      "html." + ROOT_CLASS + " body *{-webkit-tap-highlight-color:transparent!important;}" +
      ".sn7-command-row:active{background:rgba(255,255,255,.012)!important;transform:none!important;}" +
      ".sn7-command-row:hover{transform:none!important;}" +
      "@media (hover:none) and (pointer:coarse){" +
        ".sn7-command-row:hover{background:inherit!important;transform:none!important;}" +
      "}";
    (document.head || document.documentElement).appendChild(style);
  }

  function readTab() {
    try {
      var value = localStorage.getItem(TAB_KEY);
      return value && value !== "overview" ? value : null;
    } catch (_) {
      return null;
    }
  }

  function saveTab(tab) {
    if (!tab) return;
    try {
      localStorage.setItem(TAB_KEY, tab);
    } catch (_) {}
  }

  function restoreTab() {
    var saved = readTab();
    if (!saved) return;

    var button = document.querySelector(
      'nav button[data-tab="' + CSS.escape(saved) + '"]'
    );
    if (!button) return;

    // Use the dashboard's existing tab logic when available.
    button.click();
  }

  function reveal() {
    if (revealed) return;
    revealed = true;
    document.documentElement.classList.remove(ROOT_CLASS);
  }

  function bindTabPersistence() {
    document.querySelectorAll("nav button[data-tab]").forEach(function (button) {
      button.addEventListener("click", function () {
        saveTab(button.dataset.tab);
      }, { passive: true });
    });
  }

  function init() {
    installStyles();
    bindTabPersistence();

    // Let the existing dashboard JS finish registering its handlers,
    // then restore the saved tab before revealing the page.
    restoreTab();

    requestAnimationFrame(function () {
      reveal();
    });
  }

  // Install the critical CSS before body parsing/paint.
  installStyles();

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
