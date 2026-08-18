const $=id=>document.getElementById(id);
document.querySelectorAll("nav button").forEach(b=>b.onclick=()=>{
 document.querySelectorAll(".tab").forEach(x=>x.classList.remove("active"));
 document.querySelectorAll("nav button").forEach(x=>x.classList.remove("active"));
 $(b.dataset.tab).classList.add("active");b.classList.add("active");
 $("title").textContent=b.textContent.trim();
});
document.querySelector('button[data-tab="overview"]').classList.add("active");

async function loadSettings(){
 const r=await fetch(`/api/settings/${BROADCASTER_ID}`),d=await r.json();
 if(!d.ok)return;const s=d.settings;
 ["currency_name","currency_command","currency_emoji","rank_title","rank_limit","duel_win_points","duel_loss_points"].forEach(k=>$(k).value=s[k]);
 $("statCurrency").textContent=s.currency_name;$("statCommand").textContent=s.currency_command;$("statEmoji").textContent=s.currency_emoji;
 loadCommands();
}
async function saveSettings(){
 const data={};["currency_name","currency_command","currency_emoji","rank_title","rank_limit","duel_win_points","duel_loss_points"].forEach(k=>data[k]=$(k).value);
 const r=await fetch(`/api/settings/${BROADCASTER_ID}`,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify(data)});
 const d=await r.json();$("settingsMsg").textContent=d.ok?"✅ Salvo.":"❌ "+(d.error||"Erro");
 if(d.ok){$("statCurrency").textContent=d.settings.currency_name;$("statCommand").textContent=d.settings.currency_command;$("statEmoji").textContent=d.settings.currency_emoji}
}
async function loadCommands(){
 const d=await (await fetch(`/api/commands/${BROADCASTER_ID}`)).json();
 $("commandsList").innerHTML=(d.commands||[]).map(c=>`<div class="command"><span><b>${esc(c.command)}</b> — ${esc(c.response)}</span><button onclick="delCmd('${encodeURIComponent(c.command)}')">Excluir</button></div>`).join("");
}
async function saveCommand(){
 const command=$("cmd").value.trim(),response=$("response").value.trim();if(!command||!response)return;
 await fetch(`/api/commands/${BROADCASTER_ID}`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({command,response})});
 $("cmd").value="";$("response").value="";loadCommands();
}
async function delCmd(c){await fetch(`/api/commands/${BROADCASTER_ID}?command=${c}`,{method:"DELETE"});loadCommands()}
function esc(s){return String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"}[c]))}
loadSettings();
