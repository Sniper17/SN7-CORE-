/* SN7 Core UI fix - connection modal */
(function () {
  'use strict';

  const MODAL_ID = 'sn7ConnectModal';
  let closeTimer = null;

  function getModal() {
    return document.getElementById(MODAL_ID);
  }

  function ensureStyles() {
    if (document.getElementById('sn7-ui-fix-style')) return;

    const style = document.createElement('style');
    style.id = 'sn7-ui-fix-style';
    style.textContent = `
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
        transition: opacity .18s ease, visibility .18s ease !important;
      }

      #sn7ConnectModal.sn7-connect-fixed.is-open {
        opacity: 1 !important;
        visibility: visible !important;
        pointer-events: auto !important;
      }

      #sn7ConnectModal.sn7-connect-fixed .sn7-modal-card {
        pointer-events: auto !important;
      }
    `;
    document.head.appendChild(style);
  }

  function openConnectModal() {
    const modal = getModal();
    if (!modal) return;

    ensureStyles();
    clearTimeout(closeTimer);

    modal.classList.add('sn7-connect-fixed');
    modal.hidden = false;
    modal.removeAttribute('aria-hidden');
    document.body.classList.add('sn7-modal-open');

    requestAnimationFrame(() => {
      modal.classList.add('is-open');
    });
  }

  function closeConnectModal(event) {
    if (event && event.target !== event.currentTarget) return;

    const modal = getModal();
    if (!modal) return;

    ensureStyles();
    clearTimeout(closeTimer);

    modal.classList.remove('is-open');
    modal.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('sn7-modal-open');

    closeTimer = setTimeout(() => {
      modal.hidden = true;
      modal.classList.remove('sn7-connect-fixed');
    }, 190);
  }

  // Substitui as funções antigas do dashboard sem editar app.js.
  window.openConnectModal = openConnectModal;
  window.closeConnectModal = closeConnectModal;

  function init() {
    ensureStyles();

    const modal = getModal();
    if (!modal) return;

    modal.classList.add('sn7-connect-fixed');
    modal.hidden = true;
    modal.setAttribute('aria-hidden', 'true');

    modal.addEventListener('click', (event) => {
      if (event.target === modal) closeConnectModal(event);
    });

    const card = modal.querySelector('.sn7-modal-card');
    if (card) {
      card.addEventListener('click', (event) => event.stopPropagation());
    }

    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') closeConnectModal();
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }
})();
