/* SN7 CORE - exemplos de comandos + modo demonstração deslogado
 * Patch manual V2. Não altera a lógica do backend de pontos.
 */
(function () {
  "use strict";

  const DEMO_KEY = "sn7-core-demo-commands-v2";
  const DEMO_SETTINGS_KEY = "sn7-core-demo-settings-v2";
  const originalFetch = window.fetch.bind(window);

  const DEFAULT_SETTINGS = {
    broadcaster_user_id: null,
    username: "",
    currency_name: "Pontos",
    currency_command: "!pontos",
    currency_emoji: "",
    points_response: "$(user), você tem $(points) $(currency).$(emoji_text)$(rank_text)",
    rank_title: "Ranking",
    rank_limit: 5,
    duel_win_points: 10,
    duel_loss_points: 3,
    point_rewards: { watch_points: 1, watch_interval_minutes: 10, sub_bonus: 500, kicks_bonus_per_kick: 1 }
  };

  const DEMO_COMMANDS = [
    { command_key:"points", command:"!pontos", description:"Consulta seu saldo de pontos.", response:"$(user), você tem $(points) $(currency).$(emoji_text)$(rank_text)", enabled:true, category:"public", is_system:true, aliases:[] },
    { command_key:"ranking", command:"!ranking", description:"Mostra o ranking do canal.", response:"$(ranking)", enabled:true, category:"public", is_system:true, aliases:[] },
    { command_key:"duel", command:"!aposta", description:"Inicia uma aposta contra outro usuário.", response:"$(duel_result)", enabled:true, category:"public", is_system:true, aliases:[] },
    { command_key:"cmds", command:"!cmds", description:"Lista os comandos personalizados da live.", response:"$(commands)", enabled:true, category:"public", is_system:true, aliases:[] },
    { command_key:"addcmd", command:"!addcmd", description:"Cria ou atualiza um comando personalizado.", response:"✅ $(command) configurado.", enabled:true, category:"mod", is_system:true, aliases:[] },
    { command_key:"addpoint", command:"!addpoint", description:"Adiciona pontos a um usuário.", response:"🪙 $(target) recebeu +$(amount) $(currency). Saldo: $(new_points) $(currency).", enabled:true, category:"mod", is_system:true, aliases:[] },
    { command_key:"settpoint", command:"!setpoint", description:"Define o saldo de um usuário.", response:"🪙 Saldo de $(target): $(new_points) $(currency).", enabled:true, category:"mod", is_system:true, aliases:[] },
    { command_key:"delcmd", command:"!delcmd", description:"Remove um comando personalizado.", response:"🗑️ $(command) removido.", enabled:true, category:"mod", is_system:true, aliases:[] }
  ];

  const EXAMPLES = {
    "!pontos": ["!pontos @user", "@user tem 19.283 Pontos e sua posição no ranking é #4."],
    "!ranking": ["!ranking", "🏆 1. @user 19.283 • 2. Player 10.000"],
    "!aposta": ["!aposta @user", "⚔️ @user venceu a aposta contra @oponente!"],
    "!cmds": ["!cmds", "📜 Comandos: !meta !duelo !ranking"],
    "!addcmd": ["!addcmd !discord Entre no Discord", "✅ !discord configurado."],
    "!addpoint": ["!addpoint @user 1000", "@user tinha 20 Pontos e agora tem 1.020 Pontos."],
    "!setpoint": ["!setpoint @user 1000", "@user tinha 5.000 Pontos e agora tem somente 1.000 Pontos."],
    "!settpoint": ["!settpoint @user 1000", "@user tinha 5.000 Pontos e agora tem somente 1.000 Pontos."],
    "!delcmd": ["!delcmd !discord", "🗑️ !discord removido."]
  };

  function esc(value) {
    return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#039;");
  }

  function readJson(key, fallback) {
    try {
      const raw = localStorage.getItem(key);
      if (!raw) return fallback;
      const value = JSON.parse(raw);
      return value ?? fallback;
    } catch (_) { return fallback; }
  }

  function writeJson(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch (_) {}
  }

  function demoSettings() { return { ...DEFAULT_SETTINGS, ...readJson(DEMO_SETTINGS_KEY, {}) }; }

  function demoCommands() {
    const saved = readJson(DEMO_KEY, null);
    return Array.isArray(saved) ? saved : DEMO_COMMANDS.map((x) => ({ ...x }));
  }

  function jsonResponse(payload, status = 200) {
    return new Response(JSON.stringify(payload), { status, headers: { "Content-Type": "application/json" } });
  }

  // O template pode expor o ID como null, undefined, vazio ou a string "null".
  // Todos esses estados significam que a página está em modo deslogado.
  function isLoggedOut() {
    if (typeof BROADCASTER_ID === "undefined") return true;
    return BROADCASTER_ID == null || BROADCASTER_ID === "" || String(BROADCASTER_ID).toLowerCase() === "null";
  }

  function installDemoFetch() {
    if (!isLoggedOut() || window.__sn7DemoFetchInstalled) return;
    window.__sn7DemoFetchInstalled = true;

    window.fetch = async function (input, options = {}) {
      const url = typeof input === "string" ? input : (input && input.url) || "";
      const method = String(options.method || (input && input.method) || "GET").toUpperCase();
      const path = (() => {
        try { return new URL(url, window.location.origin).pathname; }
        catch (_) { return url; }
      })();

      if (path === "/api/settings/null") {
        if (method === "GET") return jsonResponse({ ok:true, settings:demoSettings(), demo:true });
        if (method === "PUT") {
          try {
            const body = JSON.parse(options.body || "{}");
            const next = { ...demoSettings(), ...body };
            writeJson(DEMO_SETTINGS_KEY, next);
            return jsonResponse({ ok:true, settings:next, demo:true });
          } catch (_) {
            return jsonResponse({ ok:false, error:"Dados inválidos." }, 400);
          }
        }
      }

      if (path === "/api/commands/null") {
        let commands = demoCommands();
        if (method === "GET") return jsonResponse({ ok:true, commands, demo:true });
        if (method === "POST") {
          try {
            const body = JSON.parse(options.body || "{}");
            const command = String(body.command || "").trim().toLowerCase();
            const item = {
              command_key:"custom:" + command,
              command,
              description:String(body.description || "Comando personalizado desta live."),
              response:String(body.response || ""),
              enabled:body.enabled !== false,
              category:"custom",
              is_system:false,
              aliases:Array.isArray(body.aliases) ? body.aliases : []
            };
            commands = commands.filter((x) => x.command_key !== item.command_key);
            commands.push(item);
            writeJson(DEMO_KEY, commands);
            return jsonResponse({ ok:true, commands, demo:true });
          } catch (_) {
            return jsonResponse({ ok:false, error:"Dados inválidos." }, 400);
          }
        }
      }

      const commandMatch = path.match(/^\/api\/commands\/null\/([^/]+)(?:\/aliases)?$/);
      if (commandMatch) {
        let commands = demoCommands();
        const key = decodeURIComponent(commandMatch[1]);
        const item = commands.find((x) => x.command_key === key);

        if (path.endsWith("/aliases") && method === "POST" && item) {
          const body = JSON.parse(options.body || "{}");
          const alias = String(body.alias || "").trim().toLowerCase();
          item.aliases = [...new Set([...(item.aliases || []), alias])];
          writeJson(DEMO_KEY, commands);
          return jsonResponse({ ok:true, commands, demo:true });
        }

        if (path.endsWith("/aliases") && method === "DELETE" && item) {
          const body = JSON.parse(options.body || "{}");
          const alias = String(body.alias || "").trim().toLowerCase();
          item.aliases = (item.aliases || []).filter((x) => x !== alias);
          writeJson(DEMO_KEY, commands);
          return jsonResponse({ ok:true, commands, demo:true });
        }

        if (item && method === "PATCH") {
          const body = JSON.parse(options.body || "{}");
          if (body.command != null) item.command = body.command;
          if (body.description != null) item.description = body.description;
          if (body.response != null) item.response = body.response;
          if (body.enabled != null) item.enabled = body.enabled;
          writeJson(DEMO_KEY, commands);
          return jsonResponse({ ok:true, commands, demo:true });
        }

        if (item && method === "DELETE") {
          if (item.is_system) item.enabled = !item.enabled;
          else commands = commands.filter((x) => x.command_key !== key);
          writeJson(DEMO_KEY, commands);
          return jsonResponse({ ok:true, commands, demo:true });
        }
      }

      return originalFetch(input, options);
    };
  }

  function installStyles() {
    if (document.getElementById("sn7-command-example-style")) return;
    const style = document.createElement("style");
    style.id = "sn7-command-example-style";
    style.textContent = `
      .sn7-command-example-box{margin-top:8px;padding:9px 11px;border:1px solid var(--border);border-radius:10px;background:#0d1016;color:var(--muted);font-size:11px;line-height:1.55}
      .sn7-command-example-box strong{color:#8f98a8;font-weight:700}
      .sn7-command-example-box code{color:#aeb6c5;font-size:11px}
      .sn7-command-example-box .sn7-example-result{color:#c2c8d2}
      .sn7-command-example-hint{display:block;margin-top:3px;color:#70798a;font-size:10px}
    `;
    document.head.appendChild(style);
  }

  function findCommandInput() { return document.getElementById("v2cmd"); }
  function findResponseBox() { return document.getElementById("v2resp"); }

  function updateExample() {
    const commandInput = findCommandInput();
    const responseBox = findResponseBox();
    if (!commandInput || !responseBox) return;

    const command = String(commandInput.value || "").trim().toLowerCase();
    const pair = EXAMPLES[command];
    let node = document.getElementById("sn7CommandExampleBox");

    if (!node) {
      node = document.createElement("div");
      node.id = "sn7CommandExampleBox";
      node.className = "sn7-command-example-box";
      responseBox.insertAdjacentElement("afterend", node);
    }

    if (!pair) {
      node.innerHTML = "<strong>Exemplo:</strong><span class=\"sn7-example-result\"> digite o comando acima para ver um exemplo.</span>";
      return;
    }

    node.innerHTML = `<strong>Exemplo:</strong> <code>${esc(pair[0])}</code><br><span class="sn7-example-result">↳ ${esc(pair[1])}</span><span class="sn7-command-example-hint">O exemplo é apenas uma prévia e não altera a resposta salva.</span>`;
    commandInput.placeholder = pair[0];
    responseBox.placeholder = pair[1];
  }

  function observeCommandModal() {
    const observer = new MutationObserver((mutations) => {
      const opened = mutations.some((mutation) =>
        Array.from(mutation.addedNodes || []).some((node) =>
          node.nodeType === 1 &&
          (node.matches?.(".sn7-command-modal") || node.querySelector?.(".sn7-command-modal"))
        )
      );
      if (opened) setTimeout(updateExample, 0);
    });

    observer.observe(document.body, { childList:true, subtree:true });

    document.addEventListener("input", (event) => {
      if (event.target?.id === "v2cmd") updateExample();
    });
  }

  function markDemo() {
    if (!isLoggedOut()) return;
    const msg = document.getElementById("settingsMsg");
    if (msg) msg.textContent = "Modo demonstração: você pode editar e visualizar exemplos. Para salvar de verdade, entre com a Kick.";
  }

  // IMPORTANTE: o app.js registra seu DOMContentLoaded antes deste arquivo.
  // Se o fetch demo só for instalado no DOMContentLoaded, loadSettings/loadCommands
  // do app.js executam primeiro e recebem HTTP 404 em /api/*/null.
  // Por isso o interceptor deve ser instalado imediatamente.
  installDemoFetch();
  installStyles();

  function init() {
    observeCommandModal();
    markDemo();
    updateExample();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once:true });
  } else {
    init();
  }
})();
