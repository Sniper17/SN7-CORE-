/* SN7 Core UI patch V4
 * - Visão geral limpa: somente mensagem de boas-vindas
 * - "Aparência" passa a ser "Perfil"
 * - Perfil mostra o streamer Kick conectado
 * - Move a ação existente de adicionar/conectar o bot para Perfil
 * - Reduz feedback visual de toque/click
 * - Desktop com largura confortável e altura natural para scroll
 * - Não altera sn7-ui-fix.js
 */
(function () {
  "use strict";

  const PROFILE_TAB = "appearance";
  const STYLE_ID = "sn7-ui-patch-v4-style";

  function installStyles() {
    if (document.getElementById(STYLE_ID)) return;

    const style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = `
      /* Touch/click: feedback extremamente discreto */
      .sn7-command-row,
      .sn7-command-row:hover,
      .sn7-command-row:active,
      nav button,
      nav button:hover,
      nav button:active {
        -webkit-tap-highlight-color: transparent !important;
      }

      .sn7-command-row:active {
        background: transparent !important;
        transform: none !important;
      }

      @media (hover: none) and (pointer: coarse) {
        .sn7-command-row:hover {
          background: inherit !important;
          transform: none !important;
        }
      }

      /* Desktop: layout natural, com conteúdo crescendo para baixo */
      @media (min-width: 701px) {
        html, body {
          min-height: 100%;
          height: auto !important;
          overflow-x: hidden !important;
          overflow-y: auto !important;
        }

        .layout {
          min-height: 100vh;
          height: auto !important;
          align-items: flex-start;
        }

        .sidebar {
          min-height: 100vh;
          height: auto !important;
        }

        main {
          width: min(100%, 1180px);
          max-width: 1180px;
          min-height: 100vh;
          height: auto !important;
          overflow: visible !important;
          padding: 34px 42px 70px;
        }

        .tab {
          height: auto !important;
          min-height: 0 !important;
          overflow: visible !important;
        }

        .sn7-command-panel,
        .panel {
          height: auto !important;
          max-height: none !important;
        }
      }

      /* Visão geral */
      .sn7-home-clean {
        width: min(760px, 100%);
        min-height: 250px;
        display: flex;
        flex-direction: column;
        justify-content: center;
        padding: 42px 38px;
        border: 1px solid var(--border);
        border-radius: 18px;
        background:
          radial-gradient(circle at 90% 10%, rgba(124,92,255,.10), transparent 28%),
          linear-gradient(145deg, #141923, #10131a);
        box-shadow: 0 18px 45px rgba(0,0,0,.12);
      }

      .sn7-home-clean .sn7-home-badge {
        display: inline-flex;
        align-self: flex-start;
        padding: 6px 10px;
        border-radius: 999px;
        background: #1b202a;
        color: #aeb6c5;
        font-size: 10px;
        font-weight: 800;
        letter-spacing: 1.8px;
        margin-bottom: 18px;
      }

      .sn7-home-clean h2 {
        margin: 0 0 10px;
        font-size: 30px;
        letter-spacing: -.7px;
      }

      .sn7-home-clean p {
        max-width: 600px;
        font-size: 14px;
      }

      /* Perfil */
      .sn7-profile {
        width: min(760px, 100%);
      }

      .sn7-profile-card {
        padding: 26px;
        border: 1px solid var(--border);
        border-radius: 16px;
        background: var(--panel);
      }

      .sn7-profile-head {
        display: flex;
        align-items: center;
        gap: 16px;
        margin-bottom: 24px;
      }

      .sn7-profile-avatar {
        width: 58px;
        height: 58px;
        flex: 0 0 58px;
        display: grid;
        place-items: center;
        border-radius: 16px;
        background: #202635;
        border: 1px solid var(--border);
        color: #fff;
        font-size: 21px;
        font-weight: 900;
      }

      .sn7-profile-kicker {
        margin: 0 0 4px;
        color: #697386;
        font-size: 10px;
        font-weight: 800;
        letter-spacing: 1.7px;
        text-transform: uppercase;
      }

      .sn7-profile-name {
        margin: 0;
        font-size: 22px;
        overflow-wrap: anywhere;
      }

      .sn7-profile-meta {
        margin-top: 4px;
        color: var(--muted);
        font-size: 12px;
      }

      .sn7-profile-divider {
        height: 1px;
        background: var(--border);
        margin: 0 0 20px;
      }

      .sn7-profile-section-title {
        margin: 0 0 6px;
        font-size: 14px;
      }

      .sn7-profile-section-text {
        margin: 0 0 18px;
        font-size: 12px;
      }

      .sn7-profile-connect {
        width: 100%;
        margin-top: 4px;
      }

      @media (max-width: 700px) {
        .sn7-home-clean {
          min-height: 220px;
          padding: 28px 22px;
        }

        .sn7-home-clean h2 {
          font-size: 25px;
        }

        .sn7-profile-card {
          padding: 20px;
        }
      }
    `;

    (document.head || document.documentElement).appendChild(style);
  }

  function findKickUsername() {
    const status = window.__sn7KickStatus;
    if (!status || !Array.isArray(status.connections)) return "";

    const wanted = String(window.BROADCASTER_ID || "").trim();
    const row = status.connections.find(
      (item) => String(item.broadcaster_user_id || "").trim() === wanted
    );

    return String(row?.username || "").trim();
  }

  function updateProfileUsername(username) {
    const name = document.getElementById("sn7ProfileName");
    const avatar = document.getElementById("sn7ProfileAvatar");
    const channelName = document.querySelector(".channel-card strong");

    const clean = String(username || "").trim();
    if (!clean) return;

    if (name) name.textContent = clean.startsWith("@") ? clean : `@${clean}`;
    if (avatar) avatar.textContent = clean.replace(/^@/, "").charAt(0).toUpperCase() || "S";
    if (channelName) channelName.textContent = clean;
  }

  async function loadKickProfile() {
    try {
      const response = await fetch("/kick/status", { cache: "no-store" });
      if (!response.ok) return;
      const data = await response.json();
      window.__sn7KickStatus = data;
      updateProfileUsername(findKickUsername());
    } catch (_) {
      /* O perfil continua funcional mesmo se o status da Kick não responder. */
    }
  }

  function buildHome() {
    const overview = document.getElementById("overview");
    if (!overview) return;

    overview.innerHTML = `
      <div class="sn7-home-clean">
        <span class="sn7-home-badge">SN7 CORE</span>
        <h2>Bem-vindo ao projeto SN7 Core</h2>
        <p>Melhorias em breve.</p>
      </div>
    `;
  }

  function buildProfile() {
    const profile = document.getElementById(PROFILE_TAB);
    if (!profile) return;

    profile.innerHTML = `
      <div class="sn7-profile">
        <div class="sn7-profile-card">
          <div class="sn7-profile-head">
            <div id="sn7ProfileAvatar" class="sn7-profile-avatar">S</div>
            <div>
              <p class="sn7-profile-kicker">Perfil do streamer</p>
              <h3 id="sn7ProfileName" class="sn7-profile-name">Canal conectado</h3>
              <p class="sn7-profile-meta">Kick • Canal conectado ao SN7 Core</p>
            </div>
          </div>

          <div class="sn7-profile-divider"></div>

          <h3 class="sn7-profile-section-title">Seu chat</h3>
          <p class="sn7-profile-section-text">
            Configure a conexão do SN7 Core com o seu chat sem alterar as configurações da sua live.
          </p>

          <button
            type="button"
            class="btn sn7-profile-connect"
            onclick="openConnectModal()"
          >Add bot ao meu chat</button>
        </div>
      </div>
    `;
  }

  function renameProfileTab() {
    const button = document.querySelector(`nav button[data-tab="${PROFILE_TAB}"]`);
    if (!button) return;

    const span = button.querySelector("span");
    if (span) span.textContent = "◉";

    const textNode = Array.from(button.childNodes).find(
      (node) => node.nodeType === Node.TEXT_NODE
    );

    if (textNode) {
      textNode.nodeValue = " Perfil";
    } else {
      button.appendChild(document.createTextNode(" Perfil"));
    }
  }

  function init() {
    installStyles();
    buildHome();
    buildProfile();
    renameProfileTab();
    loadKickProfile();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
