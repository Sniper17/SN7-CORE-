const $ = (id) => document.getElementById(id);
let commandCache = [];
let draftAliases = [];

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


const SN7_ACTIVE_TAB_KEY = "sn7-core-active-tab";
let sn7NavigationReady = false;

function getSavedTab() {
  try {
    const tab = localStorage.getItem(SN7_ACTIVE_TAB_KEY);
    if (!tab) return null;
    const button = document.querySelector(
      'nav button[data-tab="' + CSS.escape(tab) + '"]'
    );
    return button ? tab : null;
  } catch (_) {
    return null;
  }
}

function saveActiveTab(tab) {
  if (!tab) return;
  try {
    localStorage.setItem(SN7_ACTIVE_TAB_KEY, tab);
  } catch (_) {}
}

function activateTab(tab, options = {}) {
  if (!tab) return false;

  const button = document.querySelector(
    'nav button[data-tab="' + CSS.escape(tab) + '"]'
  );
  if (!button) return false;

  document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
  document.querySelectorAll("nav button").forEach((x) => x.classList.remove("active"));

  const section = $(button.dataset.tab);
  if (section) section.classList.add("active");

  button.classList.add("active");

  if (button.dataset.tab === "ranking" && typeof loadRanking === "function") {
    loadRanking();
  }

  const title = $("title");
  if (title) title.textContent = button.textContent.trim();

  if (options.persist !== false) saveActiveTab(button.dataset.tab);

  if (options.scroll !== false) {
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  return true;
}

function openTab(tab) {
  return activateTab(tab, { persist: true, scroll: true });
}

function setupTabPersistence() {
  if (sn7NavigationReady) return;
  sn7NavigationReady = true;

  document.querySelectorAll("nav button[data-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      activateTab(button.dataset.tab, { persist: true, scroll: true });
    });
  });
}

function restoreSavedTab() {
  // OAuth/login tem prioridade sobre a aba salva.
  if (window.SN7_OPEN_PROFILE || window.SN7_NAV_FROM_URL) return false;

  const saved = getSavedTab();
  if (!saved) return false;

  return activateTab(saved, { persist: true, scroll: false });
}


function setMessage(text, ok = false) {
  const msg = $("settingsMsg");
  if (!msg) return;
  msg.textContent = text;
  msg.classList.toggle("success", ok);
  msg.classList.toggle("error", !ok);
}

function renderChatPreview(template, settings, sample = {}) {
  let text = String(template || "");
  const values = {
    user: sample.user || "Usuário",
    points: sample.points ?? 183,
    currency: settings?.currency_name || "pontos",
    emoji: settings?.currency_emoji || "",
    emoji_text: settings?.currency_emoji ? ` ${settings.currency_emoji}` : "",
    rank: sample.rank ?? 4,
    rank_text: sample.rank != null ? ` Sua posição no ranking é #${sample.rank}.` : "",
    command: settings?.currency_command || "!points",
    target: "Usuário",
    amount: 10,
    new_points: 193,
    commands: "!cmds !ranking",
    duel_result: "⚔️ Exemplo de duelo",
    ranking: "🏆 1. Usuário 500 • 2. Player 350",
  };
  Object.entries(values).forEach(([key, value]) => {
    text = text.replaceAll(`$(${key})`, String(value ?? ""));
  });
  text = text.replace(/#None/g, "");
  return text.replace(/\s{2,}/g, " ").trim();
}

function updatePointsResponsePreview() {
  const preview = $("points_response_preview");
  const input = $("points_response");
  if (!preview || !input) return;
  const settings = {
    currency_name: $("currency_name")?.value || "pontos",
    currency_emoji: $("currency_emoji")?.value || "",
    currency_command: $("currency_command")?.value || "!points",
  };
  preview.textContent = renderChatPreview(input.value, settings);
}

function togglePointsResponseEditor(force) {
  const input = $("points_response");
  const preview = $("points_response_preview");
  const button = $("points_response_edit");
  const help = $("points_response_help");
  if (!input || !preview || !button) return;
  const editing = force === undefined ? input.hidden : force;
  input.hidden = !editing;
  preview.hidden = editing;
  if (help) help.hidden = !editing;
  button.textContent = editing ? "Visualizar mensagem" : "Editar mensagem";
  if (editing) input.focus();
  else updatePointsResponsePreview();
}

function updatePreview(settings) {
  if ($("statCurrency")) $("statCurrency").textContent = settings.currency_name ?? "";
  if ($("statCommand")) $("statCommand").textContent = settings.currency_command ?? "";
  if ($("statEmoji")) $("statEmoji").textContent = settings.currency_emoji ?? "";
  if ($("previewCurrency")) $("previewCurrency").textContent = settings.currency_name ?? "";
  if ($("previewCommand")) $("previewCommand").textContent = settings.currency_command ?? "";
  if ($("previewEmoji")) $("previewEmoji").textContent = settings.currency_emoji ?? "";
  updatePointsResponsePreview();
}

function updateEconomyCards(settings = {}) {
  const name = settings.currency_name ?? $("currency_name")?.value ?? "Pontos";
  const command = settings.currency_command ?? $("currency_command")?.value ?? "!pontos";
  const rewards = settings.point_rewards || {};
  const watch = settings.watch_points ?? rewards.watch_points ?? $("watch_points")?.value ?? 1;
  const sub = settings.sub_bonus ?? rewards.sub_bonus ?? $("sub_bonus")?.value ?? 500;
  const kicks = settings.kicks_bonus_per_kick ?? rewards.kicks_bonus_per_kick ?? $("kicks_bonus_per_kick")?.value ?? 1;
  if ($("pointsCardName")) $("pointsCardName").textContent = name;
  if ($("pointsCardCommand")) $("pointsCardCommand").textContent = command;
  if ($("rewardsCardSummary")) {
    const n = Number(watch);
    $("rewardsCardSummary").textContent = `${watch} ponto${n === 1 ? "" : "s"} • sub +${sub} • KICK +${kicks}/cada`;
  }
}

function setSaveMessage(id, text, ok = true) {
  const el = $(id);
  if (!el) return;
  el.textContent = text;
  el.classList.toggle("success", ok);
  el.classList.toggle("error", !ok);
  el.hidden = false;
}

function injectCommandStyles() {
  if ($("sn7-command-v24-style")) return;
  const style = document.createElement("style");
  style.id = "sn7-command-v24-style";
  style.textContent = `
    .sn7-command-panel{max-width:980px;padding:0;overflow:hidden}
    .sn7-command-category{padding:18px 20px}
    .sn7-command-category+.sn7-command-category{border-top:1px solid var(--border)}
    .sn7-command-head{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:10px}
    .sn7-command-head h3{margin:0}
    .sn7-command-status{font-size:12px;color:var(--muted);margin:0 0 10px}
    .sn7-command-row{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:12px 8px;border-top:1px solid var(--border);cursor:pointer}
    .sn7-command-row:hover{background:rgba(255,255,255,.025)}
    .sn7-command-row>div:first-child{min-width:0;flex:1}
    .sn7-command-row>.sn7-command-status-badge{flex:0 0 auto;display:inline-flex;align-items:center;gap:5px;white-space:nowrap;color:var(--muted);font-size:12px}
    .sn7-command-status-badge .sn7-status-dot{width:10px;height:10px;border-radius:50%;display:inline-block;flex:0 0 10px;background:#22c55e;box-shadow:0 0 8px rgba(34,197,94,.35)}
    .sn7-command-status-badge.offline .sn7-status-dot{background:#ef4444;box-shadow:0 0 8px rgba(239,68,68,.25)}
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
    .sn7-toggle input{width:20px!important;height:20px;margin:0!important;accent-color:#ef4444;cursor:pointer}
    .sn7-system-action{border:1px solid #ef4444!important;color:#ff8f8f!important;background:rgba(239,68,68,.08)!important}
    .sn7-system-action.off{border-color:#ef4444!important;color:#ff8f8f!important;background:rgba(239,68,68,.08)!important}
    .sn7-alias-row{display:flex;gap:8px;margin-top:7px}
    .sn7-alias-row input{margin:0}
    .sn7-danger{border:1px solid #63383b;background:transparent;color:#ff9c9c;border-radius:8px;padding:8px 11px;cursor:pointer}
    .sn7-actions{display:flex;justify-content:space-between;gap:10px;margin-top:20px;align-items:center;flex-wrap:wrap}
    .sn7-save-message{font-size:12px;min-height:18px;color:#8f98a8}
    .sn7-save-message.success{color:#55d98b}
    .sn7-save-message.error{color:#ff9ca6}
    .sn7-system-action:disabled{opacity:.72;cursor:wait}
    .sn7-spinner{display:inline-block;width:13px;height:13px;border:2px solid currentColor;border-right-color:transparent;border-radius:50%;vertical-align:-2px;margin-right:7px;animation:sn7spin .65s linear infinite}
    @keyframes sn7spin{to{transform:rotate(360deg)}}
    .sn7-actions>div{display:flex;gap:8px}
    .sn7-variant-list{display:flex;flex-direction:column;gap:7px;margin-top:8px}
    .sn7-variant-item{display:flex;align-items:center;gap:8px}
    .sn7-variant-item input{margin:0}
    .sn7-variant-remove{border:1px solid #63383b;background:transparent;color:#ff9c9c;border-radius:8px;padding:7px 9px;cursor:pointer}
    .sn7-remove-all{margin-top:8px}
    .sn7-help{font-size:11px;color:var(--muted);margin-top:6px}
    .sn7-points-response{min-height:105px!important;background:#0b0d12!important;color:#fff!important;border:1px solid #303744!important}
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

function renderCommandListPreview(command) {
  const settings = {
    currency_name: $("currency_name")?.value || "pontos",
    currency_emoji: $("currency_emoji")?.value || "",
    currency_command: $("currency_command")?.value || "!points",
  };
  if (command.command_key === "points") {
    return renderChatPreview(command.response, settings, { user: "Usuário", points: 183, rank: 4 });
  }
  return renderChatPreview(command.response, settings);
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
          <code>${esc(command.command_key === "duel" ? "!aposta" : command.command)}</code>
          <small>${esc(command.description)}</small>
          <span class="sn7-command-preview">${esc(renderCommandListPreview(command))}</span>
          ${command.aliases?.length ? `<div class="sn7-aliases">Variantes: ${command.aliases.map(esc).join(", ")}</div>` : ""}
        </div>
        <span class="sn7-command-status-badge ${command.enabled ? "" : "offline"}"><i class="sn7-status-dot"></i>${command.enabled ? "Ativo" : "Desativado"}</span>
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
  const status = $("commandPanelStatus");
  if (status) status.innerHTML = `<span class="sn7-spinner"></span>Carregando comandos...`;
  try {
    const data = await apiJson(`/api/commands/${BROADCASTER_ID}`);
    commandCache = Array.isArray(data.commands) ? data.commands : [];
    renderCommands();
  } catch (error) {
    commandCache = [];
    renderCommands();
    if ($("commandPanelStatus")) $("commandPanelStatus").textContent = `⚠ ${error.message}`;
  }
}

function closeCommandModal() {
  const modal = document.querySelector(".sn7-command-modal");
  if (!modal) return;
  modal.classList.remove("open");
  setTimeout(() => modal.remove(), 220);
}

function openCommand(encodedKey) {
  const key = decodeURIComponent(encodedKey);
  const command = commandCache.find((item) => item.command_key === key);
  if (command) showCommand(command, false);
}

function newCommand() {
  draftAliases = [];
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

function renderDraftAliases() {
  const list = $("v2variants");
  if (!list) return;
  list.innerHTML = draftAliases.length
    ? draftAliases.map((alias, index) => `
        <div class="sn7-variant-item">
          <input value="${esc(alias)}" readonly>
          <button class="sn7-variant-remove" type="button" onclick="removeDraftAlias(${index})">Excluir</button>
        </div>`).join("")
    : `<div class="sn7-help">Nenhuma variante adicionada.</div>`;
  const removeAll = $("v2removeAll");
  if (removeAll) removeAll.style.display = draftAliases.length ? "inline-block" : "none";
}

function addDraftAlias() {
  const input = $("v2alias");
  const alias = input?.value.trim().toLowerCase();
  if (!alias) return;
  if (!alias.startsWith("!")) {
    alert("A variante deve começar com !");
    return;
  }
  if (alias === $("v2cmd")?.value.trim().toLowerCase()) {
    alert("A variante não pode ser igual ao comando principal.");
    return;
  }
  const existing = [...draftAliases, ...commandCache.flatMap((x) => [x.command, ...(x.aliases || [])])];
  if (existing.includes(alias)) {
    alert("Essa palavra de ativação já está em uso.");
    return;
  }
  draftAliases.push(alias);
  input.value = "";
  renderDraftAliases();
}

function removeDraftAlias(index) {
  draftAliases.splice(index, 1);
  renderDraftAliases();
}

function removeAllDraftAliases() {
  draftAliases = [];
  renderDraftAliases();
}

function sn7ConfirmAction(title, message, confirmText = "Continuar") {
  return new Promise((resolve) => {
    document.querySelector(".sn7-confirm-modal")?.remove();

    const modal = document.createElement("div");
    modal.className = "sn7-confirm-modal";
    modal.innerHTML = `
      <div class="sn7-confirm-card" role="dialog" aria-modal="true" aria-labelledby="sn7ConfirmTitle">
        <h3 id="sn7ConfirmTitle">${esc(title)}</h3>
        <p>${esc(message)}</p>
        <div class="sn7-confirm-actions">
          <button type="button" class="sn7-confirm-cancel">Cancelar</button>
          <button type="button" class="sn7-confirm-ok danger">${esc(confirmText)}</button>
        </div>
      </div>`;

    document.body.appendChild(modal);

    const finish = (value) => {
      modal.remove();
      resolve(value);
    };

    modal.querySelector(".sn7-confirm-cancel")?.addEventListener("click", () => finish(false));
    modal.querySelector(".sn7-confirm-ok")?.addEventListener("click", () => finish(true));
    modal.addEventListener("click", (event) => {
      if (event.target === modal) finish(false);
    });
  });
}


function showCommand(command, isNew = false) {
  document.querySelector(".sn7-command-modal")?.remove();
  if (!isNew) draftAliases = [...(command.aliases || [])];

  const modal = document.createElement("div");
  modal.className = "sn7-modal sn7-command-modal";

  const aliases = (command.aliases || []).map((alias) => `
    <div class="sn7-alias-row">
      <input value="${esc(alias)}" readonly>
      <button class="sn7-danger" type="button" onclick="removeAlias('${encodeURIComponent(command.command_key)}','${encodeURIComponent(alias)}')">Excluir</button>
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
      <label>Variantes
        ${isNew ? `
          <div class="sn7-alias-row">
            <input id="v2alias" placeholder="!disc" autocapitalize="none" spellcheck="false">
            <button class="btn" type="button" onclick="addDraftAlias()">Adicionar</button>
          </div>
          <div id="v2variants" class="sn7-variant-list"></div>
          <button id="v2removeAll" class="sn7-danger sn7-remove-all" type="button" onclick="removeAllDraftAliases()">Excluir todas</button>
        ` : `
          ${aliases || `<div class="sn7-help">Nenhuma variante cadastrada.</div>`}
          <div class="sn7-alias-row">
            <input id="v2alias" placeholder="!disc" autocapitalize="none" spellcheck="false">
            <button class="btn" type="button" onclick="addAlias('${encodeURIComponent(command.command_key)}')">Adicionar</button>
          </div>
        `}
      </label>
      <div class="sn7-actions">
        <p id="commandSaveMsg" class="sn7-save-message" aria-live="polite"></p>
        ${command.is_system ? `<button class="sn7-subtle" type="button" onclick="resetSystemCommandV2('${encodeURIComponent(command.command_key)}')">Redefinir configuração</button>` : ""}
        <button class="${command.is_system ? "sn7-system-action" : "sn7-danger"} ${command.is_system && !command.enabled ? "off" : ""}" type="button"
          onclick="deleteCommandV2('${encodeURIComponent(command.command_key)}',${command.is_system},this)">
          ${command.is_system ? (command.enabled ? "Desativar comando" : "Ativar comando") : "Excluir"}
        </button>
        <div>
          <button id="commandSaveButton" class="btn" type="button" onclick="saveCommandV2('${encodeURIComponent(command.command_key)}',${isNew},this)">Salvar alterações</button>
          <button class="sn7-subtle" type="button" onclick="closeCommandModal()">Fechar</button>
        </div>
      </div>
    </div>`;
  document.body.appendChild(modal); requestAnimationFrame(() => modal.classList.add("open"));
  requestAnimationFrame(() => modal.classList.add("open"));
  if (isNew) renderDraftAliases();
}

async function saveCommandV2(encodedKey, isNew, button) {
  const body = {
    command: $("v2cmd")?.value.trim(),
    description: $("v2desc")?.value.trim(),
    response: $("v2resp")?.value,
  };
  if (isNew) body.aliases = [...draftAliases];
  const saveButton = button || $("commandSaveButton");
  const originalText = saveButton?.textContent || "Salvar alterações";
  if (saveButton) { saveButton.disabled = true; saveButton.textContent = "Salvando..."; }
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
    renderCommands();
    if (isNew) {
      const created = commandCache.find((item) => item.command === body.command);
      if (created) {
        document.querySelector(".sn7-command-modal")?.remove();
        draftAliases = [];
        showCommand(created, false);
      }
    }
    setSaveMessage("commandSaveMsg", "✓ Alterações salvas.", true);
    if (saveButton) saveButton.textContent = "Salvo ✓";
    setTimeout(() => { if (saveButton) saveButton.textContent = originalText; }, 1400);
  } catch (error) {
    setSaveMessage("commandSaveMsg", `⚠ ${error.message}`, false);
  } finally {
    if (saveButton) saveButton.disabled = false;
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
    document.querySelector(".sn7-command-modal")?.remove();
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
    document.querySelector(".sn7-command-modal")?.remove();
    await loadCommands();
    openCommand(encodedKey);
  } catch (error) {
    alert(error.message);
  }
}

async function resetSystemCommandV2(encodedKey) {
  const key = decodeURIComponent(encodedKey);
  const command = commandCache.find((item) => item.command_key === key);
  if (!command || !command.is_system) return;

  const ok = await sn7ConfirmAction(
    "Redefinir configuração?",
    `A configuração de ${command.command} será restaurada para o padrão original do sistema.`,
    "Continuar"
  );
  if (!ok) return;

  try {
    await apiJson(`/api/commands/${BROADCASTER_ID}/${encodedKey}/reset`, { method: "POST" });
    document.querySelector(".sn7-command-modal")?.remove();
    await loadCommands();
    openCommand(encodedKey);
  } catch (error) {
    alert(error.message);
  }
}


async function deleteCommandV2(encodedKey, isSystem, button) {
  button = button || document.querySelector(".sn7-system-action");
  const originalText = button ? button.textContent.trim() : "";

  if (button) {
    button.disabled = true;
    button.innerHTML = `<span class="sn7-spinner"></span>${isSystem ? "Atualizando..." : "Excluindo..."}`;
  }

  try {
    const row = button?.closest(".sn7-command-row");
    if (row) row.classList.add("loading");
    const data = await apiJson(`/api/commands/${BROADCASTER_ID}/${encodedKey}`, { method: "DELETE" });
    commandCache = Array.isArray(data.commands) ? data.commands : commandCache;
    renderCommands();

    if (isSystem) {
      const updated = commandCache.find((item) => item.command_key === decodeURIComponent(encodedKey));
      if (button && updated) {
        button.disabled = false;
        button.classList.toggle("off", !updated.enabled);
        button.innerHTML = updated.enabled ? "Desativar comando" : "Ativar comando";
      }
      return;
    }

    document.querySelector(".sn7-command-modal")?.remove();
  } catch (error) {
    if (button) {
      button.disabled = false;
      button.textContent = originalText || (isSystem ? "Desativar comando" : "Excluir");
    }
    alert(error.message);
  }
}

async function loadSettings() {
  try {
    const data = await apiJson(`/api/settings/${BROADCASTER_ID}`);
    const settings = data.settings;
    Object.assign(settings, settings.point_rewards || {});
    [
      "currency_name", "currency_command", "currency_emoji", "points_response",
      "rank_title", "rank_limit", "duel_win_points", "duel_loss_points",
      "watch_points", "watch_interval_minutes", "sub_bonus", "kicks_bonus_per_kick",
    ].forEach((key) => {
      if ($(key)) $(key).value = settings[key] ?? "";
    });
    updatePreview(settings);
    updateEconomyCards(settings);
    if (data.demo) setMessage("Modo demonstração: alterações não são persistidas.", false);
  } catch (error) {
    setMessage(`⚠ ${error.message}`, false);
  }
  await loadCommands();
}

async function saveSettingsAndClose(modalId) {
  const button = (typeof event !== "undefined" && event) ? event.currentTarget : null;
  if (button) button.disabled = true;
  try {
    const ok = await saveSettings();
    if (ok) {
      const target = modalId === "sn7RewardsEditor" ? "rewardsMsg" : "settingsMsg";
      setSaveMessage(target, "✓ Alterações salvas.", true);
    }
  } finally {
    if (button) button.disabled = false;
  }
}

async function loadRanking() {
  const list = $("sn7RankingList");
  if (!list || typeof BROADCASTER_ID === "undefined" || BROADCASTER_ID === null) return;
  list.innerHTML = '<div class="sn7-ranking-loading">Carregando ranking...</div>';
  try {
    const data = await apiJson(`/api/ranking/${BROADCASTER_ID}?limit=50`);
    const rows = Array.isArray(data.ranking) ? data.ranking : [];
    if (!rows.length) {
      list.innerHTML = '<div class="sn7-ranking-empty">Nenhum usuário com pontos ainda.</div>';
      return;
    }
    const currency = data.currency || "Pontos";
    list.innerHTML = rows.map((item) => `
      <div class="sn7-ranking-row">
        <div class="sn7-ranking-position">#${esc(item.position)}</div>
        <div class="sn7-ranking-user">${esc(item.username)}</div>
        <div class="sn7-ranking-points">${Number(item.points || 0).toLocaleString("pt-BR")} ${esc(currency)}</div>
      </div>
    `).join("");
  } catch (error) {
    list.innerHTML = `<div class="sn7-ranking-empty">⚠ ${esc(error.message)}</div>`;
  }
}

async function resetRankingPoints() {
  const ok = await sn7ConfirmAction(
    "Resetar ranking/pontos?",
    "Tem certeza que deseja resetar os pontos do seu canal? Todos os pontos dos usuários serão zerados. Os usuários e as configurações do canal serão mantidos.",
    "Continuar"
  );
  if (!ok) return;

  try {
    await apiJson(`/api/ranking/${BROADCASTER_ID}/reset`, { method: "POST" });
    await loadRanking();
  } catch (error) {
    alert(error.message);
  }
}


async function saveSettings() {
  const data = {};
  [
    "currency_name", "currency_command", "currency_emoji", "points_response",
    "rank_title", "rank_limit", "duel_win_points", "duel_loss_points",
    "watch_points", "watch_interval_minutes", "sub_bonus", "kicks_bonus_per_kick",
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
    updateEconomyCards(result.settings);
    setMessage("✓ Alterações salvas.", true);
    setSaveMessage("rewardsMsg", "✓ Alterações salvas.", true);
    await loadCommands();
    return true;
  } catch (error) {
    setMessage(`⚠ ${error.message}`, false);
    return false;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  injectCommandStyles();

  // Registra a navegação antes de restaurar a aba.
  // Assim, F5 no Android mantém a última tela aberta.
  setupTabPersistence();
  restoreSavedTab();

  ["currency_name", "currency_emoji", "currency_command", "points_response", "watch_points", "watch_interval_minutes", "sub_bonus", "kicks_bonus_per_kick"].forEach((id) => {
    $(id)?.addEventListener("input", () => {
      updatePointsResponsePreview();
      updateEconomyCards();
      renderCommands();
    });
  });

  loadSettings();
  loadRanking();
});

// SN7 MODAL + POINTS CLEAN IMPLEMENTATION
(function () {
  "use strict";

  function $(id) { return document.getElementById(id); }

  function syncBodyLock() {
    const open = Array.from(document.querySelectorAll(".sn7-config-modal"))
      .some((item) => !item.hidden);
    document.body.classList.toggle("sn7-modal-open", open);
  }

  function openModal(id) {
    const el = $(id);
    if (!el) return false;
    if (el.parentElement !== document.body) document.body.appendChild(el);
    el.hidden = false;
    syncBodyLock();
    const card = el.querySelector(".sn7-config-modal-card");
    if (card) card.scrollTop = 0;
    return true;
  }

  function closeModal(id) {
    const el = $(id);
    if (!el) return false;
    el.hidden = true;
    syncBodyLock();
    return true;
  }

  window.openPointsEditor = () => openModal("sn7PointsEditor");
  window.closePointsEditor = () => closeModal("sn7PointsEditor");
  window.openRewardsEditor = () => openModal("sn7RewardsEditor");
  window.closeRewardsEditor = () => closeModal("sn7RewardsEditor");

  window.openApostaEditor = function () {
    openModal("sn7ApostaEditor");
    loadApostaSettings();
    return true;
  };
  window.closeApostaEditor = () => closeModal("sn7ApostaEditor");

  function applyDefaults() {
    const name = $("currency_name");
    const command = $("currency_command");
    const emoji = $("currency_emoji");
    const response = $("points_response");

    if (name && !name.value.trim()) name.value = "Pontos";
    if (command && !command.value.trim()) command.value = "!pontos";
    if (emoji && !emoji.value) emoji.value = "";
    if (response && !response.value.trim()) {
      response.value = "$(user), você tem $(points) $(currency).$(emoji_text)$(rank_text)";
    }

    const rewardDefaults = {
      watch_points: "1",
      watch_interval_minutes: "10",
      sub_bonus: "500",
      kicks_bonus_per_kick: "1"
    };
    Object.entries(rewardDefaults).forEach(([id, value]) => {
      const el = $(id);
      if (el && !String(el.value || "").trim()) el.value = value;
    });

    if ($("pointsCardName")) $("pointsCardName").textContent = name?.value || "Pontos";
    if ($("pointsCardCommand")) $("pointsCardCommand").textContent = command?.value || "!pontos";

    if ($("rewardsCardSummary")) {
      const w = $("watch_points")?.value || "1";
      const sub = $("sub_bonus")?.value || "500";
      const kicks = $("kicks_bonus_per_kick")?.value || "1";
      $("rewardsCardSummary").textContent =
        `${w} ponto${Number(w) === 1 ? "" : "s"} • sub +${sub} • KICK +${kicks}/cada`;
    }
  }

  async function loadApostaSettings() {
    try {
      if (typeof apiJson !== "function" || typeof BROADCASTER_ID === "undefined") return;
      const data = await apiJson(`/api/commands/${BROADCASTER_ID}`);
      const cmd = (data.commands || []).find((x) => x.command_key === "duel");

      const savedCommand = String(cmd?.command || "").trim();
      const savedResponse = String(cmd?.response || "").trim();
      const apostaCommand = savedCommand || "!aposta";
      const oldDefault = "$(duel_result)";
      const newDefault = "$(user) está apostando $(amount) points contra $(target).";
      const apostaResponse = !savedResponse || savedResponse === oldDefault ? newDefault : savedResponse;
      if ($("aposta_command")) $("aposta_command").value = apostaCommand;
      if ($("aposta_response")) $("aposta_response").value = apostaResponse;
      if ($("apostaCardCommand")) $("apostaCardCommand").textContent = apostaCommand;
    } catch (e) {
      if ($("apostaMsg")) $("apostaMsg").textContent = "⚠ " + e.message;
    }
  }

  window.sn7LoadAposta = loadApostaSettings;

  async function resetPointsSettings() {
    const ok = await sn7ConfirmAction(
      "Redefinir configuração de pontos?",
      "A configuração atual será substituída pelo padrão original do sistema. Isso não apaga os pontos dos usuários.",
      "Continuar"
    );
    if (!ok) return;

    try {
      const data = await apiJson(`/api/settings/${BROADCASTER_ID}/reset-points`, { method: "POST" });
      const settings = data.settings || {};
      Object.assign(settings, settings.point_rewards || {});
      [
        "currency_name", "currency_command", "currency_emoji", "points_response",
        "rank_title", "rank_limit", "duel_win_points", "duel_loss_points",
        "watch_points", "watch_interval_minutes", "sub_bonus", "kicks_bonus_per_kick",
      ].forEach((key) => {
        if ($(key)) $(key).value = settings[key] ?? "";
      });
      updatePreview(settings);
      updateEconomyCards(settings);
      await loadCommands();
      if ($("settingsMsg")) $("settingsMsg").textContent = "✓ Configuração redefinida.";
    } catch (e) {
      if ($("settingsMsg")) $("settingsMsg").textContent = "⚠ " + e.message;
    }
  }

  async function resetRewardsSettings() {
    const ok = await sn7ConfirmAction(
      "Redefinir recompensas?",
      "Os valores de presença, intervalo, bônus de inscrição e bônus por KICK voltarão ao padrão original.",
      "Continuar"
    );
    if (!ok) return;

    try {
      const data = await apiJson(`/api/settings/${BROADCASTER_ID}/reset-rewards`, { method: "POST" });
      const settings = data.settings || {};
      Object.assign(settings, settings.point_rewards || {});
      [
        "watch_points", "watch_interval_minutes", "sub_bonus", "kicks_bonus_per_kick",
      ].forEach((key) => {
        if ($(key)) $(key).value = settings[key] ?? "";
      });
      updateEconomyCards(settings);
      if ($("rewardsMsg")) $("rewardsMsg").textContent = "✓ Configuração redefinida.";
    } catch (e) {
      if ($("rewardsMsg")) $("rewardsMsg").textContent = "⚠ " + e.message;
    }
  }

  async function resetApostaSettings() {
    const ok = await sn7ConfirmAction(
      "Redefinir configuração da aposta?",
      "O comando e a mensagem da aposta voltarão ao padrão original do sistema.",
      "Continuar"
    );
    if (!ok) return;

    try {
      await apiJson(`/api/commands/${BROADCASTER_ID}/duel/reset`, { method: "POST" });
      await loadCommands();
      await loadApostaSettings();
      if ($("apostaMsg")) $("apostaMsg").textContent = "✓ Configuração redefinida.";
    } catch (e) {
      if ($("apostaMsg")) $("apostaMsg").textContent = "⚠ " + e.message;
    }
  }

  window.resetPointsSettings = resetPointsSettings;
  window.resetRewardsSettings = resetRewardsSettings;
  window.resetApostaSettings = resetApostaSettings;

  async function saveApostaSettings() {
    const msg = $("apostaMsg");
    try {
      const command = $("aposta_command")?.value.trim() || "!aposta";
      const response = $("aposta_response")?.value.trim() || "$(duel_result)";
      await apiJson(`/api/commands/${BROADCASTER_ID}/duel`, {
        method: "PATCH",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({command, response})
      });
      if ($("apostaCardCommand")) $("apostaCardCommand").textContent = command;
      setSaveMessage("apostaMsg", "✓ Alterações salvas.", true);
      if ($("apostaMsg")) $("apostaMsg").textContent = "✓ Alterações salvas.";
    } catch (e) {
      if (msg) msg.textContent = "⚠ " + e.message;
    }
  }

  window.saveApostaSettings = saveApostaSettings;

  // ÚNICO listener de fechamento.
  document.addEventListener("click", function (event) {
    const close = event.target.closest(".sn7-config-close");
    if (close) {
      event.preventDefault();
      event.stopPropagation();
      const owner = close.closest(".sn7-config-modal");
      if (owner) closeModal(owner.id);
      return;
    }
    const owner = event.target.closest(".sn7-config-modal");
    if (owner && event.target === owner) closeModal(owner.id);
  }, true);

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;
    document.querySelectorAll(".sn7-config-modal").forEach((item) => {
      if (!item.hidden) closeModal(item.id);
    });
  });

  window.addEventListener("load", function () {
    setTimeout(applyDefaults, 100);
    setTimeout(loadApostaSettings, 150);
  });
})();
