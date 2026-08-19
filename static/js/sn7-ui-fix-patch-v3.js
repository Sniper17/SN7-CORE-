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


  /* SN7_BUTTON_FEEDBACK_V1 */
  function installButtonFeedback() {
    var id = "sn7-button-feedback-v1-style";

    if (!document.getElementById(id)) {
      var style = document.createElement("style");
      style.id = id;
      style.textContent =
        ".sn7-click-feedback{position:relative!important;overflow:hidden!important;-webkit-tap-highlight-color:transparent!important;isolation:isolate;}" +
        ".sn7-click-feedback::after{content:\"\";position:absolute;inset:0;border-radius:inherit;pointer-events:none;z-index:10;opacity:0;background:radial-gradient(circle at var(--sn7-fx-x,50%) var(--sn7-fx-y,50%),rgba(255,255,255,.15),rgba(255,255,255,0) 58%);transform:scale(.96);transition:opacity .16s ease,transform .16s ease;}" +
        ".sn7-click-feedback.sn7-clicking::after{opacity:1;transform:scale(1);}" +
        "@media(prefers-reduced-motion:reduce){.sn7-click-feedback::after{transition:none;}}";
      (document.head || document.documentElement).appendChild(style);
    }

    if (window.__SN7_BUTTON_FEEDBACK_V1) return;
    window.__SN7_BUTTON_FEEDBACK_V1 = true;

    function getTarget(el) {
      if (!el || !el.closest) return null;
      var target = el.closest("button,a.btn,.btn,.link-btn,.sn7-subtle,.sn7-danger,.sn7-edit-response,.sn7-command-row");
      if (!target || target.disabled) return null;
      return target;
    }

    document.addEventListener("pointerdown", function(event) {
      var target = getTarget(event.target);
      if (!target) return;

      var rect = target.getBoundingClientRect();
      if (!rect.width || !rect.height) return;

      var x = Math.max(0, Math.min(100, ((event.clientX - rect.left) / rect.width) * 100));
      var y = Math.max(0, Math.min(100, ((event.clientY - rect.top) / rect.height) * 100));

      target.style.setProperty("--sn7-fx-x", x + "%");
      target.style.setProperty("--sn7-fx-y", y + "%");
      target.classList.add("sn7-click-feedback");
      target.classList.remove("sn7-clicking");
      void target.offsetWidth;
      target.classList.add("sn7-clicking");

      setTimeout(function() {
        target.classList.remove("sn7-clicking");
      }, 180);
    }, {passive:true});
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


  /* SN7_PUBLIC_DEFAULTS_V1 */
  (function installPublicDefaults() {
    if (window.__SN7_PUBLIC_DEFAULTS_V1) return;
    window.__SN7_PUBLIC_DEFAULTS_V1 = true;

    var originalFetch = window.fetch.bind(window);

    var DEFAULT_SETTINGS = {
      broadcaster_user_id: null,
      username: "",
      currency_name: "Placos",
      currency_command: "!points",
      currency_emoji: "",
      points_response: "$(user), você tem $(points) $(currency).$(emoji_text)$(rank_text)",
      rank_title: "Ranking",
      rank_limit: 5,
      duel_win_points: 10,
      duel_loss_points: 3
    };

    var definitions = [
      ["points", "!points", "Consulta seu saldo de pontos.", "public", DEFAULT_SETTINGS.points_response],
      ["ranking", "!ranking", "Mostra o ranking do canal.", "public", "$(ranking)"],
      ["duel", "!duelo", "Inicia um duelo contra outro usuário.", "public", "$(duel_result)"],
      ["cmds", "!cmds", "Lista os comandos personalizados da live.", "public", "$(commands)"],
      ["addcmd", "!addcmd", "Cria ou atualiza um comando personalizado.", "mod", "✅ $(command) configurado."],
      ["addpoint", "!addpoint", "Adiciona pontos a um usuário.", "mod", "🪙 $(target) recebeu +$(amount) $(currency). Saldo: $(new_points) $(currency)."],
      ["settpoint", "!setpoint", "Define o saldo de um usuário.", "mod", "🪙 Saldo de $(target): $(new_points) $(currency)."],
      ["delcmd", "!delcmd", "Remove um comando personalizado.", "mod", "🗑️ $(command) removido."]
    ];

    var SYSTEM_COMMANDS = definitions.map(function (item) {
      return {
        id: null,
        broadcaster_user_id: null,
        command_key: item[0],
        command: item[1],
        description: item[2],
        response: item[4],
        enabled: true,
        category: item[3],
        is_system: true,
        aliases: []
      };
    });

    window.fetch = function (input, init) {
      var url = typeof input === "string" ? input : ((input && input.url) || "");
      var method = String((init && init.method) || (input && input.method) || "GET").toUpperCase();
      var path = "";

      try {
        path = new URL(url, window.location.origin).pathname;
      } catch (_) {
        path = url;
      }

      if (method === "GET" && /\/api\/settings\/null$/.test(path)) {
        return Promise.resolve(new Response(
          JSON.stringify({ ok: true, settings: DEFAULT_SETTINGS, demo: true }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        ));
      }

      if (method === "GET" && /\/api\/commands\/null$/.test(path)) {
        return Promise.resolve(new Response(
          JSON.stringify({ ok: true, commands: SYSTEM_COMMANDS, demo: true }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        ));
      }

      return originalFetch(input, init);
    };
  })();

  function removeWelcomeConnect() {
    var button = document.querySelector(".sn7-connect-btn");
    if (button) button.remove();

    var note = document.querySelector(".sn7-future-note");
    if (note) note.remove();
  }

  function init() {
    installStyles();
    installButtonFeedback();
    removeWelcomeConnect();
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
