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

function sn7ShowOperationLoader() {
  const loader = document.getElementById("sn7OperationLoader");
  if (!loader) return;
  clearTimeout(window.__sn7OperationLoaderTimer);
  loader.classList.add("open");
  // Fail-safe: uma falha inesperada nunca pode deixar a navegação travada.
  window.__sn7OperationLoaderTimer = setTimeout(() => sn7HideOperationLoader(), 12000);
}

function sn7HideOperationLoader() {
  const loader = document.getElementById("sn7OperationLoader");
  clearTimeout(window.__sn7OperationLoaderTimer);
  if (loader) loader.classList.remove("open");
}

function sn7HideBootLoader() {
  const loader = document.getElementById("sn7BootLoader");
  if (!loader) return;
  loader.classList.add("done");
  setTimeout(() => loader.remove(), 220);
}


const SN7_ACTIVE_TAB_KEY = "sn7-core-active-tab";
const SN7_ACTIVE_MODAL_KEY = "sn7-core-active-modal";
let sn7NavigationReady = false;
let sn7Booting = true;

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

  // Um editor aberto nunca pode sobreviver à troca de seção.
  if (typeof window.sn7CloseAllModals === "function") {
    window.sn7CloseAllModals();
  }

  document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
  document.querySelectorAll("nav button").forEach((x) => x.classList.remove("active"));

  const section = $(button.dataset.tab);
  if (section) section.classList.add("active");

  button.classList.add("active");

  // Durante o boot inicial, os dados essenciais são carregados em paralelo
  // enquanto o loader cobre a interface. Depois disso, as abas continuam
  // carregando sob demanda para evitar consultas desnecessárias.
  if (!sn7Booting) {
    if (button.dataset.tab === "economy" && typeof loadSettings === "function") {
      loadSettings().catch(() => {});
    }
    if (button.dataset.tab === "commands" && typeof loadCommands === "function") {
      loadCommands().catch(() => {});
    }
    if (button.dataset.tab === "ranking" && typeof loadRanking === "function") {
      loadRanking().catch(() => {});
      startRankingPolling();
    } else {
      stopRankingPolling();
    }
    if (button.dataset.tab === "profile" && typeof window.sn7LoadProfile === "function") {
      window.sn7LoadProfile();
    }
    if (button.dataset.tab === "music" && typeof window.loadMusic === "function") {
      window.loadMusic().catch(() => {});
    }
  }

  const title = $("title");
  if (title) {
    const label = button.querySelector(".sn7-nav-label");
    const navIcon = button.querySelector(".sn7-nav-icon");
    const titleIcon = title.querySelector(".sn7-page-title-icon");
    const titleText = title.querySelector(".sn7-page-title-text");
    if (titleText) {
      const baseLabel = label?.textContent?.trim() || button.getAttribute("aria-label") || "";
      titleText.textContent = button.dataset.tab === "minigames" ? "Mini Games" : baseLabel;
    }
    if (titleIcon) {
      titleIcon.replaceChildren();
      if (navIcon) {
        // Nunca copiamos a foto de perfil para o cabeçalho. Quando a conta
        // está conectada, o ícone da navegação contém um <img>; copiar esse
        // elemento fazia a foto ocupar uma área enorme no topo da página.
        const sourceSvg = navIcon.querySelector("svg");
        if (sourceSvg) {
          const clone = sourceSvg.cloneNode(true);
          clone.removeAttribute("id");
          clone.classList.add("sn7-page-title-svg");
          titleIcon.appendChild(clone);
        } else {
          const fallback = document.createElementNS("http://www.w3.org/2000/svg", "svg");
          fallback.setAttribute("viewBox", "0 0 24 24");
          fallback.setAttribute("aria-hidden", "true");
          fallback.classList.add("sn7-page-title-svg");
          const icons = {
            overview: '<path d="M3.5 10.6 12 3.5l8.5 7.1"/><path d="M5.5 9.7v9.8a1 1 0 0 0 1 1h11a1 1 0 0 0 1-1V9.7"/><path d="M9.5 20.5v-5.8h5v5.8"/>',
            economy: '<circle cx="12" cy="12" r="7.8"/><path d="M12 8v8M9.2 10.2c.8-1.1 4.8-1.1 5.6.3.9 1.7-1.1 2.4-2.8 2.7-1.7.3-3.7.8-2.9 2.5.7 1.5 4.9 1.6 5.8.1"/>',
            ranking: '<path d="M7 20V10h4v10M13 20V4h4v16M3 20h18"/>',
            music: '<path d="M9 18V5l10-2v13"/><circle cx="6.5" cy="18" r="3"/><circle cx="16.5" cy="16" r="3"/>',
            minigames: '<path d="m7.5 8.5 2-2h5l2 2"/><path d="M7.5 8.5h9a4.2 4.2 0 0 1 3.9 5.7l-1.2 3.1a2.4 2.4 0 0 1-4.3.3L14 16h-4l-2.9 1.6a2.4 2.4 0 0 1-4.3-.3l-1.2-3.1a4.2 4.2 0 0 1 3.9-5.7Z"/>',
            commands: '<path d="m8 8 4 4-4 4M13 16h4"/><rect x="3.5" y="4" width="17" height="16" rx="3"/>',
            profile: '<circle cx="12" cy="8.2" r="3.2"/><path d="M5.2 20a6.8 6.8 0 0 1 13.6 0"/>'
          };
          fallback.innerHTML = icons[button.dataset.tab] || icons.profile;
          titleIcon.appendChild(fallback);
        }
      }
    }
  }

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

  document.querySelectorAll("[data-tab]").forEach((button) => {
    if (button.dataset.sn7NavigationBound === "1") return;
    button.dataset.sn7NavigationBound = "1";
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
    .sn7-modal{position:fixed;inset:0;background:rgba(0,0,0,.68);display:flex;align-items:center;justify-content:center;padding:16px 16px calc(16px + env(safe-area-inset-bottom));z-index:2147483200;box-sizing:border-box}
    .sn7-box{width:min(620px,100%);max-height:calc(100svh - 32px);overflow:auto;background:#11151d;border:1px solid var(--border);border-radius:16px;padding:20px}
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

let commandsLoadPromise = null;
let commandsLoadedAt = 0;
const COMMAND_CACHE_TTL = 30000;

async function loadCommands(force = false) {
  if (!force && commandCache.length && (Date.now() - commandsLoadedAt) < COMMAND_CACHE_TTL) {
    buildCommandCatalog();
    renderCommands();
    return commandCache;
  }
  if (commandsLoadPromise) return commandsLoadPromise;

  commandsLoadPromise = (async () => {
    buildCommandCatalog();
    const panel = document.querySelector(".sn7-command-panel");
    if (panel) panel.classList.add("is-loading");
    const status = $("commandPanelStatus");
    if (status) status.innerHTML = `<span class="sn7-spinner"></span>Carregando comandos...`;

    try {
      const data = await apiJson(`/api/commands/${BROADCASTER_ID}`);
      commandCache = Array.isArray(data.commands) ? data.commands : [];
      commandsLoadedAt = Date.now();
      renderCommands();
      return commandCache;
    } catch (error) {
      commandCache = [];
      renderCommands();
      if ($("commandPanelStatus")) $("commandPanelStatus").textContent = `⚠ ${error.message}`;
      throw error;
    } finally {
      const currentPanel = document.querySelector(".sn7-command-panel");
      if (currentPanel) {
        requestAnimationFrame(() => {
          currentPanel.classList.remove("is-loading");
          currentPanel.classList.add("is-ready");
          setTimeout(() => currentPanel.classList.remove("is-ready"), 260);
        });
      }
      commandsLoadPromise = null;
    }
  })();

  return commandsLoadPromise;
}

function closeCommandModal() {
  const modal = document.querySelector(".sn7-command-modal");
  if (!modal) return;
  modal.classList.remove("open");
  try { sessionStorage.removeItem(SN7_ACTIVE_MODAL_KEY); } catch (_) {}
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
    modal.setAttribute("data-sn7-layer", "confirm");
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
    requestAnimationFrame(() => modal.classList.add("open"));

    const finish = (value) => {
      modal.classList.remove("open");
      modal.classList.add("closing");
      setTimeout(() => modal.remove(), 160);
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
  modal.dataset.draftEnabled = command.enabled ? "1" : "0";

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
          onclick="${command.is_system ? "toggleCommandDraft(this)" : "deleteCommandV2('" + encodeURIComponent(command.command_key) + "',false,this)"}">
          ${command.is_system ? (command.enabled ? "Desativar comando" : "Ativar comando") : "Excluir"}
        </button>
        <div>
          <button id="commandSaveButton" class="btn" type="button" onclick="saveCommandV2('${encodeURIComponent(command.command_key)}',${isNew},this)">Salvar alterações</button>
          <button class="sn7-subtle" type="button" onclick="closeCommandModal()">Fechar</button>
        </div>
      </div>
    </div>`;
  document.body.appendChild(modal);
  try { sessionStorage.setItem(SN7_ACTIVE_MODAL_KEY, `command:${command.command_key || "new"}`); } catch (_) {}
  requestAnimationFrame(() => modal.classList.add("open"));
  if (isNew) renderDraftAliases();
}

function toggleCommandDraft(button) {
  const modal = document.querySelector(".sn7-command-modal");
  if (!modal || !button) return;

  const current = modal.dataset.draftEnabled !== "0";
  const next = !current;
  modal.dataset.draftEnabled = next ? "1" : "0";

  button.classList.toggle("off", !next);
  button.textContent = next ? "Desativar comando" : "Ativar comando";

  setSaveMessage(
    "commandSaveMsg",
    next
      ? "Comando marcado como ativo. Clique em Salvar alterações para aplicar."
      : "Comando marcado como desativado. Clique em Salvar alterações para aplicar.",
    true
  );
}

async function saveCommandV2(encodedKey, isNew, button) {
  const modal = document.querySelector(".sn7-command-modal");
  const body = {
    command: $("v2cmd")?.value.trim(),
    description: $("v2desc")?.value.trim(),
    response: $("v2resp")?.value,
  };
  if (modal?.dataset.draftEnabled !== undefined) {
    body.enabled = modal.dataset.draftEnabled === "1";
  }
  if (isNew) body.aliases = [...draftAliases];
  if (modal?.dataset.resetAliases === "1") body.reset_aliases = true;
  const saveButton = button || $("commandSaveButton");
  const originalText = saveButton?.textContent || "Salvar alterações";
  if (saveButton) { saveButton.disabled = true; saveButton.textContent = "Salvando..."; }
  sn7ShowOperationLoader();
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
    sn7HideOperationLoader();
  }
}

async function addAlias(encodedKey) {
  const alias = $("v2alias")?.value.trim();
  if (!alias) return;
  sn7ShowOperationLoader();
  try {
    await apiJson(`/api/commands/${BROADCASTER_ID}/${encodedKey}/aliases`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ alias }),
    });
    document.querySelector(".sn7-command-modal")?.remove();
    await loadCommands(true);
    openCommand(encodedKey);
  } catch (error) {
    alert(error.message);
  } finally {
    sn7HideOperationLoader();
  }
}

async function removeAlias(encodedKey, encodedAlias) {
  sn7ShowOperationLoader();
  try {
    await apiJson(`/api/commands/${BROADCASTER_ID}/${encodedKey}/aliases`, {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ alias: decodeURIComponent(encodedAlias) }),
    });
    document.querySelector(".sn7-command-modal")?.remove();
    await loadCommands(true);
    openCommand(encodedKey);
  } catch (error) {
    alert(error.message);
  } finally {
    sn7HideOperationLoader();
  }
}

async function resetSystemCommandV2(encodedKey) {
  const key = decodeURIComponent(encodedKey);
  const command = commandCache.find((item) => item.command_key === key);
  if (!command || !command.is_system) return;

  const ok = await sn7ConfirmAction(
    "Redefinir configuração?",
    `A configuração de ${command.command} será restaurada para o padrão original do sistema. Clique em Salvar alterações para aplicar.`,
    "Continuar"
  );
  if (!ok) return;

  sn7ShowOperationLoader();
  try {
    const data = await apiJson(`/api/commands/${BROADCASTER_ID}/${encodedKey}/reset`, { method: "POST" });
    const defaults = data.default;
    if (!defaults) throw new Error("Não foi possível carregar o padrão do comando.");
    const currentModal = document.querySelector(".sn7-command-modal");
    if (currentModal) {
      currentModal.dataset.resetAliases = "1";
      currentModal.remove();
    }
    showCommand({
      ...command,
      command: defaults.command,
      description: defaults.description,
      response: defaults.response,
      enabled: true,
      aliases: [],
    }, false);
    const modal = document.querySelector(".sn7-command-modal");
    if (modal) modal.dataset.resetAliases = "1";
    setSaveMessage("commandSaveMsg", "Padrão carregado. Clique em Salvar alterações para aplicar.", true);
  } catch (error) {
    setSaveMessage("commandSaveMsg", `⚠ ${error.message}`, false);
  } finally {
    sn7HideOperationLoader();
  }
}


async function deleteCommandV2(encodedKey, isSystem, button) {
  button = button || document.querySelector(".sn7-system-action");
  const originalText = button ? button.textContent.trim() : "";

  if (button) {
    button.disabled = true;
    button.innerHTML = `<span class="sn7-spinner"></span>${isSystem ? "Atualizando..." : "Excluindo..."}`;
  }
  sn7ShowOperationLoader();

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
  } finally {
    sn7HideOperationLoader();
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
      "watch_points", "watch_interval_minutes", "sub_bonus", "kicks_bonus_per_kick", "bits_bonus_per_bit", "superchat_bonus_per_unit",
    ].forEach((key) => {
      if ($(key)) $(key).value = settings[key] ?? "";
    });
    updatePreview(settings);
    updateEconomyCards(settings);
    if (data.demo) setMessage("Modo demonstração: alterações não são persistidas.", false);
  } catch (error) {
    setMessage(`⚠ ${error.message}`, false);
  }
}

async function saveSettingsAndClose(modalId, button) {
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


function injectRankingStyles() {
  if ($("sn7-ranking-platform-style")) return;
  const style = document.createElement("style");
  style.id = "sn7-ranking-platform-style";
  style.textContent = `
    .sn7-ranking-platform-grid{display:grid;grid-template-columns:1fr;gap:14px;width:100%;max-width:980px}
    .sn7-ranking-platform-card{position:relative;width:100%;text-align:left;color:inherit;background:var(--panel);border:1px solid var(--border);border-radius:16px;padding:18px;cursor:pointer;min-height:118px;transition:transform .16s ease,border-color .16s ease,background .16s ease;box-sizing:border-box}
    .sn7-ranking-platform-card:hover{transform:translateY(-1px);border-color:#394253;background:#141923}
    .sn7-ranking-platform-card:active{transform:scale(.985)}
    .sn7-ranking-platform-card.sn7-ranking-card-loading{pointer-events:none}
    .sn7-ranking-platform-card.sn7-ranking-card-loading::after{content:"";position:absolute;inset:0;z-index:20;background:rgba(11,13,18,.46);backdrop-filter:blur(2px);border-radius:inherit}
    .sn7-ranking-platform-card.sn7-ranking-card-loading::before{content:"";position:absolute;left:50%;top:50%;width:27px;height:27px;margin:-13.5px 0 0 -13.5px;z-index:21;border:3px solid rgba(255,255,255,.18);border-top-color:#fff;border-radius:50%;animation:sn7RankingCardSpin .72s linear infinite;box-sizing:border-box}
    @keyframes sn7RankingCardSpin{to{transform:rotate(360deg)}}
    .sn7-ranking-platform-top{display:flex;align-items:center;gap:13px;margin-bottom:12px}
    .sn7-ranking-platform-top-info{min-width:0;flex:1}
    .sn7-ranking-platform-logo{width:42px;height:42px;border-radius:12px;display:grid;place-items:center;font-weight:900;font-size:19px;flex:0 0 42px;background:#1b202a;border:1px solid #303744;overflow:hidden}.sn7-ranking-platform-logo svg{width:32px;height:32px;display:block}
    .sn7-ranking-platform-card.kick .sn7-ranking-platform-logo{color:#53e88a}
    .sn7-ranking-platform-card.twitch .sn7-ranking-platform-logo{color:#b78cff}
    .sn7-ranking-platform-card.youtube .sn7-ranking-platform-logo{color:#ff6b73}
    .sn7-ranking-platform-name{font-weight:800;font-size:16px}
    .sn7-ranking-platform-sub{display:block;color:var(--muted);font-size:11px;margin-top:3px}
    .sn7-ranking-platform-reset{appearance:none;flex:0 0 auto;border:1px solid #63383b;background:transparent;color:#ff9c9c;border-radius:8px;padding:7px 9px;font-size:10px;font-weight:800;cursor:pointer;white-space:nowrap}
    .sn7-ranking-platform-reset:hover{background:rgba(239,68,68,.08);border-color:#8b4b50}
    .sn7-ranking-platform-reset:disabled{opacity:.55;cursor:wait}
    .sn7-ranking-mini{display:grid;gap:7px}
    .sn7-ranking-mini-row{display:flex;gap:8px;align-items:center;font-size:12px}
    .sn7-ranking-mini-pos{width:22px;color:#8f98a8;font-weight:800}
    .sn7-ranking-mini-user{min-width:0;flex:1;overflow-wrap:anywhere}
    .sn7-ranking-mini-points{font-weight:800;white-space:nowrap}
    .sn7-ranking-platform-empty{color:var(--muted);font-size:12px;padding:8px 0}
    .sn7-ranking-platform-loading{color:var(--muted);font-size:12px;padding:8px 0}
    .sn7-platform-ranking-modal{position:fixed;inset:0;z-index:2147483200;background:rgba(3,5,9,.78);backdrop-filter:blur(7px);display:flex;align-items:center;justify-content:center;padding:16px;box-sizing:border-box}
    .sn7-platform-ranking-card{position:relative;width:min(620px,100%);max-height:calc(100svh - 32px);overflow:auto;background:#11151d;border:1px solid #2a303c;border-radius:18px;padding:22px;box-shadow:0 24px 70px rgba(0,0,0,.5);box-sizing:border-box}
    .sn7-platform-ranking-close{position:absolute;right:10px;top:9px;width:36px;height:36px;border:0;border-radius:10px;background:transparent;color:#8f98a8;font-size:25px;cursor:pointer}
    .sn7-platform-ranking-close:hover{background:#1b202a;color:#fff}
    .sn7-platform-ranking-head{display:flex;align-items:center;gap:11px;padding-right:38px;margin-bottom:16px}
    .sn7-platform-ranking-head h3{margin:0;font-size:21px}
    .sn7-platform-ranking-head p{margin:4px 0 0;color:var(--muted);font-size:12px}
    .sn7-platform-ranking-list{display:grid;gap:9px}
    @media(max-width:600px){.sn7-ranking-platform-card{min-height:96px;padding:16px}.sn7-ranking-platform-top{margin-bottom:10px}.sn7-ranking-platform-reset{padding:7px 9px}}
    @media(prefers-reduced-motion:reduce){.sn7-ranking-platform-card.sn7-ranking-card-loading::before{animation:none}}
  `;
  document.head.appendChild(style);
}

function rankingPlatformIcon(platform) {
  if (platform === "kick") return '<svg viewBox="0 0 42 42" aria-hidden="true"><rect x="4" y="4" width="34" height="34" rx="10" fill="currentColor"/><path d="M13 11h5v8l7-8h6l-8 9 8 11h-6l-7-8v8h-5z" fill="#0b0d12"/></svg>';
  if (platform === "twitch") return '<svg viewBox="0 0 42 42" aria-hidden="true"><path d="M8 5h27v23l-8 7h-7l-5 4v-4H8z" fill="currentColor"/><path d="M13 10h18v14l-5 5h-6l-4 3v-3h-3z" fill="#11151d"/><path d="M18 13h3v8h-3zm7 0h3v8h-3z" fill="currentColor"/></svg>';
  return '<svg viewBox="0 0 42 42" aria-hidden="true"><rect x="4" y="8" width="34" height="26" rx="8" fill="currentColor"/><path d="M17 14l11 7-11 7z" fill="#11151d"/></svg>';
}

function rankingPlatformLabel(platform) {
  return platform === "kick" ? "Kick" : platform === "twitch" ? "Twitch" : "YouTube";
}

function renderRankingRows(rows, currency, compact = false) {
  if (!rows.length) return `<div class="${compact ? "sn7-ranking-platform-empty" : "sn7-ranking-empty"}">Nenhum usuário com pontos ainda.</div>`;
  const visible = compact ? rows.slice(0, 3) : rows;
  return visible.map((item) => `
    <div class="${compact ? "sn7-ranking-mini-row" : "sn7-ranking-row"}">
      <div class="${compact ? "sn7-ranking-mini-pos" : "sn7-ranking-position"}">#${esc(item.position)}</div>
      <div class="${compact ? "sn7-ranking-mini-user" : "sn7-ranking-user"}">${esc(item.username)}</div>
      <div class="${compact ? "sn7-ranking-mini-points" : "sn7-ranking-points"}">${Number(item.points || 0).toLocaleString("pt-BR")} ${esc(currency)}</div>
    </div>
  `).join("");
}

async function openPlatformRanking(platform) {
  injectRankingStyles();
  const modal = document.createElement("div");
  modal.className = "sn7-platform-ranking-modal";
  modal.innerHTML = `
    <div class="sn7-platform-ranking-card" role="dialog" aria-modal="true">
      <button type="button" class="sn7-platform-ranking-close" aria-label="Fechar">×</button>
      <div class="sn7-platform-ranking-head">
        <span class="sn7-ranking-platform-logo">${rankingPlatformIcon(platform)}</span>
        <div><h3>Ranking ${rankingPlatformLabel(platform)}</h3><p>Pontos exclusivos desta plataforma.</p></div>
      </div>
      <div class="sn7-platform-ranking-list"><div class="sn7-ranking-loading">Carregando ranking...</div></div>
    </div>`;
  document.body.appendChild(modal);
  const close = () => modal.remove();
  modal.querySelector(".sn7-platform-ranking-close").onclick = close;
  modal.onclick = (event) => { if (event.target === modal) close(); };
  try {
    const data = await apiJson(`/api/ranking/${BROADCASTER_ID}?limit=50&platform=${encodeURIComponent(platform)}`);
    const rows = Array.isArray(data.ranking) ? data.ranking : [];
    modal.querySelector(".sn7-platform-ranking-list").innerHTML = renderRankingRows(rows, data.currency || "Pontos");
  } catch (error) {
    modal.querySelector(".sn7-platform-ranking-list").innerHTML = `<div class="sn7-ranking-empty">⚠ ${esc(error.message)}</div>`;
  }
}

function renderRankingCards(data) {
  const list = $("sn7RankingList");
  if (!list) return;
  const rankings = data?.rankings || {};
  const currency = data?.currency || "Pontos";
  const platforms = ["kick", "twitch", "youtube"];
  list.innerHTML = platforms.map((platform) => {
    const rows = Array.isArray(rankings[platform]) ? rankings[platform] : [];
    return `
      <article class="sn7-ranking-platform-card ${platform}" role="button" tabindex="0"
        onclick="openPlatformRanking('${platform}')"
        onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();openPlatformRanking('${platform}')}">
        <div class="sn7-ranking-platform-top">
          <span class="sn7-ranking-platform-logo">${rankingPlatformIcon(platform)}</span>
          <div class="sn7-ranking-platform-top-info">
            <div class="sn7-ranking-platform-name">${rankingPlatformLabel(platform)}</div>
            <span class="sn7-ranking-platform-sub">Ranking e pontos da plataforma</span>
          </div>
          <button type="button" class="sn7-ranking-platform-reset"
            onclick="event.stopPropagation();resetPlatformRanking('${platform}',this)"
            onkeydown="event.stopPropagation()"
            aria-label="Resetar pontos da ${rankingPlatformLabel(platform)}">↻ Resetar</button>
        </div>
        <div class="sn7-ranking-mini">${renderRankingRows(rows, currency, true)}</div>
      </article>
    `;
  }).join("");
}

let sn7RankingLoadPromise = null;
async function loadRanking(silent = false) {
  const list = $("sn7RankingList");
  if (!list) return;
  if (sn7RankingLoadPromise) return sn7RankingLoadPromise;
  injectRankingStyles();
  if (!silent) list.innerHTML = '<div class="sn7-ranking-loading">Carregando rankings...</div>';
  sn7RankingLoadPromise = (async () => {
    try {
      const data = await apiJson(`/api/ranking/${BROADCASTER_ID}/all?limit=50`);
      renderRankingCards(data);
    } catch (error) {
      if (!silent || !list.querySelector(".sn7-ranking-platform-card")) {
        list.innerHTML = `<div class="sn7-ranking-empty">⚠ ${esc(error.message)}</div>`;
      }
    } finally {
      sn7RankingLoadPromise = null;
    }
  })();
  return sn7RankingLoadPromise;
}

let sn7RankingPollTimer = null;
function startRankingPolling() {
  if (sn7RankingPollTimer) return;
  sn7RankingPollTimer = setInterval(() => {
    const tab = document.querySelector('nav button[data-tab="ranking"]');
    if (!tab || !tab.classList.contains("active")) {
      stopRankingPolling();
      return;
    }
    loadRanking(true).catch(() => {});
  }, 2000);
}
function stopRankingPolling() {
  if (sn7RankingPollTimer) {
    clearInterval(sn7RankingPollTimer);
    sn7RankingPollTimer = null;
  }
}

async function resetPlatformRanking(platform, button) {
  const label = rankingPlatformLabel(platform);
  const ok = await sn7ConfirmAction(
    `Resetar pontos da ${label}?`,
    `Todos os pontos dos usuários da ${label} serão zerados. Os rankings das outras plataformas continuarão intactos.`,
    "Resetar"
  );
  if (!ok) return;

  const card = button?.closest(".sn7-ranking-platform-card");
  if (card) {
    card.classList.add("sn7-ranking-card-loading");
    card.setAttribute("aria-busy", "true");
  }
  if (button) button.disabled = true;
  try {
    await apiJson(`/api/ranking/${BROADCASTER_ID}/reset?platform=${encodeURIComponent(platform)}`, { method: "POST" });
    await loadRanking(true);
  } catch (error) {
    alert(error.message);
  } finally {
    if (card && document.body.contains(card)) {
      card.classList.remove("sn7-ranking-card-loading");
      card.removeAttribute("aria-busy");
    }
    if (button && document.body.contains(button)) button.disabled = false;
  }
}

async function saveSettings() {
  const data = {};
  [
    "currency_name", "currency_command", "currency_emoji", "points_response",
    "rank_title", "rank_limit", "duel_win_points", "duel_loss_points",
    "watch_points", "watch_interval_minutes", "sub_bonus", "kicks_bonus_per_kick", "bits_bonus_per_bit", "superchat_bonus_per_unit",
  ].forEach((key) => {
    if ($(key)) data[key] = $(key).value;
  });

  sn7ShowOperationLoader();
  try {
    const result = await apiJson(`/api/settings/${BROADCASTER_ID}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    const saved = result.settings || {};
    Object.assign(saved, saved.point_rewards || {});
    [
      "currency_name", "currency_command", "currency_emoji", "points_response",
      "rank_title", "rank_limit", "duel_win_points", "duel_loss_points",
      "watch_points", "watch_interval_minutes", "sub_bonus", "kicks_bonus_per_kick", "bits_bonus_per_bit", "superchat_bonus_per_unit",
    ].forEach((key) => {
      if ($(key) && Object.prototype.hasOwnProperty.call(saved, key)) $(key).value = saved[key] ?? "";
    });
    updatePreview(saved);
    updateEconomyCards(saved);
    renderCommands();
    setMessage("✓ Alterações salvas.", true);
    setSaveMessage("rewardsMsg", "✓ Alterações salvas.", true);
    await loadCommands(true);
    return true;
  } catch (error) {
    setMessage(`⚠ ${error.message}`, false);
    return false;
  } finally {
    sn7HideOperationLoader();
  }
}

document.addEventListener("DOMContentLoaded", () => {
  injectCommandStyles();

  // Registra a navegação antes de restaurar a aba.
  // Assim, F5 no Android mantém a última tela aberta.
  setupTabPersistence();
  restoreSavedTab();

  ["currency_name", "currency_emoji", "currency_command", "points_response", "watch_points", "watch_interval_minutes", "sub_bonus", "kicks_bonus_per_kick", "bits_bonus_per_bit", "superchat_bonus_per_unit"].forEach((id) => {
    $(id)?.addEventListener("input", () => {
      updatePointsResponsePreview();
      updateEconomyCards();
      renderCommands();
    });
  });

  // Boot rápido: dados essenciais começam em paralelo. O loader tem
  // no máximo 1,2s; se o Render demorar, a interface aparece e as respostas
  // continuam chegando em segundo plano, sem bloquear a navegação.
  requestAnimationFrame(() => {
    const bootTasks = [
      loadSettings(),
      loadCommands(),
      loadRanking(),
    ];

    const allReady = Promise.allSettled(bootTasks);
    const maxBoot = new Promise((resolve) => setTimeout(resolve, 1200));

    Promise.race([allReady, maxBoot]).then(() => {
      sn7Booting = false;
      sn7HideBootLoader();
      const activeTab = document.querySelector('nav button[data-tab].active')?.dataset.tab;
      if (activeTab === "music" && typeof window.loadMusic === "function") window.loadMusic().catch(() => {});
      if (activeTab === "ranking" && typeof startRankingPolling === "function") startRankingPolling();
      if (activeTab === "profile" && typeof window.sn7LoadProfile === "function") window.sn7LoadProfile().catch(() => {});
      if (typeof window.sn7RestoreSavedModal === "function") {
        window.sn7RestoreSavedModal().catch(() => {});
      }
    });
  });
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
    try { sessionStorage.setItem(SN7_ACTIVE_MODAL_KEY, id); } catch (_) {}
    syncBodyLock();
    const card = el.querySelector(".sn7-config-modal-card");
    if (card) card.scrollTop = 0;
    return true;
  }

  function closeModal(id) {
    const el = $(id);
    if (!el) return false;
    el.hidden = true;
    try {
      if (sessionStorage.getItem(SN7_ACTIVE_MODAL_KEY) === id) sessionStorage.removeItem(SN7_ACTIVE_MODAL_KEY);
    } catch (_) {}
    syncBodyLock();
    return true;
  }

  window.sn7CloseAllModals = function () {
    document.querySelectorAll(".sn7-config-modal").forEach((item) => {
      if (!item.hidden) closeModal(item.id);
    });
    try { sessionStorage.removeItem(SN7_ACTIVE_MODAL_KEY); } catch (_) {}
  };

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


  window.sn7RestoreSavedModal = async function () {
    try {
      const id = sessionStorage.getItem(SN7_ACTIVE_MODAL_KEY);
      if (!id) return;

      if (id === "sn7ApostaEditor") {
        openModal(id);
        await loadApostaSettings();
      } else if (id === "sn7PointsEditor" || id === "sn7RewardsEditor") {
        openModal(id);
      } else if (id.startsWith("command:")) {
        const key = id.slice(8);
        if (key && key !== "new") {
          if (!commandCache.length) {
            await loadCommands(true);
          }
          const encoded = encodeURIComponent(key);
          setTimeout(() => openCommand(encoded), 80);
        }
      }
    } catch (_) {}
  };

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
      const commands = Array.isArray(commandCache) && commandCache.length
        ? commandCache
        : await loadCommands(true);
      const cmd = (commands || []).find((x) => x.command_key === "duel");

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
      "Os campos voltarão ao padrão original. Nada será salvo até você clicar em Salvar alterações.",
      "Continuar"
    );
    if (!ok) return;

    sn7ShowOperationLoader();
    try {
      const defaults = {
        currency_name: "Pontos",
        currency_command: "!pontos",
        currency_emoji: "",
        points_response: "$(user), você tem $(points) $(currency).$(emoji_text)$(rank_text)",
      };
      Object.entries(defaults).forEach(([key, value]) => { if ($(key)) $(key).value = value; });
      updatePreview(defaults);
      updateEconomyCards(defaults);
      if ($("settingsMsg")) setSaveMessage("settingsMsg", "Padrão carregado. Clique em Salvar alterações para aplicar.", true);
    } finally {
      sn7HideOperationLoader();
    }
  }

  async function resetRewardsSettings() {
    const ok = await sn7ConfirmAction(
      "Redefinir recompensas?",
      "Os valores voltarão ao padrão original. Nada será salvo até você clicar em Salvar alterações.",
      "Continuar"
    );
    if (!ok) return;

    sn7ShowOperationLoader();
    try {
      const defaults = { watch_points: "1", watch_interval_minutes: "10", sub_bonus: "500", kicks_bonus_per_kick: "1" };
      Object.entries(defaults).forEach(([key, value]) => { if ($(key)) $(key).value = value; });
      updateEconomyCards();
      setSaveMessage("rewardsMsg", "Padrão carregado. Clique em Salvar alterações para aplicar.", true);
    } finally {
      sn7HideOperationLoader();
    }
  }

  async function resetApostaSettings() {
    const ok = await sn7ConfirmAction(
      "Redefinir configuração da aposta?",
      "O comando e a mensagem voltarão ao padrão original. Nada será salvo até você clicar em Salvar alterações.",
      "Continuar"
    );
    if (!ok) return;

    sn7ShowOperationLoader();
    try {
      if ($("aposta_command")) $("aposta_command").value = "!aposta";
      if ($("aposta_response")) $("aposta_response").value = "$(user) está apostando $(amount) $(currency) contra $(target). Digite $(accept_command) ou $(decline_command).";
      if ($("apostaCardCommand")) $("apostaCardCommand").textContent = "!aposta";
      setSaveMessage("apostaMsg", "Padrão carregado. Clique em Salvar alterações para aplicar.", true);
    } finally {
      sn7HideOperationLoader();
    }
  }

  window.resetPointsSettings = resetPointsSettings;
  window.resetRewardsSettings = resetRewardsSettings;
  window.resetApostaSettings = resetApostaSettings;

  async function saveApostaSettings() {
    const msg = $("apostaMsg");
    sn7ShowOperationLoader();
    try {
      const command = $("aposta_command")?.value.trim() || "!aposta";
      const response = $("aposta_response")?.value.trim() || "$(duel_result)";
      await apiJson(`/api/commands/${BROADCASTER_ID}/duel`, {
        method: "PATCH",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({command, response})
      });
      await loadCommands(true);
      if ($("apostaCardCommand")) $("apostaCardCommand").textContent = command;
      setSaveMessage("apostaMsg", "✓ Alterações salvas.", true);
      if ($("apostaMsg")) $("apostaMsg").textContent = "✓ Alterações salvas.";
    } catch (e) {
      if (msg) msg.textContent = "⚠ " + e.message;
    } finally {
      sn7HideOperationLoader();
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

/* SN7 MUSIC PLAYER V2 - compacto e leve */
let sn7MusicData = null;
let sn7MusicLoadPromise = null;
let sn7MusicAudio = null;
let sn7MusicVolumeSaveTimer = null;
let sn7MusicConnectionsData = null;
let sn7MusicConnectionsPromise = null;

function musicHasChannel() {
  return typeof BROADCASTER_ID !== "undefined" && BROADCASTER_ID !== null && BROADCASTER_ID !== "";
}

function musicApi(path, options = {}) {
  if (!musicHasChannel()) throw new Error("Conecte sua conta Kick para editar o player.");
  return apiJson(`/api/music/${BROADCASTER_ID}${path}`, options);
}

function musicConnectionsApi() {
  if (!musicHasChannel()) throw new Error("Conecte sua conta Kick para vincular plataformas.");
  return apiJson(`/api/music/${BROADCASTER_ID}/connections`);
}


let sn7SpotifyPlayer = null;
let sn7SpotifyDeviceId = null;
let sn7SpotifyReadyPromise = null;
let sn7SpotifyCurrentUri = "";

function ensureSpotifySDK() {
  if (window.Spotify && window.Spotify.Player) return Promise.resolve();
  if (sn7SpotifyReadyPromise) return sn7SpotifyReadyPromise;
  sn7SpotifyReadyPromise = new Promise((resolve, reject) => {
    const previous = window.onSpotifyWebPlaybackSDKReady;
    window.onSpotifyWebPlaybackSDKReady = () => {
      try { if (typeof previous === "function") previous(); } catch (_) {}
      resolve();
    };
    const script = document.createElement("script");
    script.src = "https://sdk.scdn.co/spotify-player.js";
    script.async = true;
    script.onerror = () => {
      sn7SpotifyReadyPromise = null;
      reject(new Error("Não foi possível carregar o player do Spotify."));
    };
    document.head.appendChild(script);
  });
  return sn7SpotifyReadyPromise;
}

async function ensureSpotifyPlayer() {
  if (sn7SpotifyPlayer && sn7SpotifyDeviceId) return sn7SpotifyPlayer;
  await ensureSpotifySDK();
  if (sn7SpotifyPlayer && sn7SpotifyDeviceId) return sn7SpotifyPlayer;

  const data = await musicApi("/spotify/player-token");
  if (!data?.token) throw new Error("Spotify não retornou um token de reprodução.");

  sn7SpotifyPlayer = new Spotify.Player({
    name: "SN7 Core Music Player",
    getOAuthToken: async (cb) => {
      try {
        const fresh = await musicApi("/spotify/player-token");
        cb(fresh.token);
      } catch (_) {
        cb(data.token);
      }
    },
    volume: Number(sn7MusicData?.state?.volume ?? 80) / 100
  });

  sn7SpotifyPlayer.addListener("ready", ({device_id}) => {
    sn7SpotifyDeviceId = device_id;
  });
  sn7SpotifyPlayer.addListener("not_ready", ({device_id}) => {
    if (device_id === sn7SpotifyDeviceId) sn7SpotifyDeviceId = null;
  });
  sn7SpotifyPlayer.addListener("initialization_error", ({message}) => {
    const source = $("sn7MusicSourceStatus");
    if (source) source.textContent = `⚠ Spotify: ${message}`;
  });
  sn7SpotifyPlayer.addListener("authentication_error", ({message}) => {
    const source = $("sn7MusicSourceStatus");
    if (source) source.textContent = `⚠ Spotify: ${message}`;
  });
  sn7SpotifyPlayer.addListener("account_error", ({message}) => {
    const source = $("sn7MusicSourceStatus");
    if (source) source.textContent = `⚠ Spotify: ${message || "Conta não elegível"} (Premium necessário)`;
  });
  sn7SpotifyPlayer.addListener("autoplay_failed", () => {
    const source = $("sn7MusicSourceStatus");
    if (source) source.textContent = "⚠ O navegador bloqueou a reprodução. Toque em Reproduzir novamente.";
  });
  sn7SpotifyPlayer.addListener("playback_error", ({message}) => {
    const source = $("sn7MusicSourceStatus");
    if (source) source.textContent = `⚠ Spotify: ${message}`;
  });
  sn7SpotifyPlayer.addListener("player_state_changed", (state) => {
    if (!state) return;
    const playing = !state.paused;
    if (sn7MusicData?.state) sn7MusicData.state.is_playing = playing;
    musicRenderPlaying(playing);
  });

  const connected = await new Promise((resolve, reject) => {
    let done = false;
    const finish = (ok) => {
      if (done) return;
      done = true;
      ok ? resolve(true) : reject(new Error("O dispositivo do Spotify não ficou pronto."));
    };
    const onReady = ({device_id}) => {
      sn7SpotifyDeviceId = device_id;
      finish(true);
    };
    sn7SpotifyPlayer.addListener("ready", onReady);
    sn7SpotifyPlayer.connect().then(ok => {
      if (!ok) finish(false);
    }).catch(() => finish(false));
    setTimeout(() => finish(false), 10000);
  });

  return connected ? sn7SpotifyPlayer : sn7SpotifyPlayer;
}

async function musicPlaySpotify(current) {
  const uri = String(current?.source_url || "").trim();
  if (!/^spotify:track:[A-Za-z0-9]+$/.test(uri)) {
    throw new Error("Faixa do Spotify inválida.");
  }
  const player = await ensureSpotifyPlayer();
  if (typeof player.activateElement === "function") {
    try { await player.activateElement(); } catch (_) {}
  }
  if (!sn7SpotifyDeviceId) throw new Error("Spotify ainda está preparando o dispositivo. Tente novamente.");
  const tokenData = await musicApi("/spotify/player-token");
  const response = await fetch(`https://api.spotify.com/v1/me/player/play?device_id=${encodeURIComponent(sn7SpotifyDeviceId)}`, {
    method: "PUT",
    headers: {"Authorization": `Bearer ${tokenData.token}`, "Content-Type": "application/json"},
    body: JSON.stringify({uris: [uri]})
  });
  if (!response.ok && response.status !== 204) {
    let detail = "";
    try { detail = (await response.json())?.error?.message || ""; } catch (_) {}
    throw new Error(detail || `Spotify recusou a reprodução (HTTP ${response.status}).`);
  }
  sn7SpotifyCurrentUri = uri;
  await player.resume();
}

function ensureMusicAudio() {
  if (sn7MusicAudio) return sn7MusicAudio;
  sn7MusicAudio = new Audio();
  sn7MusicAudio.preload = "metadata";
  sn7MusicAudio.addEventListener("timeupdate", musicRenderProgress);
  sn7MusicAudio.addEventListener("loadedmetadata", musicRenderProgress);
  sn7MusicAudio.addEventListener("ended", () => musicSkip(true));
  sn7MusicAudio.addEventListener("play", () => musicRenderPlaying(true));
  sn7MusicAudio.addEventListener("pause", () => musicRenderPlaying(false));
  return sn7MusicAudio;
}

function musicFormatTime(value) {
  const seconds = Math.max(0, Number(value) || 0);
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60).toString().padStart(2, "0");
  return `${m}:${s}`;
}

function musicRenderPlaying(playing) {
  const btn = $("sn7MusicPlay");
  if (btn) {
    btn.classList.toggle("is-playing", !!playing);
    btn.innerHTML = playing
      ? '<span class="sn7-pause-icon" aria-hidden="true"><i></i><i></i></span>'
      : '<span class="sn7-play-icon" aria-hidden="true"></span>';
    btn.setAttribute("aria-label", playing ? "Pausar" : "Reproduzir");
  }
  if (sn7MusicData?.state) sn7MusicData.state.is_playing = playing;
}

function musicRenderProgress() {
  const audio = sn7MusicAudio;
  if (!audio) return;
  const duration = Number(audio.duration) || 0;
  const current = Number(audio.currentTime) || 0;
  const bar = $("sn7MusicProgressBar");
  if (bar) bar.style.width = duration ? `${Math.min(100, current / duration * 100)}%` : "0%";
  if ($("sn7MusicElapsed")) $("sn7MusicElapsed").textContent = musicFormatTime(current);
  if ($("sn7MusicDuration")) $("sn7MusicDuration").textContent = duration ? musicFormatTime(duration) : "—";
}

function musicRenderVolume(value) {
  const volume = Math.max(0, Math.min(100, Number(value) || 0));
  if ($("sn7MusicVolumeValue")) $("sn7MusicVolumeValue").textContent = String(volume);
  const icon = $("sn7MusicVolumeIcon");
  if (icon) {
    icon.classList.toggle("is-muted", volume === 0);
    icon.classList.toggle("is-low", volume > 0 && volume <= 30);
    icon.classList.toggle("is-medium", volume > 30 && volume <= 70);
    icon.classList.toggle("is-high", volume > 70);
  }
  document.querySelectorAll(".sn7-volume-btn").forEach((button) => {
    const label = button.getAttribute("aria-label") || "";
    button.disabled = (volume === 0 && label.includes("Diminuir")) || (volume === 100 && label.includes("Aumentar"));
  });
}

function musicRenderConnectionsLoading() {
  const providers = [
    ["youtube", "Youtube"],
    ["spotify", "Spotify"],
    ["soundcloud", "Soundcloud"],
  ];
  providers.forEach(([provider, key]) => {
    const status = $(`music${key}Status`);
    const account = $(`music${key}Account`);
    const button = $(`music${key}Connect`);
    if (status) {
      status.classList.remove("connected", "disconnected");
      status.classList.add("neutral");
      status.textContent = "…";
      status.setAttribute("aria-label", "Verificando conexão");
    }
    if (account) account.textContent = "Verificando conexão…";
    if (button) {
      button.textContent = "Verificando…";
      button.disabled = true;
      button.dataset.connected = "0";
      button.dataset.configured = "0";
    }
  });
}

function musicRenderConnections(data) {
  const connections = data?.connections || {};
  sn7MusicConnectionsData = data || null;
  const providers = [
    ["youtube", "Youtube"],
    ["spotify", "Spotify"],
    ["soundcloud", "Soundcloud"],
  ];

  providers.forEach(([provider, key]) => {
    const item = connections[provider] || {};
    const status = $(`music${key}Status`);
    const account = $(`music${key}Account`);
    const button = $(`music${key}Connect`);
    const avatar = $(`music${key}Avatar`);
    const fallback = provider === "youtube" ? "▶" : provider === "spotify" ? "●" : "◉";
    if (status) {
      status.classList.remove("neutral");
      status.classList.toggle("connected", !!item.connected);
      status.classList.toggle("disconnected", !item.connected);
      status.textContent = item.connected ? "✓" : "✕";
      status.setAttribute("aria-label", item.connected ? "Conta conectada" : "Conta desconectada");
    }
    if (account) {
      account.textContent = item.connected
        ? (item.display_name || item.username || "Conta conectada")
        : (item.configured ? "Conta não conectada" : "OAuth ainda não configurado");
    }
    if (avatar) {
      avatar.classList.toggle("has-avatar", !!item.avatar_url);
      avatar.innerHTML = item.avatar_url
        ? `<img src="${String(item.avatar_url).replace(/"/g, "&quot;")}" alt="" loading="lazy">`
        : `<span>${fallback}</span>`;
    }
    if (button) {
      button.textContent = item.connected ? "Desconectar" : "Conectar";
      button.classList.remove("ghost");
      button.disabled = false;
      button.dataset.connected = item.connected ? "1" : "0";
      button.dataset.configured = item.configured ? "1" : "0";
    }
  });

  musicRenderToggleFeedback();
}

async function loadMusicConnections(force = false) {
  if (!musicHasChannel()) {
    const data = {connections:{
      youtube:{configured:false,connected:false},
      spotify:{configured:false,connected:false},
      soundcloud:{configured:false,connected:false}
    }};
    musicRenderConnections(data);
    return data;
  }
  if (!force && sn7MusicConnectionsData) {
    musicRenderConnections(sn7MusicConnectionsData);
    return sn7MusicConnectionsData;
  }
  if (!sn7MusicConnectionsPromise) {
    sn7MusicConnectionsPromise = musicConnectionsApi()
      .then((data) => {
        sn7MusicConnectionsData = data;
        musicRenderConnections(data);
        return data;
      })
      .catch((error) => {
        sn7MusicConnectionsPromise = null;
        const msg = $("musicConfigMsg");
        if (msg) {
          msg.textContent = `⚠ ${error.message}`;
          msg.className = "sn7-save-message error";
        }
        throw error;
      });
  }
  return sn7MusicConnectionsPromise;
}

function musicConnect(provider) {
  if (!musicHasChannel()) {
    const msg = $("musicConfigMsg");
    if (msg) {
      msg.textContent = "Faça login com a Kick para conectar plataformas.";
      msg.className = "sn7-save-message error";
    }
    return;
  }
  const button = $(`music${String(provider).charAt(0).toUpperCase() + String(provider).slice(1)}Connect`);
  if (button?.dataset.connected === "1") {
    musicDisconnect(provider);
    return;
  }
  if (button?.dataset.configured !== "1") {
    const labels = {youtube:"YouTube", spotify:"Spotify", soundcloud:"SoundCloud"};
    const msg = $("musicConfigMsg");
    if (msg) {
      msg.textContent = `⚠ ${labels[provider] || provider} ainda não foi configurado no Render.`;
      msg.className = "sn7-save-message error";
    }
    return;
  }
  button?.classList.add("is-connecting");
  if (button) {
    button.disabled = true;
    button.textContent = "Abrindo…";
  }
  const card = button?.closest(".sn7-source-card");
  card?.classList.add("is-connecting");
  window.location.href = `/api/music/${BROADCASTER_ID}/connect/${encodeURIComponent(provider)}`;
}

async function musicDisconnect(provider) {
  if (!musicHasChannel()) return;
  const label = provider === "youtube" ? "YouTube" : provider === "spotify" ? "Spotify" : "SoundCloud";
  if (!confirm(`Desconectar ${label} do SN7?`)) return;
  try {
    const data = await apiJson(`/api/music/${BROADCASTER_ID}/disconnect/${encodeURIComponent(provider)}`, {method:"POST"});
    musicRenderConnections(data);
    sn7MusicConnectionsPromise = Promise.resolve(data);
  } catch (error) {
    const msg = $("musicConfigMsg");
    if (msg) {
      msg.textContent = `⚠ ${error.message}`;
      msg.className = "sn7-save-message error";
    }
  }
}

function musicRender(data) {
  sn7MusicData = data || {settings:{}, state:{}, current:null, queue:[]};
  const current = sn7MusicData.current;
  const queue = Array.isArray(sn7MusicData.queue) ? sn7MusicData.queue : [];
  const title = $("sn7MusicTitle");
  const artist = $("sn7MusicArtist");
  const source = $("sn7MusicSourceStatus");
  const art = $("sn7MusicArt");
  const volume = Number(sn7MusicData.state?.volume ?? 80);

  if (title) title.textContent = current?.title || "Nenhuma música";
  if (artist) artist.textContent = current?.artist || (queue.length ? "Pronta para a próxima reprodução." : "A fila está pronta para receber músicas.");
  if (art) art.textContent = current ? "♫" : "♪";
  if (source) {
    if (!current) source.textContent = "Player pronto";
    else if (current.source_url) source.textContent = `Fonte: ${String(current.provider || "link").toUpperCase()}`;
    else source.textContent = "Aguardando fonte de reprodução autorizada";
  }

  const count = $("sn7MusicQueueCount");
  if (count) count.textContent = String(queue.length);

  musicRenderVolume(volume);
  musicRenderPlaying(Boolean(sn7MusicData.state?.is_playing));
  renderMusicQueue(queue);

  const audio = ensureMusicAudio();
  const url = current?.source_url || "";
  const provider = String(current?.provider || "").toLowerCase();
  if (provider === "spotify" && /^spotify:track:[A-Za-z0-9]+$/.test(url)) {
    audio.pause();
    audio.removeAttribute("src");
    audio.load();
    if (sn7SpotifyPlayer) {
      sn7SpotifyPlayer.setVolume(volume / 100).catch(() => {});
    }
  } else if (url && /^https?:\/\//i.test(url) && /\.(mp3|m4a|aac|ogg|wav|opus)(\?.*)?$/i.test(url)) {
    if (audio.src !== url) {
      audio.src = url;
      audio.volume = volume / 100;
    }
  } else if (!url) {
    audio.pause();
    audio.removeAttribute("src");
    audio.load();
  } else {
    audio.pause();
    audio.removeAttribute("src");
    audio.load();
    audio.volume = volume / 100;
  }
  musicRenderProgress();
}

function renderMusicQueue(queue) {
  const box = $("sn7MusicQueue");
  if (!box) return;
  if (!queue.length) {
    box.innerHTML = `<div class="sn7-music-empty">Nenhuma música adicionada ainda.</div>`;
    return;
  }
  box.innerHTML = queue.map((item, index) => `
    <div class="sn7-music-row">
      <span class="sn7-music-number">${index + 1}</span>
      <div class="sn7-music-row-info">
        <strong>${esc(item.title)}</strong>
        <small>${esc(item.artist || "Artista não informado")} · ${esc(item.added_by || "chat")}</small>
      </div>
      <span class="sn7-music-provider">${esc(item.provider || "link")}</span>
      <button type="button" class="sn7-music-remove" onclick="removeMusicItem(${Number(item.id)})" aria-label="Remover ${esc(item.title)}">×</button>
    </div>`).join("");
}

let sn7MusicQueuePollTimer = null;

function startMusicQueuePolling() {
  if (sn7MusicQueuePollTimer) return;
  // O chat do bot atualiza a fila no banco imediatamente. Mantemos o painel
  // sincronizado em ~1s enquanto a aba Música estiver aberta, em vez de
  // depender de uma nova navegação/modal para buscar o estado.
  sn7MusicQueuePollTimer = setInterval(() => {
    const musicTab = document.querySelector('nav button[data-tab="music"]');
    if (!musicTab || !musicTab.classList.contains("active")) {
      stopMusicQueuePolling();
      return;
    }
    loadMusic().catch(() => {});
  }, 1000);
}

function stopMusicQueuePolling() {
  if (sn7MusicQueuePollTimer) {
    clearInterval(sn7MusicQueuePollTimer);
    sn7MusicQueuePollTimer = null;
  }
}

async function loadMusic() {
  if (!musicHasChannel()) {
    const data = {
      ok: true,
      settings: {
        allow_youtube: true,
        allow_spotify: true,
        allow_soundcloud: false,
        allow_links: true,
        public_commands: false
      },
      state: {current_queue_id:null,is_playing:false,volume:80},
      current: null,
      queue: []
    };
    musicRender(data);
    return data;
  }
  if (sn7MusicLoadPromise) return sn7MusicLoadPromise;
  sn7MusicLoadPromise = musicApi("").then((data) => {
    musicRender(data);
    startMusicQueuePolling();
    return data;
  }).catch((error) => {
    const source = $("sn7MusicSourceStatus");
    if (source) source.textContent = "⚠ Não foi possível carregar o player";
    throw error;
  }).finally(() => { sn7MusicLoadPromise = null; });
  return sn7MusicLoadPromise;
}

async function musicTogglePlay() {
  const current = sn7MusicData?.current;
  if (!current) return;

  const provider = String(current.provider || "").toLowerCase();
  try {
    if (provider === "spotify" && /^spotify:track:[A-Za-z0-9]+$/.test(String(current.source_url || ""))) {
      if (sn7MusicData?.state?.is_playing) {
        if (sn7SpotifyPlayer) await sn7SpotifyPlayer.pause();
        const data = await musicApi("/state", {
          method:"PATCH",
          headers:{"Content-Type":"application/json"},
          body:JSON.stringify({is_playing:false})
        });
        musicRender(data);
      } else {
        await musicPlaySpotify(current);
        const data = await musicApi("/state", {
          method:"PATCH",
          headers:{"Content-Type":"application/json"},
          body:JSON.stringify({is_playing:true})
        });
        musicRender(data);
      }
      return;
    }

    const audio = ensureMusicAudio();
    if (audio.src) {
      if (audio.paused) {
        await audio.play();
        const data = await musicApi("/state", {
          method:"PATCH",
          headers:{"Content-Type":"application/json"},
          body:JSON.stringify({is_playing:true})
        });
        sn7MusicData = data;
        musicRenderPlaying(true);
      } else {
        audio.pause();
        const data = await musicApi("/state", {
          method:"PATCH",
          headers:{"Content-Type":"application/json"},
          body:JSON.stringify({is_playing:false})
        });
        sn7MusicData = data;
        musicRenderPlaying(false);
      }
      return;
    }

    const source = $("sn7MusicSourceStatus");
    if (source) {
      source.textContent = provider === "youtube"
        ? "YouTube precisa ser reproduzido pelo player do YouTube."
        : "Esta fonte não possui áudio reproduzível neste player.";
    }
  } catch (error) {
    const source = $("sn7MusicSourceStatus");
    if (source) source.textContent = `⚠ ${error.message || "Não foi possível iniciar a reprodução."}`;
    musicRenderPlaying(false);
  }
}

function musicChangeVolume(delta) {
  const current = Number(sn7MusicData?.state?.volume ?? 80);
  const volume = Math.max(0, Math.min(100, Math.round((current + Number(delta)) / 10) * 10));
  if (sn7MusicData?.state) sn7MusicData.state.volume = volume;
  const audio = ensureMusicAudio();
  audio.volume = volume / 100;
  if (sn7SpotifyPlayer) sn7SpotifyPlayer.setVolume(volume / 100).catch(() => {});
  musicRenderVolume(volume);

  clearTimeout(sn7MusicVolumeSaveTimer);
  sn7MusicVolumeSaveTimer = setTimeout(async () => {
    try {
      const data = await musicApi("/state", {
        method:"PATCH",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify({volume})
      });
      sn7MusicData = data;
      musicRenderVolume(Number(data.state?.volume ?? volume));
    } catch (_) {}
  }, 180);
}

async function musicSkip(fromAudio = false) {
  const current = sn7MusicData?.current;
  if (!current) return;
  const audio = ensureMusicAudio();
  audio.pause();
  audio.removeAttribute("src");
  audio.load();
  if (sn7SpotifyPlayer) {
    sn7SpotifyPlayer.pause().catch(() => {});
  }
  sn7SpotifyCurrentUri = "";

  const queue = Array.isArray(sn7MusicData.queue) ? sn7MusicData.queue : [];
  if (!queue.length) {
    try {
      musicRender(await musicApi("/state", {
        method:"PATCH",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify({is_playing:false})
      }));
    } catch (_) {}
    return;
  }

  try {
    const data = await musicApi("/skip", {method:"POST"});
    musicRender(data);
    const source = $("sn7MusicSourceStatus");
    if (source && data.current) source.textContent = `Próxima: ${data.current.title}`;
  } catch (_) {
    const source = $("sn7MusicSourceStatus");
    if (source) source.textContent = "⚠ Não foi possível avançar a fila";
  }
}

function musicPrevious() {
  const source = $("sn7MusicSourceStatus");
  if (source) source.textContent = "Histórico de reprodução ainda não está disponível.";
}

async function removeMusicItem(id) {
  if (!Number.isInteger(Number(id))) return;
  const button = document.querySelector(`.sn7-music-remove[onclick*="${Number(id)}"]`);
  if (button) {
    button.disabled = true;
    button.textContent = "…";
  }
  try {
    musicRender(await musicApi(`/queue/${Number(id)}/remove`, {method:"POST"}));
  } catch (_) {
    const msg = $("sn7MusicQueueMessage");
    if (msg) {
      msg.textContent = "Não foi possível remover a música.";
      msg.hidden = false;
      setTimeout(() => { msg.hidden = true; }, 2200);
    }
  }
}

async function clearMusicQueue() {
  if (!confirm("Limpar todas as músicas que estão na fila?")) return;
  const button = document.querySelector(".sn7-queue-clear");
  const card = document.querySelector(".sn7-music-queue-card");
  const loader = $("sn7MusicQueueLoading");
  if (button) {
    button.disabled = true;
    button.textContent = "Limpando...";
  }
  if (card) card.setAttribute("aria-busy", "true");
  if (loader) {
    loader.classList.add("open");
    loader.setAttribute("aria-hidden", "false");
  }
  try {
    musicRender(await musicApi("/queue/clear", {method:"POST"}));
  } catch (_) {
    const msg = $("sn7MusicQueueMessage");
    if (msg) {
      msg.textContent = "Não foi possível limpar a fila.";
      msg.hidden = false;
      setTimeout(() => { msg.hidden = true; }, 2200);
    }
  } finally {
    if (loader) {
      loader.classList.remove("open");
      loader.setAttribute("aria-hidden", "true");
    }
    if (card) card.removeAttribute("aria-busy");
    if (button) {
      button.disabled = false;
      button.textContent = "Limpar fila";
    }
  }
}

function openMusicQueue() {
  const modal = $("sn7MusicQueueModal");
  if (!modal) return;
  if (modal.parentElement !== document.body) document.body.appendChild(modal);
  modal.removeAttribute("hidden");
  document.body.classList.add("sn7-modal-open");
  loadMusic().catch(() => {});
  startMusicQueuePolling();
}

function closeMusicQueue(event) {
  if (event && event.target !== event.currentTarget) return;
  const modal = $("sn7MusicQueueModal");
  if (!modal) return;
  modal.setAttribute("hidden", "");
  document.body.classList.remove("sn7-modal-open");
  // A fila aberta é só uma das formas de visualizar a Música. Se a aba
  // Música continuar ativa, o painel principal também precisa permanecer
  // sincronizado depois de fechar o modal.
  const musicTab = document.querySelector('nav button[data-tab="music"]');
  if (musicTab && musicTab.classList.contains("active")) startMusicQueuePolling();
  else stopMusicQueuePolling();
}

function openMusicConfig() {
  const modal = $("sn7MusicConfig");
  if (!modal) return;
  if (modal.parentElement !== document.body) document.body.appendChild(modal);
  modal.removeAttribute("hidden");
  document.body.classList.add("sn7-modal-open");
  if (!sn7MusicConnectionsData) musicRenderConnectionsLoading();
  loadMusic().then(() => {
    const s = sn7MusicData?.settings || {};
    if ($("musicAllowYoutube")) $("musicAllowYoutube").checked = s.allow_youtube !== false;
    if ($("musicAllowSpotify")) $("musicAllowSpotify").checked = s.allow_spotify !== false;
    if ($("musicAllowSoundcloud")) $("musicAllowSoundcloud").checked = s.allow_soundcloud === true;
    if ($("musicAllowLinks")) $("musicAllowLinks").checked = s.allow_links === true;
    if ($("musicPublicCommands")) $("musicPublicCommands").checked = s.public_commands === true;
    musicRenderToggleFeedback();
  }).catch(() => {});
  loadMusicConnections().catch(() => {});
}

function closeMusicConfig(event) {
  if (event && event.target !== event.currentTarget) return;
  const modal = $("sn7MusicConfig");
  if (!modal) return;
  modal.setAttribute("hidden", "");
  document.body.classList.remove("sn7-modal-open");
}

function musicRenderToggleFeedback() {
  const link = $("musicAllowLinks");
  if (link) {
    link.classList.toggle("is-enabled", link.checked);
  }
  const pub = $("musicPublicCommands");
  const pubStatus = $("musicPublicStatus");
  if (pub && pubStatus) {
    pubStatus.textContent = pub.checked ? "✓" : "✕";
    pubStatus.classList.toggle("connected", pub.checked);
    pubStatus.classList.toggle("disconnected", !pub.checked);
  }
}

document.addEventListener("change", (event) => {
  if (event.target?.id === "musicAllowLinks" || event.target?.id === "musicPublicCommands") {
    musicRenderToggleFeedback();
  }
});

async function saveMusicConfig() {
  const msg = $("musicConfigMsg");
  if (msg) { msg.textContent = "Salvando..."; msg.className = "sn7-save-message"; }
  try {
    const data = await musicApi("/settings", {
      method:"PATCH",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({
        allow_youtube: $("musicAllowYoutube")?.checked,
        allow_spotify: $("musicAllowSpotify")?.checked,
        allow_soundcloud: $("musicAllowSoundcloud")?.checked,
        allow_links: $("musicAllowLinks")?.checked,
        public_commands: $("musicPublicCommands")?.checked,
      })
    });
    if (data?.settings) {
      sn7MusicData = sn7MusicData || {settings:{}, state:{}, current:null, queue:[]};
      sn7MusicData.settings = data.settings;
      if ($("musicAllowYoutube")) $("musicAllowYoutube").checked = data.settings.allow_youtube !== false;
      if ($("musicAllowSpotify")) $("musicAllowSpotify").checked = data.settings.allow_spotify !== false;
      if ($("musicAllowSoundcloud")) $("musicAllowSoundcloud").checked = data.settings.allow_soundcloud === true;
      if ($("musicAllowLinks")) $("musicAllowLinks").checked = data.settings.allow_links === true;
      if ($("musicPublicCommands")) $("musicPublicCommands").checked = data.settings.public_commands === true;
      musicRenderToggleFeedback();
    }
    if (msg) { msg.textContent = "✓ Configuração salva."; msg.className = "sn7-save-message success"; }
    setTimeout(closeMusicConfig, 350);
  } catch (error) {
    if (msg) { msg.textContent = `⚠ ${error.message}`; msg.className = "sn7-save-message error"; }
  }
}

/* O player é pré-carregado junto do boot, mas não cria áudio/rede de mídia até necessário. */
(function setupMusicTabLoader(){
  document.querySelectorAll('[data-tab="music"]').forEach((button) => {
    button.addEventListener("click", () => loadMusic().catch(() => {}));
  });
  if (document.querySelector('section#music.active')) loadMusic().catch(() => {});
  // Prefetch the lightweight connection status so opening the modal feels instant.
  if (musicHasChannel()) loadMusicConnections().catch(() => {});
  const params = new URLSearchParams(window.location.search);
  const connected = params.get("music_connected");
  const oauthError = params.get("music_error");
  if (connected || oauthError) {
    setTimeout(() => {
      if (typeof activateTab === "function") activateTab("music");
      openMusicConfig();
      const msg = $("musicConfigMsg");
      if (msg) {
        if (connected) {
          const label = connected === "youtube" ? "YouTube" : connected === "spotify" ? "Spotify" : "SoundCloud";
          msg.textContent = `✓ ${label} conectado com sucesso.`;
          msg.className = "sn7-save-message success";
        } else {
          msg.textContent = `⚠ ${oauthError}`;
          msg.className = "sn7-save-message error";
        }
      }
      try {
        const url = new URL(window.location.href);
        url.searchParams.delete("music_connected");
        url.searchParams.delete("music_error");
        history.replaceState({}, "", url.pathname + (url.search ? url.search : ""));
      } catch (_) {}
    }, 120);
  }
})();
