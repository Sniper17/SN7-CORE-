const $ = id => document.getElementById(id);
let commandCache = [];

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

function buildCommandCatalog(){const section=$('commands');if(!section)return;section.innerHTML=`<div class="section-head"><div><h2>Comandos</h2><p>Um comando por função. Aliases ficam dentro do comando.</p></div></div><div class="panel command-catalog"><div class="command-category"><h3>🌐 Públicos</h3><div id="publicCommandsList"></div></div><div class="command-category"><h3>🛡️ ADM / MOD</h3><div id="modCommandsList"></div></div><div class="command-category"><div class="command-category-head"><h3>✨ Personalizados</h3><button class="subtle-btn" type="button" onclick="newCommand()">＋ Novo comando</button></div><div id="customCommandsList"></div></div></div>`;injectV2Styles()}
function injectV2Styles(){if(!document.getElementById('sn7subtlebtn')){const z=document.createElement('style');z.id='sn7subtlebtn';z.textContent='.subtle-btn{background:transparent;border:1px solid var(--border);color:var(--muted);border-radius:8px;padding:8px 12px;font-size:12px;font-weight:600;cursor:pointer;transition:.15s ease}.subtle-btn:hover{color:#fff;border-color:#394253;background:#171c25}.subtle-btn:active{transform:translateY(1px)}';document.head.appendChild(z)}if($('sn7v2styles'))return;const s=document.createElement('style');s.id='sn7v2styles';s.textContent='.command-category{padding:18px 20px;border-bottom:1px solid var(--border)}.command-category h3{margin:0 0 10px}.system-command{display:flex;justify-content:space-between;gap:10px;padding:12px 8px;border-top:1px solid var(--border);cursor:pointer}.system-command.disabled{opacity:.4}.system-command small{display:block;color:var(--muted);margin-top:4px}.aliases{font-size:11px;color:var(--muted);margin-top:4px}.cmd-modal{position:fixed;inset:0;background:#000b;display:flex;align-items:center;justify-content:center;padding:16px;z-index:9999}.cmd-box{width:min(620px,100%);max-height:90vh;overflow:auto;background:#11151d;border:1px solid var(--border);border-radius:16px;padding:20px}.cmd-box label{display:block;margin-top:14px;font-size:12px}.cmd-box input,.cmd-box textarea{width:100%;margin-top:7px}.cmd-box textarea{min-height:110px}.alias-row{display:flex;gap:8px;margin-top:7px}.alias-row input{margin:0}.danger{border:1px solid #63383b;background:transparent;color:#ff9c9c;border-radius:8px;padding:7px 10px}.cmd-actions{display:flex;justify-content:space-between;margin-top:18px}.cmd-actions div{display:flex;gap:8px}';document.head.appendChild(s)}
function renderCommands(){['public','mod','custom'].forEach(cat=>{const id=cat==='public'?'publicCommandsList':cat==='mod'?'modCommandsList':'customCommandsList',el=$(id);if(!el)return;const rows=commandCache.filter(c=>c.category===cat);el.innerHTML=rows.length?rows.map(c=>`<div class="system-command ${c.enabled?'':'disabled'}" onclick="openCommand('${encodeURIComponent(c.command_key)}')"><div><code>${esc(c.command)}</code><small>${esc(c.description)}</small>${c.aliases?.length?`<div class="aliases">Atalhos: ${c.aliases.map(esc).join(', ')}</div>`:''}</div><small>${c.enabled?'🟢 Ativo':'⚪ Desativado'}</small></div>`).join(''):'<div class="empty-panel"><p>Nenhum comando.</p></div>'})}
function openCommand(k){const c=commandCache.find(x=>x.command_key===decodeURIComponent(k));if(c)showCommand(c)}
function newCommand(){showCommand({command_key:'',command:'',description:'Comando personalizado desta live.',response:'',enabled:true,aliases:[],is_system:false,category:'custom'},true)}
function showCommand(c,isNew=false){document.querySelector('.cmd-modal')?.remove();const m=document.createElement('div');m.className='cmd-modal';m.innerHTML=`<div class="cmd-box"><h3>${isNew?'✨ Novo comando':esc(c.command)}</h3><label>Comando principal<input id="v2cmd" value="${esc(c.command)}"></label><label>Descrição<input id="v2desc" value="${esc(c.description)}"></label><label>Mensagem/resposta<textarea id="v2resp">${esc(c.response)}</textarea></label>${!isNew?`<label>Palavras de ativação${(c.aliases||[]).map(a=>`<div class="alias-row"><input value="${esc(a)}" readonly><button class="danger" onclick="removeAlias('${encodeURIComponent(c.command_key)}','${encodeURIComponent(a)}')">🗑</button></div>`).join('')}<div class="alias-row"><input id="v2alias" placeholder="!rank"><button class="btn" onclick="addAlias('${encodeURIComponent(c.command_key)}')">Adicionar</button></div></label>`:''}<div class="cmd-actions"><button class="danger" onclick="deleteCommandV2('${encodeURIComponent(c.command_key)}')">${c.is_system?'Desativar':'Excluir'}</button><div><button class="btn" onclick="saveCommandV2('${encodeURIComponent(c.command_key)}',${isNew})">Salvar</button><button class="btn" onclick="this.closest('.cmd-modal').remove()">Fechar</button></div></div></div>`;document.body.appendChild(m)}
async function saveCommandV2(key,isNew){const body={command:$('v2cmd').value.trim(),description:$('v2desc').value.trim(),response:$('v2resp').value};const r=await fetch(isNew?`/api/commands/${BROADCASTER_ID}`:`/api/commands/${BROADCASTER_ID}/${key}`,{method:isNew?'POST':'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const d=await r.json();if(!d.ok)return alert(d.error||'Falha ao salvar');document.querySelector('.cmd-modal')?.remove();loadCommands()}
async function addAlias(key){const alias=$('v2alias')?.value.trim();if(!alias)return;const r=await fetch(`/api/commands/${BROADCASTER_ID}/${key}/aliases`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({alias})});const d=await r.json();if(!d.ok)return alert(d.error||'Falha');document.querySelector('.cmd-modal')?.remove();await loadCommands();openCommand(key)}
async function removeAlias(key,alias){const r=await fetch(`/api/commands/${BROADCASTER_ID}/${key}/aliases`,{method:'DELETE',headers:{'Content-Type':'application/json'},body:JSON.stringify({alias:decodeURIComponent(alias)})});const d=await r.json();if(!d.ok)return alert(d.error||'Falha');document.querySelector('.cmd-modal')?.remove();await loadCommands();openCommand(key)}
async function deleteCommandV2(key){const r=await fetch(`/api/commands/${BROADCASTER_ID}/${key}`,{method:'DELETE'});const d=await r.json();if(!d.ok)return alert(d.error||'Falha');document.querySelector('.cmd-modal')?.remove();loadCommands()}
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
      cache: "no-store",
      body: JSON.stringify(data)
    });

    const d = await r.json();

    if (d.ok) {
      updatePreview(d.settings);
      renderSystemCommands(d.settings);
      await loadCommands();
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
    commandCache = list;

    if ($("commandCount")) $("commandCount").textContent = list.filter(c => c.category === "custom").length;
    renderCommands();
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
