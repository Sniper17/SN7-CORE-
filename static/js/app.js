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

async function loadSettings() {
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
    if (d.demo) {
      setMessage("Modo demonstração. O banco será conectado depois.", true);
    }
    await loadCommands();
  } catch (e) {
    updatePreview({
      currency_name: "Placos",
      currency_command: "!placos",
      currency_emoji: "🪙"
    });
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
      setMessage("✓ Alterações salvas.", true);
    } else {
      setMessage("⚠ " + (d.error || "Não foi possível salvar."));
    }
  } catch (e) {
    setMessage("⚠ Não foi possível salvar agora.");
  }
}

async function loadCommands() {
  try {
    const r = await fetch(`/api/commands/${BROADCASTER_ID}`, { cache: "no-store" });
    const d = await r.json();
    const list = d.commands || [];

    if ($("commandCount")) $("commandCount").textContent = list.length;
    if (!$("commandsList")) return;

    $("commandsList").innerHTML = list.length
      ? list.map(c => `
        <div class="command">
          <div><b>${esc(c.command)}</b></div>
          <div class="command-response">${esc(c.response)}</div>
          <button type="button" class="delete-btn"
            onclick="delCmd('${encodeURIComponent(c.command)}')">Excluir</button>
        </div>`).join("")
      : `<div class="empty-panel"><p>Nenhum comando personalizado ainda.</p></div>`;
  } catch (e) {
    if ($("commandCount")) $("commandCount").textContent = "0";
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
    await fetch(`/api/commands/${BROADCASTER_ID}?command=${c}`, { method: "DELETE" });
    await loadCommands();
  } catch (e) {}
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
  }[c]));
}

// Evita que o navegador restaure foco em um campo e abra o teclado sozinho.
window.addEventListener("load", () => {
  setTimeout(() => {
    const active = document.activeElement;
    if (active && ["INPUT", "TEXTAREA"].includes(active.tagName)) active.blur();
  }, 80);
  loadSettings();
});
