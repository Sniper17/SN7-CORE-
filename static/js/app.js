const $=id=>document.getElementById(id);

function openTab(tab){
  const button=document.querySelector(`nav button[data-tab="${tab}"]`);
  if(button) button.click();
}

document.querySelectorAll("nav button").forEach(b=>b.onclick=()=>{
  document.querySelectorAll(".tab").forEach(x=>x.classList.remove("active"));
  document.querySelectorAll("nav button").forEach(x=>x.classList.remove("active"));
  const target=$(b.dataset.tab);
  if(target) target.classList.add("active");
  b.classList.add("active");
  $("title").textContent=b.textContent.trim();
});

function updatePreview(s){
  $("statCurrency").textContent=s.currency_name;
  $("statCommand").textContent=s.currency_command;
  $("statEmoji").textContent=s.currency_emoji;
  $("previewCurrency").textContent=s.currency_name;
  $("previewCommand").textContent=s.currency_command;
  $("previewEmoji").textContent=s.currency_emoji;
}

async function loadSettings(){
  try{
    const r=await fetch(`/api/settings/${BROADCASTER_ID}`);
    const d=await r.json();
    if(!d.ok)return;
    const s=d.settings;
    ["currency_name","currency_command","currency_emoji","rank_title","rank_limit","duel_win_points","duel_loss_points"].forEach(k=>{
      if($(k)) $(k).value=s[k];
    });
    updatePreview(s);
    await loadCommands();
  }catch(e){
    const msg=$("settingsMsg");
    if(msg) msg.textContent="❌ Não foi possível carregar as configurações.";
  }
}

async function saveSettings(){
  const data={};
  ["currency_name","currency_command","currency_emoji","rank_title","rank_limit","duel_win_points","duel_loss_points"].forEach(k=>{
    if($(k)) data[k]=$(k).value;
  });
  try{
    const r=await fetch(`/api/settings/${BROADCASTER_ID}`,{
      method:"PUT",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify(data)
    });
    const d=await r.json();
    const msg=$("settingsMsg");
    if(msg) msg.textContent=d.ok?"✓ Alterações salvas.":"❌ "+(d.error||"Erro");
    if(d.ok) updatePreview(d.settings);
  }catch(e){
    const msg=$("settingsMsg");
    if(msg) msg.textContent="❌ Erro ao salvar.";
  }
}

async function loadCommands(){
  try{
    const d=await (await fetch(`/api/commands/${BROADCASTER_ID}`)).json();
    const list=d.commands||[];
    $("commandCount").textContent=list.length;
    $("commandsList").innerHTML=list.length?list.map(c=>`
      <div class="command">
        <div><b>${esc(c.command)}</b></div>
        <div class="command-response">${esc(c.response)}</div>
        <button class="delete-btn" onclick="delCmd('${encodeURIComponent(c.command)}')">Excluir</button>
      </div>`).join(""):`<div class="empty-panel"><p>Nenhum comando personalizado ainda.</p></div>`;
  }catch(e){
    $("commandCount").textContent="—";
  }
}

async function saveCommand(){
  const command=$("cmd").value.trim(),response=$("response").value.trim();
  if(!command||!response)return;
  const r=await fetch(`/api/commands/${BROADCASTER_ID}`,{
    method:"POST",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify({command,response})
  });
  if(r.ok){
    $("cmd").value="";
    $("response").value="";
    loadCommands();
  }
}

async function delCmd(c){
  await fetch(`/api/commands/${BROADCASTER_ID}?command=${c}`,{method:"DELETE"});
  loadCommands();
}

function esc(s){
  return String(s).replace(/[&<>"']/g,c=>({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"
  }[c]));
}

loadSettings();
