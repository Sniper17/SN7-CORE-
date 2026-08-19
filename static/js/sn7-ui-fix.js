/* SN7 Core UI enhancement
 * - Mantém a última aba aberta após F5/recarregamento
 * - Transições suaves entre abas
 * - Entrada suave dos painéis
 * - Animação suave dos modais de comandos
 * - Mantém/corrige o modal de conexão
 * - Compatível com mobile
 * - Respeita prefers-reduced-motion
 */
(function () {
  "use strict";

  const ACTIVE_TAB_KEY = "sn7-core-active-tab";
  const MODAL_ID = "sn7ConnectModal";
  let closeTimer = null;

  function getModal() {
    return document.getElementById(MODAL_ID);
  }

  function ensureStyles() {
    if (document.getElementById("sn7-ui-enhancement-style")) return;

    const style = document.createElement("style");
    style.id = "sn7-ui-enhancement-style";

    style.textContent = `
      /* ================================
         SN7 CORE - TRANSIÇÕES DAS ABAS
         ================================ */

      .tab {
        display: none;
      }

      .tab.active {
        display: block;
        animation: sn7TabIn .28s cubic-bezier(.22,1,.36,1) both;
      }

      @keyframes sn7TabIn {
        from {
          opacity: 0;
          transform: translateY(7px);
        }
        to {
          opacity: 1;
          transform: translateY(0);
        }
      }

      /* Evita que os cards pareçam "teletransportar" */
      .tab.active .panel,
      .tab.active .sn7-welcome,
      .tab.active .sn7-quick-card,
      .tab.active .stat,
      .tab.active .info-box,
      .tab.active .appearance-grid > * {
        animation: sn7PanelIn .32s cubic-bezier(.22,1,.36,1) both;
      }

      .tab.active .panel {
        animation-delay: .025s;
      }

      .tab.active .sn7-quick-card:nth-child(2),
      .tab.active .appearance-grid > *:nth-child(2) {
        animation-delay: .05s;
      }

      @keyframes sn7PanelIn {
        from {
          opacity: 0;
          transform: translateY(5px);
        }
        to {
          opacity: 1;
          transform: translateY(0);
        }
      }

      /* ================================
         MENU LATERAL / MENU MOBILE
         ================================ */

      nav button {
        transition:
          background-color .18s ease,
          color .18s ease,
          transform .18s ease,
          opacity .18s ease;
      }

      nav button:hover {
        transform: translateX(2px);
      }

      nav button:active {
        transform: scale(.98);
      }

      @media (max-width: 700px) {
        nav button:hover {
          transform: none;
        }

        nav button:active {
          transform: scale(.96);
        }
      }

      /* ================================
         BOTÕES
         ================================ */

      .btn,
      .sn7-subtle,
      .sn7-danger,
      .sn7-edit-response,
      .link-btn,
      .delete-btn {
        transition:
          transform .16s ease,
          opacity .16s ease,
          background-color .18s ease,
          border-color .18s ease,
          color .18s ease,
          box-shadow .18s ease;
      }

      .btn:hover,
      .sn7-subtle:hover,
      .sn7-danger:hover,
      .sn7-edit-response:hover {
        transform: translateY(-1px);
      }

      .btn:active,
      .sn7-subtle:active,
      .sn7-danger:active,
      .sn7-edit-response:active {
        transform: translateY(0) scale(.98);
      }

      /* ================================
         LINHAS DE COMANDOS
         ================================ */

      .sn7-command-row {
        transition:
          background-color .18s ease,
          opacity .18s ease,
          transform .18s ease;
      }

      .sn7-command-row:hover {
        transform: translateX(2px);
      }

      .sn7-command-row.loading {
        transform: none;
      }

      /* ================================
         MODAL DE COMANDOS
         ================================ */

      .sn7-command-modal {
        opacity: 0;
        visibility: hidden;
        pointer-events: none;
        transition:
          opacity .22s ease,
          visibility .22s ease;
      }

      .sn7-command-modal.open {
        opacity: 1;
        visibility: visible;
        pointer-events: auto;
      }

      .sn7-command-modal .sn7-box {
        opacity: 0;
        transform: translateY(12px) scale(.985);
        transition:
          opacity .22s cubic-bezier(.22,1,.36,1),
          transform .22s cubic-bezier(.22,1,.36,1);
      }

      .sn7-command-modal.open .sn7-box {
        opacity: 1;
        transform: translateY(0) scale(1);
      }

      /* ================================
         MODAL DE CONEXÃO
         ================================ */

      #sn7ConnectModal.sn7-connect-fixed {
        position: fixed !important;
        inset: 0 !important;
        z-index: 2147483000 !important;
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
        padding: 18px !important;
        box-sizing: border-box !important;
        background: rgba(3,5,9,.76) !important;
        opacity: 0 !important;
        visibility: hidden !important;
        pointer-events: none !important;
        transition:
          opacity .22s ease,
          visibility .22s ease !important;
      }

      #sn7ConnectModal.sn7-connect-fixed.is-open {
        opacity: 1 !important;
        visibility: visible !important;
        pointer-events: auto !important;
      }

      #sn7ConnectModal.sn7-connect-fixed .sn7-connect-modal-card {
        opacity: 0;
        transform: translateY(12px) scale(.985);
        transition:
          opacity .22s cubic-bezier(.22,1,.36,1),
          transform .22s cubic-bezier(.22,1,.36,1);
      }

      #sn7ConnectModal.sn7-connect-fixed.is-open .sn7-connect-modal-card {
        opacity: 1;
        transform: translateY(0) scale(1);
      }

      #sn7ConnectModal.sn7-connect-fixed .sn7-connect-modal-card {
        pointer-events: auto !important;
      }

      /* ================================
         REDUZIR MOVIMENTO
         ================================ */

      @media (prefers-reduced-motion: reduce) {
        .tab.active,
        .tab.active .panel,
        .tab.active .sn7-welcome,
        .tab.active .sn7-quick-card,
        .tab.active .stat,
        .tab.active .info-box,
        .tab.active .appearance-grid > * {
          animation: none !important;
        }

        nav button,
        .btn,
        .sn7-subtle,
        .sn7-danger,
        .sn7-edit-response,
        .link-btn,
        .delete-btn,
        .sn7-command-row,
        .sn7-command-modal,
        .sn7-command-modal .sn7-box,
        #sn7ConnectModal.sn7-connect-fixed,
        #sn7ConnectModal.sn7-connect-fixed .sn7-connect-modal-card {
          transition: none !important;
        }
      }
    `;

    document.head.appendChild(style);
  }

  /* ==================================
     MEMÓRIA DA ÚLTIMA ABA
     ================================== */

  function getSavedTab() {
    try {
      const saved = localStorage.getItem(ACTIVE_TAB_KEY);
      if (!saved) return null;

      const button = document.querySelector(
        `nav button[data-tab="${CSS.escape(saved)}"]`
      );

      return button ? saved : null;
    } catch (_) {
      return null;
    }
  }

  function saveActiveTab(tab) {
    try {
      localStorage.setItem(ACTIVE_TAB_KEY, tab);
    } catch (_) {
      // Alguns navegadores podem bloquear localStorage.
    }
  }

  function activateTab(tab, smoothScroll) {
    const button = document.querySelector(
      `nav button[data-tab="${CSS.escape(tab)}"]`
    );

    if (!button) return false;

    button.click();

    if (smoothScroll) {
      window.scrollTo({
        top: 0,
        behavior: "smooth"
      });
    }

    return true;
  }

  function setupTabPersistence() {
    const buttons = document.querySelectorAll("nav button[data-tab]");

    if (!buttons.length) return;

    buttons.forEach((button) => {
      button.addEventListener("click", () => {
        saveActiveTab(button.dataset.tab);
      });
    });

    const savedTab = getSavedTab();

    if (savedTab && savedTab !== "overview") {
      // Espera o dashboard terminar de montar para evitar conflito
      // com o estado inicial definido no HTML/app.js.
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          activateTab(savedTab, false);
        });
      });
    } else if (savedTab === "overview") {
      saveActiveTab("overview");
    }
  }

  /* ==================================
     MODAL DE CONEXÃO
     ================================== */

  function openConnectModal() {
    const modal = getModal();
    if (!modal) return;

    ensureStyles();
    clearTimeout(closeTimer);

    modal.classList.add("sn7-connect-fixed");
    modal.hidden = false;
    modal.removeAttribute("aria-hidden");

    document.body.classList.add("sn7-modal-open");

    requestAnimationFrame(() => {
      modal.classList.add("is-open");
    });
  }

  function closeConnectModal(event) {
    if (event && event.target !== event.currentTarget) return;

    const modal = getModal();
    if (!modal) return;

    ensureStyles();
    clearTimeout(closeTimer);

    modal.classList.remove("is-open");
    modal.setAttribute("aria-hidden", "true");

    document.body.classList.remove("sn7-modal-open");

    closeTimer = setTimeout(() => {
      modal.hidden = true;
      modal.classList.remove("sn7-connect-fixed");
    }, 220);
  }

  /* Substitui as funções antigas do dashboard. */
  window.openConnectModal = openConnectModal;
  window.closeConnectModal = closeConnectModal;

  function setupConnectModal() {
    const modal = getModal();
    if (!modal) return;

    modal.classList.add("sn7-connect-fixed");
    modal.hidden = true;
    modal.setAttribute("aria-hidden", "true");

    modal.addEventListener("click", (event) => {
      if (event.target === modal) {
        closeConnectModal(event);
      }
    });

    const card = modal.querySelector(".sn7-connect-modal-card");

    if (card) {
      card.addEventListener("click", (event) => {
        event.stopPropagation();
      });
    }

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        closeConnectModal();
      }
    });
  }

  /* ==================================
     INICIALIZAÇÃO
     ================================== */

  function init() {
    ensureStyles();
    setupTabPersistence();
    setupConnectModal();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
