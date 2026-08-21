from __future__ import annotations
import re
from .common import fetch, soup_from_html, clean_text, norm, slugify, game_slug, parse_date

BASE = "https://codmunity.gg"

SLOTS = [
    ("Optic", "Lente"), ("Muzzle", "Boca"), ("Barrel", "Cano"),
    ("Underbarrel", "Acoplamento"), ("Magazine", "Carregador"),
    ("Stock", "Coronha"), ("Rear Grip", "Cabo"), ("Grip", "Cabo"),
    ("Fire Mods", "Mods de disparo"), ("Laser", "Laser"),
    ("Comb", "Pente"), ("Ammunition", "Munição"),
]

# O FAQ do CODMunity usa frequentemente o formato:
# "Redwell 30-S 2x Lente, Monolithic Suppressor Boca, ..."
# enquanto outras páginas usam "Lente | item". Os dois formatos são aceitos.
PT_SLOT_ALIASES = {
    "lente": "Lente",
    "boca": "Boca",
    "cano": "Cano",
    "acoplamento": "Acoplamento",
    "carregador": "Carregador",
    "coronha": "Coronha",
    "cabo": "Cabo",
    "mods de disparo": "Mods de disparo",
    "laser": "Laser",
    "pente": "Pente",
    "municao": "Munição",
}


def weapon_url(name, game="Black Ops 7"):
    return f"{BASE}/pt/weapon/{game_slug(game)}/{slugify(name)}"


def _text(soup):
    return clean_text(soup.get_text(" ", strip=True))


def _meta_status(text):
    patterns = [
        r"(?:WZ Meta Status|Meta Status)\s+(Absolute Meta|Meta|Very Good|Contender|Viable|Acceptable|Unrated|Muito Bom|Contender)",
        r"\b(Absolute Meta|Meta|Very Good|Contender|Viable|Acceptable|Muito Bom)\b",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            return m.group(1)
    return None


def _pick_rate(text):
    for pat in (
        r"Pick Rate\s*([0-9]+(?:\.[0-9]+)?)\s*%",
        r"([0-9]+(?:\.[0-9]+)?)\s*%\s*Pick Rate",
        r"taxa de escolha.*?([0-9]+(?:\.[0-9]+)?)\s*%",
    ):
        m = re.search(pat, text, re.I)
        if m:
            return float(m.group(1))
    return None


def _code(text):
    m = re.search(
        r"(?:Code|Código|Loadout Code)\s*:?\s*([A-Z0-9][A-Z0-9-]{7,})",
        text, re.I
    )
    return m.group(1) if m else None


def _slot_from_label(label):
    key = norm(label).strip().lower()
    return PT_SLOT_ALIASES.get(key)


def _clean_attachment_name(value):
    value = clean_text(value)
    value = re.sub(r"^(?:Image|Imagem|Acessórios|Accessories)\s+", "", value, flags=re.I)
    value = re.sub(r"\s+\d+\s*$", "", value)
    return clean_text(value).strip(" ,|:-")


def _attachments_structured(text):
    """Lê o formato estruturado usado em algumas páginas do CODMunity."""
    found = []
    labels = [re.escape(x[0]) for x in SLOTS]
    label_alt = "|".join(labels)

    for slot_en, slot_pt in SLOTS:
        pat = (
            rf"\b{re.escape(slot_en)}\b\s*\|\s*(.+?)"
            rf"(?=\s+(?:{label_alt})\s*\||\s*$)"
        )
        m = re.search(pat, text, re.I)
        if m:
            value = _clean_attachment_name(m.group(1))
            if 2 <= len(value) <= 100:
                found.append({"slot": slot_pt, "name": value})

    return found


def _attachments_from_faq(faq):
    """Converte o texto humano do FAQ em acessórios estruturados.

    Exemplo real:
    Redwell 30-S 2x Lente, Monolithic Suppressor Boca,
    15" Benthic Barrel Cano, VAS Convergence Foregrip Acoplamento,
    Rexford Extended II Carregador
    """
    if not faq:
        return []

    # Normaliza separadores sem destruir nomes que tenham pontuação.
    raw = re.sub(r"\s+", " ", faq).strip()
    pieces = [clean_text(x) for x in re.split(r"\s*[,;]\s*", raw) if clean_text(x)]
    found = []

    for piece in pieces:
        # O rótulo de slot aparece no final de cada item.
        m = re.search(
            r"\s+(Lente|Boca|Cano|Acoplamento|Carregador|Coronha|Cabo|"
            r"Mods de disparo|Laser|Pente|Munição)\s*$",
            piece,
            re.I,
        )
        if not m:
            continue
        slot = _slot_from_label(m.group(1))
        value = _clean_attachment_name(piece[:m.start()])
        if slot and 2 <= len(value) <= 100:
            if not any(x["slot"] == slot for x in found):
                found.append({"slot": slot, "name": value})

    # Fallback para FAQ sem vírgulas: procura cada slot e usa o trecho anterior.
    if len(found) < 4:
        labels = r"Lente|Boca|Cano|Acoplamento|Carregador|Coronha|Cabo|Mods de disparo|Laser|Pente|Munição"
        matches = list(re.finditer(rf"(.+?)\s+({labels})(?=\s*(?:,|$))", raw, re.I))
        for m in matches:
            slot = _slot_from_label(m.group(2))
            value = _clean_attachment_name(m.group(1))
            if slot and 2 <= len(value) <= 100 and not any(x["slot"] == slot for x in found):
                found.append({"slot": slot, "name": value})

    return found[:8]


def _attachments(text, faq=None):
    structured = _attachments_structured(text)
    faq_items = _attachments_from_faq(faq)

    # Os dois formatos vêm da MESMA fonte (CODMunity). Escolhemos o formato
    # que recuperou mais acessórios, sem misturar builds. Isso evita perder
    # um slot válido quando o FAQ omite um rótulo que existe na página.
    candidates = [
        (len(faq_items), faq_items, "faq"),
        (len(structured), structured, "structured"),
    ]
    count, items, source = max(candidates, key=lambda x: x[0])
    if count:
        return items, source
    return [], "none"


def _faq_loadout(text, weapon_name):
    patterns = [
        rf"(?:Para os acessórios|Para os acessorios|For the attachments)"
        rf".{{0,120}}?(?:você deve usar|voce deve usar|you should use)\s*:\s*(.*?)(?:\.\s*"
        rf"(?:A\s+)?(?:AN-94|{re.escape(weapon_name)})|$)",
        r"(?:você deve usar|voce deve usar|you should use)\s*:\s*(.*?)(?:$)",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            value = clean_text(m.group(1))
            if value:
                return value[:1200]
    return None


def fetch_weapon(name, game="Black Ops 7"):
    url = weapon_url(name, game)

    try:
        html = fetch(url)
    except Exception as exc:
        return {
            "ok": False, "source": "CODMunity", "url": url,
            "attachments": [], "error": type(exc).__name__
        }

    soup = soup_from_html(html)
    text = _text(soup)
    faq = _faq_loadout(text, name)
    attachments, attachment_source = _attachments(text, faq)

    date_match = re.search(
        r"(?:Last Updated|Última atualização|Atualizado)\s*:?\s*"
        r"([A-ZÀ-ÿ][^|]{5,35}202[0-9])",
        text, re.I
    )

    return {
        "ok": True,
        "source": "CODMunity",
        "url": url,
        "meta_status": _meta_status(text),
        "pick_rate": _pick_rate(text),
        "code": _code(text),
        "attachments": attachments,
        "attachment_source": attachment_source,
        "updated": parse_date(date_match.group(1)) if date_match else None,
        "faq_loadout_text": faq,
    }
