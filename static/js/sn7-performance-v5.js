/* SN7 CORE V5 - carregamento inteligente / 1.8.6
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


(function () {
  "use strict";
  if (window.__SN7_PROFILE_AVATAR_REFRESH_PATCH) return;
  window.__SN7_PROFILE_AVATAR_REFRESH_PATCH = true;

  function refreshAvatarInBackground() {
    const initial = window.SN7_INITIAL_PROFILE;
    const user = initial && initial.id ? initial : null;
    if (!user || String(user.profile_picture_url || user.profile_picture || user.avatar_url || "").trim()) return;
    fetch("/kick/profile/refresh", { credentials: "same-origin", cache: "no-store" })
      .then((r) => r.ok ? r.json() : null)
      .then((d) => {
        if (!d || !d.ok || !d.user) return;
        const u = d.user;
        const username = String(u.username || "Kick").replace(/^@/, "").trim() || "Kick";
        const fallback = (username.charAt(0) || "S").toUpperCase();
        const avatarUrl = String(u.profile_picture_url || u.profile_picture || u.avatar_url || "").trim();
        const setAvatar = (id, fallbackText) => {
          const el = document.getElementById(id);
          if (!el) return;
          el.innerHTML = "";
          if (!avatarUrl) { el.textContent = fallbackText; return; }
          const img = document.createElement("img");
          img.src = avatarUrl; img.alt = "Foto da conta"; img.loading = "eager";
          img.onerror = () => { el.textContent = fallbackText; };
          el.appendChild(img);
        };
        setAvatar("sn7ChannelAvatar", fallback);
        setAvatar("sn7NavProfileIcon", fallback);
        setAvatar("sn7KickPlatformIcon", "K");
        window.__sn7KickProfile = { id: u.id, username, profile_picture_url: avatarUrl };
      })
      .catch(() => {});
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", refreshAvatarInBackground, { once: true });
  } else {
    refreshAvatarInBackground();
  }
})();
