from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
import re
import unicodedata

from .sources.codmunity import fetch_weapon as codmunity
from .sources.warzoneloadout import fetch_weapon as wlg
from .sources.wzhub import fetch_weapon as wzhub
from .sources.activision import fetch_weapon as activision
from .sources.common import norm


_SOURCE_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="live-source")


class LiveResolver:
    """Resolve loadouts atuais sem travar o /classe e sem inventar builds."""

    MAX_ATTACHMENTS = 5

    def __init__(self, cache_minutes=10, source_timeout=4.5, stale_minutes=30):
        self.cache = {}
        self.cache_minutes = cache_minutes
        self.source_timeout = source_timeout
        self.stale_minutes = stale_minutes

    @staticmethod
    def _norm_text(value):
        value = str(value or "").lower().strip()
        value = unicodedata.normalize("NFKD", value)
        value = "".join(c for c in value if not unicodedata.combining(c))
        value = value.replace("×", "x")
        value = re.sub(r"[^a-z0-9]+", " ", value)
        return re.sub(r"\s+", " ", value).strip()

    @classmethod
    def _attachment_key(cls, item):
        slot = cls._norm_text(item.get("slot", ""))
        name = cls._norm_text(item.get("name", ""))
        if slot and name.endswith(" " + slot):
            name = name[:-(len(slot) + 1)]
        return slot, name

    @classmethod
    def _attachment_similarity(cls, a_items, b_items):
        if not a_items or not b_items:
            return 0.0

        a, b = {}, {}
        for item in a_items:
            slot, name = cls._attachment_key(item)
            if slot and name:
                a[slot] = name
        for item in b_items:
            slot, name = cls._attachment_key(item)
            if slot and name:
                b[slot] = name

        common_slots = set(a) & set(b)
        if not common_slots:
            return 0.0

        core = {
            "lente", "boca", "cano", "acoplamento",
            "carregador", "coronha", "cabo", "municao"
        }
        weighted_total = weighted_match = 0.0

        for slot in common_slots:
            left, right = a[slot], b[slot]
            if left == right:
                score = 1.0
            else:
                score = SequenceMatcher(None, left, right).ratio()
                lt, rt = set(left.split()), set(right.split())
                if lt and rt:
                    score = max(score, len(lt & rt) / len(lt | rt))

            weight = 2.0 if slot in core else 1.0
            weighted_total += weight
            weighted_match += weight * score

        union = (set(a) | set(b)) & core
        overlap = common_slots & core
        coverage = len(overlap) / len(union) if union else 0.0
        base = weighted_match / weighted_total if weighted_total else 0.0
        return round(base * (0.70 + 0.30 * coverage), 4)

    @classmethod
    def _normalize_loadout(cls, data):
        """Normaliza um build e nunca permite mais de 5 acessórios."""
        if not data:
            return {}

        chosen = dict(data)
        attachments = []
        seen = set()

        for item in data.get("attachments") or []:
            if not isinstance(item, dict):
                continue

            slot = str(item.get("slot") or "").strip()
            name = str(item.get("name") or "").strip()
            if not slot or not name:
                continue

            key = (cls._norm_text(slot), cls._norm_text(name))
            if key in seen:
                continue

            seen.add(key)
            attachments.append({"slot": slot, "name": name})

            if len(attachments) >= cls.MAX_ATTACHMENTS:
                break

        chosen["attachments"] = attachments
        chosen["attachments_count"] = len(attachments)
        chosen["max_attachments"] = cls.MAX_ATTACHMENTS
        return chosen

    @classmethod
    def _choose_loadout(cls, cm, wz, zh):
        """Escolhe um único build real. Nunca combina acessórios de fontes diferentes."""
        priority = {
            "CODMunity": 3,
            "WarzoneLoadout.games": 2,
            "WZHUB": 1,
        }

        valid = [
            ("CODMunity", cls._normalize_loadout(cm)),
            ("WarzoneLoadout.games", cls._normalize_loadout(wz)),
            ("WZHUB", cls._normalize_loadout(zh)),
        ]
        valid = [(n, d) for n, d in valid if d.get("attachments")]

        if not valid:
            return {}, "nenhuma", 0.0

        # Primeiro tenta encontrar duas fontes que realmente concordem.
        best = None
        for i, (na, a) in enumerate(valid):
            for nb, b in valid[i + 1:]:
                sim = cls._attachment_similarity(
                    a["attachments"], b["attachments"]
                )
                if sim >= 0.55 and (best is None or sim > best[0]):
                    best = (sim, na, a, nb, b)

        if best:
            sim, na, a, nb, b = best

            # Usa um único loadout completo. Não mistura acessórios.
            candidates = [(na, a), (nb, b)]
            chosen_name, chosen = max(
                candidates,
                key=lambda item: (
                    len(item[1].get("attachments") or []),
                    priority.get(item[0], 0),
                ),
            )
            chosen = dict(chosen)
            chosen["confirmation"] = f"{na} + {nb}"
            chosen["consensus_sources"] = [na, nb]
            chosen["consensus_details"] = {
                "score": round(sim, 3),
                "source_slots": {
                    na: len(a["attachments"]),
                    nb: len(b["attachments"]),
                },
                "selected_source": chosen_name,
                "rule": "um único loadout, máximo 5 acessórios",
            }
            return chosen, chosen["confirmation"], sim

        # Sem consenso: escolhe a fonte com build mais completo; em empate,
        # respeita a prioridade das fontes. Não cria um build híbrido.
        name, chosen = max(
            valid,
            key=lambda item: (
                len(item[1].get("attachments") or []),
                priority.get(item[0], 0),
            ),
        )
        chosen = dict(chosen)
        chosen["confirmation"] = name
        chosen["consensus_sources"] = [name]
        chosen["consensus_details"] = {
            "score": 0.0,
            "selected_source": name,
            "rule": "sem consenso; loadout de uma única fonte",
        }
        return chosen, name, 0.0

    @classmethod
    def _meta(cls, cm, wz, zh):
        """META somente quando uma fonte marca explicitamente a arma como Meta."""
        sources = [
            ("CODMunity", cm.get("meta_status")),
            ("WarzoneLoadout.games", wz.get("meta_status")),
            ("WZHUB", zh.get("meta_status")),
        ]

        explicit = []
        for source, value in sources:
            if not value:
                continue
            normalized = cls._norm_text(value)
            if normalized in {"meta", "absolute meta", "meta absoluta"}:
                explicit.append(source)

        if explicit:
            confidence = "alta" if len(explicit) >= 2 else "boa"
            return "Meta", confidence, explicit

        # "Contender", "Very Good", "A/B/C Tier" etc. não viram META.
        observed = [str(value) for _, value in sources if value]
        if observed:
            return observed[0], "nao_confirmado", []

        return None, "nao_confirmado", []

    @staticmethod
    def _loadout_confidence(chosen, confirmation, consensus):
        count = len(chosen.get("attachments") or [])
        if count == 0:
            return "nenhuma"
        if consensus >= 0.85:
            return "alta"
        if consensus >= 0.70:
            return "boa"
        if count >= 5 and confirmation == "CODMunity":
            return "boa"
        if count >= 5 and confirmation == "WarzoneLoadout.games":
            return "boa"
        return "provavel"

    @staticmethod
    def _safe_call(fn):
        try:
            return fn() or {}
        except Exception as exc:
            return {"ok": False, "error": type(exc).__name__}

    def _fetch_sources(self, name, game):
        futures = {
            "codmunity": _SOURCE_EXECUTOR.submit(
                self._safe_call, lambda: codmunity(name, game)
            ),
            "warzoneloadout": _SOURCE_EXECUTOR.submit(
                self._safe_call, lambda: wlg(name, game)
            ),
            "wzhub": _SOURCE_EXECUTOR.submit(
                self._safe_call, lambda: wzhub(name)
            ),
            "patch": _SOURCE_EXECUTOR.submit(
                self._safe_call, lambda: activision(name)
            ),
        }

        done, _ = wait(futures.values(), timeout=self.source_timeout)
        sources = {}

        for key, future in futures.items():
            if future in done:
                try:
                    sources[key] = future.result() or {}
                except Exception as exc:
                    sources[key] = {
                        "ok": False,
                        "error": type(exc).__name__,
                    }
            else:
                sources[key] = {"ok": False, "timeout": True}

        return sources

    def resolve(self, weapon):
        key = f"{weapon.get('game', 'Black Ops 7')}:{weapon.get('name', '')}"
        now = datetime.now(timezone.utc)
        cached = self.cache.get(key)

        if cached and now < cached["expires_at"]:
            return cached["data"]

        name = weapon.get("name")
        game = weapon.get("game", "Black Ops 7")
        sources = self._fetch_sources(name, game)

        cm = sources.get("codmunity") or {}
        wz = sources.get("warzoneloadout") or {}
        zh = sources.get("wzhub") or {}
        patch = sources.get("patch") or {}

        chosen, confirmation, consensus = self._choose_loadout(cm, wz, zh)
        loadout_confidence = self._loadout_confidence(
            chosen, confirmation, consensus
        )
        meta_status, meta_confidence, meta_sources = self._meta(cm, wz, zh)

        data = {
            "loadout": chosen,
            "confirmation": confirmation,
            "loadout_confidence": loadout_confidence,
            "loadout_consensus": round(consensus, 3),
            "meta_status": meta_status,
            "meta_confidence": meta_confidence,
            "meta_sources": meta_sources,
            "pick_rate": cm.get("pick_rate"),
            "patch": patch,
            "codmunity": cm,
            "warzoneloadout": wz,
            "wzhub": zh,
            "refreshed": now.isoformat(timespec="seconds"),
        }

        has_loadout = bool(chosen.get("attachments"))
        if not has_loadout and cached:
            stale_until = cached.get("stale_until")
            if stale_until and now < stale_until:
                return cached["data"]

        self.cache[key] = {
            "expires_at": now + timedelta(minutes=self.cache_minutes),
            "stale_until": now + timedelta(minutes=self.stale_minutes),
            "data": data,
        }

        return data
