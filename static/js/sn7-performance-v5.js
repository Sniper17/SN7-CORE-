/* SN7 CORE V5 - carregamento inteligente / 1.8.0
 * Corrige o problema da V3: as abas refaziam GETs depois do boot
 * e o perfil só era carregado quando o usuário entrava nele.
 */
(function () {
  "use strict";

  const state = {
    settings: { promise: null, ready: false, value: undefined },
    commands: { promise: null, ready: false, value: undefined },
    ranking: { promise: null, ready: false, value: undefined },
    profile: { promise: null, ready: false, value: undefined },
  };

  function wrapCached(name, original) {
    if (typeof original !== "function") return original;
    const item = state[name];

    return function (...args) {
      if (item.ready) return Promise.resolve(item.value);
      if (item.promise) return item.promise;

      item.promise = Promise.resolve()
        .then(() => original.apply(this, args))
        .then((value) => {
          item.value = value;
          item.ready = true;
          return value;
        })
        .catch((error) => {
          item.promise = null;
          item.ready = false;
          throw error;
        });

      return item.promise;
    };
  }

  function install() {
    if (window.__SN7_V5_INSTALLED) return;
    window.__SN7_V5_INSTALLED = true;

    if (typeof window.loadSettings === "function") {
      window.loadSettings = wrapCached("settings", window.loadSettings);
    }
    if (typeof window.loadCommands === "function") {
      window.loadCommands = wrapCached("commands", window.loadCommands);
    }
    if (typeof window.loadRanking === "function") {
      window.loadRanking = wrapCached("ranking", window.loadRanking);
    }

    /*
     * O loader de Perfil já possui cache e deduplicação próprios.
     * Não o embrulhamos aqui para não manter uma Promise resolvida para sempre
     * e impedir atualizações posteriores do perfil.
     */

    /*
     * O loader inicial nunca espera o perfil.
     * O shell do perfil aparece imediatamente e os dados chegam em segundo plano.
     */
  }

  install();

  // Perfil e música são carregados sob demanda para reduzir o tempo de primeira pintura.
  // A navegação chama os loaders quando a aba correspondente é aberta.
})();

