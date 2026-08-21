
from __future__ import annotations
import re, unicodedata
from datetime import datetime, timezone
import requests
from bs4 import BeautifulSoup

UA = "SN7-Warzone-API/3.0"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA, "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8"})

def norm(value):
    value = unicodedata.normalize("NFKD", str(value or "").lower().strip())
    return "".join(c for c in value if not unicodedata.combining(c))

def slugify(value):
    value = norm(value).replace("’", "").replace("'", "")
    return re.sub(r"[^a-z0-9]+", "-", value).strip("-")

def clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()

def fetch(url, timeout=15):
    r = SESSION.get(url, timeout=timeout)
    r.raise_for_status()
    return r.text

def parse_date(value):
    if not value:
        return None
    value = clean_text(value)
    for fmt in ("%b %d, %Y %I:%M %p", "%b %d, %Y %H:%M",
                "%m/%d/%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", value)
    return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None

def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def game_slug(game):
    g = norm(game)
    return {
        "black ops 7": "bo7", "black ops 6": "bo6",
        "modern warfare 4": "mw4", "modern warfare 3": "mw3",
        "modern warfare 2": "mw2", "mw19": "mw19", "black ops 2": "bo2"
    }.get(g, slugify(game))

def canonical_id(name, game):
    return f"{game_slug(game)}:{slugify(name)}"

SLOT_MAP = {
    "optic":"Lente", "scope":"Lente", "reticle":"Lente",
    "muzzle":"Boca", "barrel":"Cano", "underbarrel":"Acoplamento",
    "magazine":"Carregador", "stock":"Coronha", "rear grip":"Cabo",
    "grip":"Cabo", "fire mods":"Mods de disparo", "laser":"Laser",
    "comb":"Pente", "ammunition":"Munição", "ammo":"Munição",
    "kit":"Kit de conversão"
}
def normalize_slot(slot):
    return SLOT_MAP.get(norm(slot), str(slot).strip())

def attachment_signature(attachments):
    return tuple(sorted((norm(a.get("slot")), norm(a.get("name")))
                        for a in attachments if a.get("slot") and a.get("name")))

def similarity(a, b):
    sa, sb = set(attachment_signature(a)), set(attachment_signature(b))
    return len(sa & sb) / max(len(sa | sb), 1) if sa and sb else 0.0

def soup_from_html(html):
    return BeautifulSoup(html, "html.parser")
