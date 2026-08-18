const $ = id => document.getElementById(id);

function openTab(tab) {
  const button = document.querySelector(`nav button[data-tab="${tab}"]`);
  if (button) button.click();
}

document.querySelectorAll("nav button").forEach(button => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(x => x.classList.remove("active"));
    document.querySelectorAll("nav button").forEach(x => x.classList.remove("active"));

    const target = $(button.dataset.tab);
    if (target) target.classList.add("active");
    button.classList.add("active");

    if ($("title")) $("title").textContent = button.textContent.trim();
    window.scrollTo({ top: 0, behavior: "smooth" });
  });
});

function updatePreview(s) {
  if ($("statCurrency")) $("statCurrency").textContent = s.currency_name;
  if ($("statCommand")) $("statCommand").textContent = s.currency_command;
  if ($("statEmoji")) $("statEmoji").textContent = s.currency_emoji;
  if ($("previewCurrency")) $("previewCurrency").textContent = s.currency_name;
  if ($("previewCommand")) $("previewCommand").textContent = s.currency_command;
  if ($("previewEmoji")) $("previewEmoji").textContent = s.currency_emoji;
}

function setMessage(text, ok = false) {
  const msg = $("settingsMsg");
  if (!msg) return;
  msg.textContent = text;
  msg.classList.toggle("success", ok);
  msg.classList.toggle("error", !ok);
}

/* Cria o catálogo novo sem precisar alterar o HTML do dashboard. */
function injectCommandStyles() {
  if ($("sn7-command-styles")) return;

  const style = document.createElement("style");
  style.id = "sn7-command-styles";
  style.textContent = `
    .commands-summary{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:0 0 14px}
    .command-summary-card{background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:14px 16px}
    .command-summary-card strong{display:block;font-size:24px}
    .command-summary-card span{display:block;color:var(--muted);font-size:11px;margin-top:3px}
    .command-catalog{max-width:950px;padding:0;overflow:hidden}
    .command-category{padding:18px 20px}
    .command-category+.command-category{border-top:1px solid var(--border)}
    .command-category-head{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:12px}
    .command-category-head h3{margin:0 0 3px}
    .command-category-head p{font-size:12px}
    .category-count{min-width:30px;height:30px;border-radius:9px;background:#1a1f2a;display:grid;place-items:center;font-size:12px;color:#c8cfdb}
    .system-command{display:grid;grid-template-columns:190px 1fr;gap:12px;align-items:center;padding:12px 10px;border-top:1px solid var(--border)}
    .system-command code{color:#fff;font-size:12px;overflow-wrap:anywhere}
    .system-command span{color:#9fa8b8;font-size:12px}
    .command-manager{margin-top:14px}
    .command-manager .command-form{margin-top:14px}
    .commands-list{border-top:1px solid var(--border)}
    .commands-list .command{display:grid;grid-template-columns:190px 1fr 70px;gap:8px;align-items:center;padding:13px 10px;border-bottom:1px solid var(--border);font-size:12px}
    @media(max-width:700px){
      .commands-summary{grid-template-columns:1fr 1fr 1fr}
      .command-category{padding:16px}
      .system-command{grid-template-columns:1fr;gap:4px}
      .commands-list .command{grid-template-columns:1fr auto}
      .commands-list .command-response{grid-column:1}
      .commands-list .delete-btn{grid-column:2;grid-row:1/3}
      .command-manager .command-form{grid-template-columns:1fr}
    }
  `;
  document.head.appendChild(style);
}

function buildCommandCatalog() {
  const section = $("commands");
  if (!section || $("publicCommandsList")) return;

  section.innerHTML = `
    <div class="section-head">
      <div>
        <h2>Comandos</h2>
        <p>Veja todos os comandos disponíveis nesta live e gerencie as respostas personalizadas.</p>
      </div>
    </div>

    <div class="commands-summary">
      <div class="command-summary-card"><strong id="systemPublicCount">0</strong><span>Públicos</span></div>
      <div class="command-summary-card"><strong id="systemModCount">0</strong><span>ADM / MOD</span></div>
      <div class="command-summary-card"><strong id="customCommandCount">0</strong><span>Personalizados</span></div>
    </div>

    <div class="panel command-catalog">
      <div class="command-category">
        <div class="command-category-head">
          <div><h3>🌐 Públicos</h3><p>Qualquer pessoa no chat pode usar.</p></div>
          <span class="category-count" id="publicCommandCount">0</span>
        </div>
        <div id="publicCommandsList" class="system-commands-list"></div>
      </div>

      <div class="command-category">
        <div class="command-category-head">
          <div><h3>🛡️ ADM / MOD</h3><p>Apenas streamer ou moderador pode usar.</p></div>
          <span class="category-count" id="modCommandCount">0</span>
        </div>
        <div id="modCommandsList" class="system-commands-list"></div>
      </div>

      <div class="command-category">
        <div class="command-category-head">
          <div><h3>✨ Personalizados</h3><p>Comandos criados para esta live. O uso é público.</p></div>
          <span class="category-count" id="customCategoryCount">0</span>
        </div>
        <div id="commandsList" class="commands-list"></div>
      </div>
    </div>

    <div class="panel form-panel command-manager">
      <div class="panel-title">
        <div>
          <h3>Adicionar comando personalizado</h3>
          <p>O comando ficará disponível somente nesta live.</p>
        </div>
      </div>

      <div class="command-form">
        <input id="cmd" placeholder="!discord" autocomplete="off" autocapitalize="none" spellcheck="false">
        <input id="response" placeholder="Resposta que o bot vai enviar" autocomplete="off">
        <button type="button" class="btn" onclick="saveCommand()">Adicionar</button>
      </div>
    </div>
  `;

  injectCommandStyles();
}

function renderSystemCommands(settings) {
  const publicList = $("publicCommandsList");
  const modList = $("modCommandsList");
  if (!publicList || !modList) return;

  const currency = String(settings?.currency_command || "!placos").toLowerCase();

  const publicCommands = [
    [currency, "Consulta seu saldo de pontos."],
    ["!saldo", "Consulta seu saldo de pontos."],
    ["!balance", "Alias de !saldo."],
    ["!ranking", "Mostra o ranking do canal."],
    ["!rank", "Alias de !ranking."],
    ["!top", "Alias de !ranking."],
    ["!duelo @usuário", "Inicia um duelo contra outro usuário."],
    ["!duel @usuário", "Alias de !duelo."],
    ["!cmds", "Lista os comandos personalizados da live."],
    ["!comandos", "Alias de !cmds."]
  ];

  const modCommands = [
    ["!addplacos @usuário quantidade", "Adiciona pontos a um usuário."],
    ["!addpontos @usuário quantidade", "Alias de !addplacos."],
    ["!setplacos @usuário quantidade", "Define o saldo de um usuário."],
    ["!setpontos @usuário quantidade", "Alias de !setplacos."],
    ["!addcmd !comando resposta", "Cria ou atualiza um comando personalizado."],
    ["!addcomando !comando resposta", "Alias de !addcmd."],
    ["!delcmd !comando", "Remove um comando personalizado."],
    ["!delcomando !comando", "Alias de !delcmd."]
  ];

  const render = rows => rows.map(([cmd, desc]) =>
    `<div class="system-command"><code>${esc(cmd)}</code><span>${esc(desc)}</span></div>`
  ).join("");

  publicList.innerHTML = render(publicCommands);
  modList.innerHTML = render(modCommands);

  if ($("systemPublicCount")) $("systemPublicCount").textContent = publicCommands.length;
  if ($("systemModCount")) $("systemModCount").textContent = modCommands.length;
  if ($("publicCommandCount")) $("publicCommandCount").textContent = publicCommands.length;
  if ($("modCommandCount")) $("modCommandCount").textContent = modCommands.length;
}

async function loadSettings() {
  buildCommandCatalog();

  try {
    const r = await fetch(`/api/settings/${BROADCASTER_ID}`, { cache: "no-store" });
    const d = await r.json();

    if (!d.ok) throw new Error(d.error || "Falha ao carregar");

    const s = d.settings;

    [
      "currency_name", "currency_command", "currency_emoji",
      "rank_title", "rank_limit", "duel_win_points", "duel_loss_points"
    ].forEach(k => {
      if ($(k)) $(k).value = s[k] ?? "";
    });

    updatePreview(s);
    renderSystemCommands(s);

    if (d.demo) {
      setMessage("Modo demonstração. O banco será conectado depois.", true);
    }

    await loadCommands();
  } catch (e) {
    const fallback = {
      currency_name: "Placos",
      currency_command: "!placos",
      currency_emoji: "🪙"
    };

    updatePreview(fallback);
    renderSystemCommands(fallback);
    await loadCommands();
    setMessage("Interface carregada em modo demonstração.", true);
  }
}

async function saveSettings() {
  const data = {};

  [
    "currency_name", "currency_command", "currency_emoji",
    "rank_title", "rank_limit", "duel_win_points", "duel_loss_points"
  ].forEach(k => {
    if ($(k)) data[k] = $(k).value;
  });

  try {
    const r = await fetch(`/api/settings/${BROADCASTER_ID}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data)
    });

    const d = await r.json();

    if (d.ok) {
      updatePreview(d.settings);
      renderSystemCommands(d.settings);
      setMessage("✓ Alterações salvas.", true);
    } else {
      setMessage("⚠ " + (d.error || "Não foi possível salvar."));
    }
  } catch (e) {
    setMessage("⚠ Não foi possível salvar agora.");
  }
}

async function loadCommands() {
  buildCommandCatalog();

  try {
    const r = await fetch(`/api/commands/${BROADCASTER_ID}`, { cache: "no-store" });
    const d = await r.json();
    const list = d.commands || [];

    if ($("commandCount")) $("commandCount").textContent = list.length;
    if ($("customCommandCount")) $("customCommandCount").textContent = list.length;
    if ($("customCategoryCount")) $("customCategoryCount").textContent = list.length;

    if (!$("commandsList")) return;

    $("commandsList").innerHTML = list.length
      ? list.map(c => `
        <div class="command">
          <div><b>${esc(c.command)}</b></div>
          <div class="command-response">${esc(c.response)}</div>
          <button type="button" class="delete-btn"
            onclick="delCmd('${encodeURIComponent(c.command)}')">Excluir</button>
        </div>`
      ).join("")
      : `<div class="empty-panel"><p>Nenhum comando personalizado ainda.</p></div>`;

  } catch (e) {
    if ($("commandCount")) $("commandCount").textContent = "0";
    if ($("customCommandCount")) $("customCommandCount").textContent = "0";
    if ($("customCategoryCount")) $("customCategoryCount").textContent = "0";

    if ($("commandsList")) {
      $("commandsList").innerHTML =
        `<div class="empty-panel"><p>Modo demonstração — banco ainda não conectado.</p></div>`;
    }
  }
}

async function saveCommand() {
  const command = $("cmd")?.value.trim();
  const response = $("response")?.value.trim();

  if (!command || !response) return;

  try {
    const r = await fetch(`/api/commands/${BROADCASTER_ID}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ command, response })
    });

    const d = await r.json();

    if (!d.ok) {
      setMessage("⚠ " + (d.error || "Não foi possível salvar o comando."));
      return;
    }

    $("cmd").value = "";
    $("response").value = "";
    await loadCommands();

  } catch (e) {
    setMessage("⚠ Não foi possível salvar o comando agora.");
  }
}

async function delCmd(c) {
  try {
    await fetch(`/api/commands/${BROADCASTER_ID}?command=${c}`, {
      method: "DELETE"
    });
    await loadCommands();
  } catch (e) {}
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;"
  }[c]));
}

window.addEventListener("load", () => {
  setTimeout(() => {
    const active = document.activeElement;
    if (active && ["INPUT", "TEXTAREA"].includes(active.tagName)) active.blur();
  }, 80);

  loadSettings();
});
