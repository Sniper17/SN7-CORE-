/* SN7 CORE V4 - carregamento inteligente
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
    if (window.__SN7_V4_INSTALLED) return;
    window.__SN7_V4_INSTALLED = true;

    if (typeof window.loadSettings === "function") {
      window.loadSettings = wrapCached("settings", window.loadSettings);
    }
    if (typeof window.loadCommands === "function") {
      window.loadCommands = wrapCached("commands", window.loadCommands);
    }
    if (typeof window.loadRanking === "function") {
      window.loadRanking = wrapCached("ranking", window.loadRanking);
    }

    if (typeof window.sn7LoadProfile === "function") {
      const originalProfile = window.sn7LoadProfile;

      window.sn7LoadProfile = function (force = false) {
        if (force) {
          state.profile.ready = false;
          state.profile.promise = null;
          state.profile.value = undefined;
        }

        if (state.profile.ready && !force) {
          return Promise.resolve(state.profile.value);
        }

        if (state.profile.promise && !force) {
          return state.profile.promise;
        }

        state.profile.promise = Promise.resolve()
          .then(() => originalProfile.call(this, force))
          .then((value) => {
            state.profile.value = value;
            state.profile.ready = true;
            return value;
          })
          .catch((error) => {
            state.profile.promise = null;
            state.profile.ready = false;
            throw error;
          });

        return state.profile.promise;
      };
    }

    /*
     * V3 esconde o loader depois de settings/commands/ranking.
     * O perfil agora também é preparado em paralelo; só liberamos a tela
     * quando essa primeira consulta terminar.
     */
    if (
      typeof window.sn7HideBootLoader === "function" &&
      typeof window.sn7LoadProfile === "function"
    ) {
      const originalHide = window.sn7HideBootLoader;

      window.sn7HideBootLoader = function () {
        const profilePromise = state.profile.promise;

        if (profilePromise) {
          Promise.resolve(profilePromise)
            .catch(() => {})
            .finally(() => originalHide());
        } else {
          originalHide();
        }
      };
    }
  }

  install();

  function warmProfile() {
    if (typeof window.sn7LoadProfile !== "function") return;
    window.sn7LoadProfile(false).catch(() => {});
  }

  /*
   * dashboard.html define sn7LoadProfile antes de carregar este arquivo.
   * Assim conseguimos iniciar o perfil antes do usuário clicar na aba.
   */
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", warmProfile, { once: true });
  } else {
    warmProfile();
  }
})();

