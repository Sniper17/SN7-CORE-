from __future__ import annotations

from .live import LiveResolver

META_LABELS = {"meta", "absolute meta", "meta absoluta"}


class MetaEngine:
    def __init__(self, catalog):
        self.catalog = catalog
        self.live = LiveResolver()

    def resolve(self, weapon, live=True):
        w = dict(weapon)
        live_data = self.live.resolve(w) if live else {}

        act = live_data.get("patch") or {}
        loadout = live_data.get("loadout") or {}

        # A classificação META vem do resultado live. Um tier antigo salvo
        # no JSON nunca transforma sozinho uma arma em META atual.
        meta_status = live_data.get("meta_status")
        meta_confidence = live_data.get("meta_confidence", "nao_confirmado")
        meta_sources = live_data.get("meta_sources") or []

        pick_rate = live_data.get("pick_rate")
        if pick_rate is None:
            pick_rate = w.get("pick_rate")

        attachments = (loadout.get("attachments") or w.get("attachments") or [])[:5]
        code = loadout.get("code") or w.get("code") or ""

        patch = act if act.get("ok") else (w.get("recent_change") or {})
        typ = patch.get("type")

        meta = (
            str(meta_status or "").strip().lower() in META_LABELS
            and meta_confidence != "nao_confirmado"
        )

        loadout_confidence = (
            live_data.get("loadout_confidence")
            or loadout.get("confidence")
            or w.get("confidence")
        )

        if attachments and not loadout_confidence:
            loadout_confidence = "boa" if len(attachments) >= 5 else "provavel"

        return {
            "weapon": w,
            "meta": meta,
            "meta_status": meta_status,
            "meta_confidence": meta_confidence,
            "meta_sources": meta_sources,
            "pick_rate": pick_rate,
            "attachments": attachments,
            "code": code,
            "loadout_source": loadout.get("source") or w.get("loadout_source"),
            "confidence": loadout_confidence,
            "patch": patch,
            "status": (
                typ if typ in {"buff", "nerf", "mixed"}
                else ("confirmed_loadout" if attachments else "unconfirmed_loadout")
            ),
        }

    def general_meta(self):
        rows = self.catalog.current_meta()
        if not rows:
            return "⚠️ A META ainda está sendo sincronizada. Tente novamente em alguns minutos."
        return "🔥 META WARZONE: " + " • ".join(w["name"] for w in rows[:3])

    def category_meta(self, cat):
        rows = self.catalog.current_meta(cat)
        if not rows:
            return "⚠️ Ainda não encontrei META confirmada nessa categoria."
        return "🔥 META " + cat.upper() + ": " + " • ".join(w["name"] for w in rows[:3])
