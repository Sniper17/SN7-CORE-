
from __future__ import annotations
import json, re, unicodedata
from pathlib import Path

ACTIVE_PRIORITY = {
    "modern warfare 4": 100,
    "black ops 7": 95,
    "black ops 6": 85,
    "modern warfare 3": 80,
    "mw3": 80,
    "modern warfare 2": 40,
    "mw19": 20,
    "black ops 2": 10,
}

def norm(s):
    s = unicodedata.normalize("NFKD", str(s or "").lower().strip())
    return "".join(c for c in s if not unicodedata.combining(c))

class WeaponCatalog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.reload()

    def reload(self):
        self.data = json.loads(self.path.read_text(encoding="utf-8"))
        raw = self.data.get("weapons", {})
        if isinstance(raw, list):
            self.weapons = raw
        else:
            self.weapons = list(raw.values())

    @staticmethod
    def priority(w):
        return ACTIVE_PRIORITY.get(norm(w.get("game")), 50)

    def search(self, query):
        q = norm(query)
        if not q: return []
        exact, partial = [], []
        for w in self.weapons:
            names = [w.get("name","")] + list(w.get("aliases", []) or [])
            ns = [norm(x) for x in names]
            score = self.priority(w)
            if q in ns:
                exact.append((score, w))
            elif any(x.startswith(q) for x in ns):
                partial.append((score, w))
            elif len(q) >= 3 and any(q in x for x in ns):
                partial.append((score - 10, w))
        if exact:
            top_priority = max(score for score, _ in exact)
            # Prefer the current Warzone version when an old game has the same exact name.
            if top_priority >= 80:
                exact = [(score, w) for score, w in exact if score == top_priority]
            pool = exact
        else:
            pool = partial
        seen, out = set(), []
        for _, w in sorted(pool, key=lambda x: (-x[0], len(norm(x[1].get("name",""))), norm(x[1].get("name","")))):
            key = (norm(w.get("name")), norm(w.get("game")))
            if key not in seen:
                seen.add(key); out.append(w)
        return out

    def by_game(self, game):
        return [w for w in self.weapons if norm(w.get("game")) == norm(game)]

    def current_meta(self, category=None):
        rows = [w for w in self.weapons if str(w.get("meta_status","")).lower() in {"meta","absolute meta","meta absoluta"} or w.get("is_meta")]
        if category:
            rows = [w for w in rows if norm(w.get("category")) == norm(category)]
        return sorted(rows, key=lambda w: float(w.get("pick_rate") or 0), reverse=True)
