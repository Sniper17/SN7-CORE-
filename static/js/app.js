const $ = (id) => document.getElementById(id);
let commandCache = [];

function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

async function apiJson(url, options = {}) {
  const response = await fetch(url, { cache: "no-store", ...options });
  let data;
  try {
    data = await response.json();
  } catch (_) {
    throw new Error(`Resposta inválida do servidor (HTTP ${response.status}).`);
  }
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || `Falha HTTP ${response.status}.`);
  }
  return data;
}

function openTab(tab) {
  const button = document.querySelector(`nav button[data-tab="${tab}"]`);
  if (button) button.click();
}

document.querySelectorAll("nav button").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
    document.querySelectorAll("nav button").forEach((x) => x.classList.remove("active"));
    $(button.dataset.tab)?.classList.add("active");
    button.classList.add("active");
    if ($("title")) $("title").textContent = button.textContent.trim();
    window.scrollTo({ top: 0, behavior: "smooth" });
  });
});

function setMessage(text, ok = false) {
  const msg = $("settingsMsg");
  if (!msg) return;
  msg.textContent = text;
  msg.classList.toggle("success", ok);
  msg.classList.toggle("error", !ok);
}

function updatePreview(settings) {
  if ($("statCurrency")) $("statCurrency").textContent = settings.currency_name ?? "";
  if ($("statCommand")) $("statCommand").textContent = settings.currency_command ?? "";
  if ($("statEmoji")) $("statEmoji").textContent = settings.currency_emoji ?? "";
  if ($("previewCurrency")) $("previewCurrency").textContent = settings.currency_name ?? "";
  if ($("previewCommand")) $("previewCommand").textContent = settings.currency_command ?? "";
  if ($("previewEmoji")) $("previewEmoji").textContent = settings.currency_emoji ?? "";
}

function injectCommandStyles() {
  if ($("sn7-command-v22-style")) return;
  const style = document.createElement("style");
  style.id = "sn7-command-v22-style";
  style.textContent = `
    .sn7-command-panel{max-width:980px;padding:0;overflow:hidden}
    .sn7-command-category{padding:18px 20px}
    .sn7-command-category+.sn7-command-category{border-top:1px solid var(--border)}
    .sn7-command-head{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:10px}
    .sn7-command-head h3{margin:0}
    .sn7-command-status{font-size:12px;color:var(--muted);margin:0 0 10px}
    .sn7-command-row{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:12px 8px;border-top:1px solid var(--border);cursor:pointer}
    .sn7-command-row:hover{background:rgba(255,255,255,.025)}
    .sn7-command-row.disabled{opacity:.48}
    .sn7-command-row code{color:#fff;font-size:12px}
    .sn7-command-row small{display:block;color:var(--muted);margin-top:4px;font-size:11px}
    .sn7-aliases{font-size:11px;color:var(--muted);margin-top:5px}
    .sn7-subtle{background:transparent;border:1px solid var(--border);color:var(--muted);border-radius:8px;padding:8px 12px;font-size:12px;font-weight:600;cursor:pointer}
    .sn7-subtle:hover{color:#fff;border-color:#394253;background:#171c25}
    .sn7-empty{padding:12px 8px;color:var(--muted);font-size:12px}
    .sn7-modal{position:fixed;inset:0;background:rgba(0,0,0,.68);display:flex;align-items:center;justify-content:center;padding:16px;z-index:9999}
    .sn7-box{width:min(620px,100%);max-height:90vh;overflow:auto;background:#11151d;border:1px solid var(--border);border-radius:16px;padding:20px}
    .sn7-box h3{margin:0}
    .sn7-box label{display:block;margin-top:14px;font-size:12px}
    .sn7-box input,.sn7-box textarea{width:100%;margin-top:7px;box-sizing:border-box}
    .sn7-box textarea{min-height:120px;resize:vertical}
    .sn7-toggle{display:flex!important;align-items:center;gap:8px}
    .sn7-toggle input{width:auto;margin:0}
    .sn7-alias-row{display:flex;gap:8px;margin-top:7px}
    .sn7-alias-row input{margin:0}
    .sn7-danger{border:1px solid #63383b;background:transparent;color:#ff9c9c;border-radius:8px;padding:8px 11px;cursor:pointer}
    .sn7-actions{display:flex;justify-content:space-between;gap:10px;margin-top:20px}
    .sn7-actions>div{display:flex;gap:8px}
    .sn7-help{font-size:11px;color:var(--muted);margin-top:6px}
    @media(max-width:700px){
      .sn7-command-category{padding:16px}
      .sn7-command-row{align-items:flex-start}
      .sn7-actions{flex-direction:column}
      .sn7-actions>div{justify-content:flex-end}
    }
  `;
  document.head.appendChild(style);
}

function buildCommandCatalog() {
  const section = $("commands");
  if (!section) return;
  section.innerHTML = `
    <div class="section-head">
      <div>
        <h2>Comandos</h2>
        <p>Comandos da live. Alterações são salvas diretamente no banco.</p>
      </div>
    </div>
    <div class="panel sn7-command-panel">
      <div class="sn7-command-category">
        <div class="sn7-command-head"><h3>🌐 Públicos</h3></div>
        <div id="publicCommandsList"></div>
      </div>
      <div class="sn7-command-category">
        <div class="sn7-command-head"><h3>🛡️ ADM / MOD</h3></div>
        <div id="modCommandsList"></div>
      </div>
      <div class="sn7-command-category">
        <div class="sn7-command-head">
          <h3>✨ Personalizados</h3>
          <button class="sn7-subtle" type="button" onclick="newCommand()">＋ Novo comando</button>
        </div>
        <p id="commandPanelStatus" class="sn7-command-status"></p>
        <div id="customCommandsList"></div>
      </div>
    </div>`;
  injectCommandStyles();
}

function renderCommands() {
  const groups = {
    public: $("publicCommandsList"),
    mod: $("modCommandsList"),
    custom: $("customCommandsList"),
  };
  for (const [category, element] of Object.entries(groups)) {
    if (!element) continue;
    const rows = commandCache.filter((command) => command.category === category);
    if (!rows.length) {
      element.innerHTML = `<div class="sn7-empty">Nenhum comando nesta categoria.</div>`;
      continue;
    }
    element.innerHTML = rows.map((command) => `
      <div class="sn7-command-row ${command.enabled ? "" : "disabled"}"
           onclick="openCommand('${encodeURIComponent(command.command_key)}')">
        <div>
          <code>${esc(command.command)}</code>
          <small>${esc(command.description)}</small>
          ${command.aliases?.length ? `<div class="sn7-aliases">Atalhos: ${command.aliases.map(esc).join(", ")}</div>` : ""}
        </div>
        <small>${command.enabled ? "🟢 Ativo" : "⚪ Desativado"}</small>
      </div>`).join("");
  }

  const customCount = commandCache.filter((x) => x.category === "custom" && x.enabled).length;
  if ($("commandCount")) $("commandCount").textContent = customCount;
  if ($("commandPanelStatus")) {
    $("commandPanelStatus").textContent =
      `${customCount} comando${customCount === 1 ? "" : "s"} personalizado${customCount === 1 ? "" : "s"} ativo${customCount === 1 ? "" : "s"}.`;
  }
}

async function loadCommands() {
  buildCommandCatalog();
  try {
    const data = await apiJson(`/api/commands/${BROADCASTER_ID}`);
    commandCache = Array.isArray(data.commands) ? data.commands : [];
    renderCommands();
  } catch (error) {
    commandCache = [];
    renderCommands();
    if ($("commandPanelStatus")) {
      $("commandPanelStatus").textContent = `⚠ ${error.message}`;
    }
  }
}

function openCommand(encodedKey) {
  const key = decodeURIComponent(encodedKey);
  const command = commandCache.find((item) => item.command_key === key);
  if (command) showCommand(command, false);
}

function newCommand() {
  showCommand({
    command_key: "",
    command: "",
    description: "Comando personalizado desta live.",
    response: "",
    enabled: true,
    aliases: [],
    is_system: false,
    category: "custom",
  }, true);
}

function showCommand(command, isNew = false) {
  document.querySelector(".sn7-modal")?.remove();
  const modal = document.createElement("div");
  modal.className = "sn7-modal";
  const aliases = (command.aliases || []).map((alias) => `
    <div class="sn7-alias-row">
      <input value="${esc(alias)}" readonly>
      <button class="sn7-danger" type="button" onclick="removeAlias('${encodeURIComponent(command.command_key)}','${encodeURIComponent(alias)}')">🗑</button>
    </div>`).join("");

  modal.innerHTML = `
    <div class="sn7-box">
      <h3>${isNew ? "✨ Novo comando" : esc(command.command)}</h3>
      <label>Comando principal
        <input id="v2cmd" value="${esc(command.command)}" maxlength="64" placeholder="!discord" autocapitalize="none" spellcheck="false">
      </label>
      <label>Descrição
        <input id="v2desc" value="${esc(command.description)}" maxlength="200">
      </label>
      <label>Mensagem / resposta
        <textarea id="v2resp" maxlength="500" placeholder="Resposta que o bot enviará">${esc(command.response)}</textarea>
      </label>
      <label class="sn7-toggle">
        <input id="v2enabled" type="checkbox" ${command.enabled ? "checked" : ""}>
        Comando ativo
      </label>
      <div class="sn7-help">Você pode usar variáveis como $(user), $(points), $(currency), $(rank) e outras específicas do comando.</div>
      ${!isNew ? `
        <label>Palavras de ativação
          ${aliases || `<div class="sn7-help">Nenhum alias cadastrado.</div>`}
          <div class="sn7-alias-row">
            <input id="v2alias" placeholder="!rank" autocapitalize="none" spellcheck="false">
            <button class="btn" type="button" onclick="addAlias('${encodeURIComponent(command.command_key)}')">Adicionar</button>
          </div>
        </label>` : ""}
      <div class="sn7-actions">
        <button class="${command.is_system ? "sn7-subtle" : "sn7-danger"}" type="button"
          onclick="deleteCommandV2('${encodeURIComponent(command.command_key)}',${command.is_system})">
          ${command.is_system ? (command.enabled ? "Desativar" : "Ativar") : "Excluir"}
        </button>
        <div>
          <button class="btn" type="button" onclick="saveCommandV2('${encodeURIComponent(command.command_key)}',${isNew})">Salvar</button>
          <button class="sn7-subtle" type="button" onclick="this.closest('.sn7-modal').remove()">Fechar</button>
        </div>
      </div>
    </div>`;
  document.body.appendChild(modal);
}

async function saveCommandV2(encodedKey, isNew) {
  const body = {
    command: $("v2cmd")?.value.trim(),
    description: $("v2desc")?.value.trim(),
    response: $("v2resp")?.value,
    enabled: $("v2enabled")?.checked,
  };
  try {
    const data = await apiJson(
      isNew
        ? `/api/commands/${BROADCASTER_ID}`
        : `/api/commands/${BROADCASTER_ID}/${encodedKey}`,
      {
        method: isNew ? "POST" : "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }
    );
    commandCache = data.commands || [];
    document.querySelector(".sn7-modal")?.remove();
    renderCommands();
  } catch (error) {
    alert(error.message);
  }
}

async function addAlias(encodedKey) {
  const alias = $("v2alias")?.value.trim();
  if (!alias) return;
  try {
    await apiJson(`/api/commands/${BROADCASTER_ID}/${encodedKey}/aliases`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ alias }),
    });
    document.querySelector(".sn7-modal")?.remove();
    await loadCommands();
    openCommand(encodedKey);
  } catch (error) {
    alert(error.message);
  }
}

async function removeAlias(encodedKey, encodedAlias) {
  try {
    await apiJson(`/api/commands/${BROADCASTER_ID}/${encodedKey}/aliases`, {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ alias: decodeURIComponent(encodedAlias) }),
    });
    document.querySelector(".sn7-modal")?.remove();
    await loadCommands();
    openCommand(encodedKey);
  } catch (error) {
    alert(error.message);
  }
}

async function deleteCommandV2(encodedKey, isSystem) {
  try {
    await apiJson(`/api/commands/${BROADCASTER_ID}/${encodedKey}`, {
      method: "DELETE",
    });
    document.querySelector(".sn7-modal")?.remove();
    await loadCommands();
  } catch (error) {
    alert(error.message);
  }
}

async function loadSettings() {
  try {
    const data = await apiJson(`/api/settings/${BROADCASTER_ID}`);
    const settings = data.settings;
    [
      "currency_name", "currency_command", "currency_emoji",
      "rank_title", "rank_limit", "duel_win_points", "duel_loss_points",
    ].forEach((key) => {
      if ($(key)) $(key).value = settings[key] ?? "";
    });
    updatePreview(settings);
    if (data.demo) setMessage("Modo demonstração: alterações não são persistidas.", false);
  } catch (error) {
    setMessage(`⚠ ${error.message}`, false);
  }
  await loadCommands();
}

async function saveSettings() {
  const data = {};
  [
    "currency_name", "currency_command", "currency_emoji",
    "rank_title", "rank_limit", "duel_win_points", "duel_loss_points",
  ].forEach((key) => {
    if ($(key)) data[key] = $(key).value;
  });

  try {
    const result = await apiJson(`/api/settings/${BROADCASTER_ID}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    updatePreview(result.settings);
    setMessage("✓ Alterações salvas.", true);
    await loadCommands();
  } catch (error) {
    setMessage(`⚠ ${error.message}`, false);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  injectCommandStyles();
  loadSettings();
});
