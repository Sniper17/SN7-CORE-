from __future__ import annotations
import re
from .common import (
    fetch, soup_from_html, clean_text, norm, slugify,
    game_slug, parse_date, normalize_slot
)

BASE = "https://wzhub.gg"
META_URL = f"{BASE}/pt/loadouts"

SLOTS = [
    "Lente", "Boca", "Cano", "Acoplamento", "Carregador",
    "Cabo", "Coronha", "Laser", "Munição", "Mods de disparo", "Pente"
]

STATUS_MAP = {
    "meta absoluta": "Absolute Meta",
    "absolute meta": "Absolute Meta",
    "meta": "Meta",
    "muito bom": "Very Good",
    "very good": "Very Good",
    "contender": "Contender",
    "viavel": "Viable",
    "viable": "Viable",
    "aceitavel": "Acceptable",
    "acceptable": "Acceptable",
}


def _weapon_url(name, game):
    return f"{BASE}/pt/loadouts/{game_slug(game)}-{slugify(name)}"


def _extract_attachments(text):
    found = []

    # Formato mais comum do WZHUB renderizado:
    # Lente | NOME, Boca | NOME...
    for slot in SLOTS:
        patterns = [
            rf"{re.escape(slot)}\s*\|\s*([^|]+?)(?=\s+(?:{'|'.join(map(re.escape, SLOTS))})\s*\||$)",
            rf"\b(.{{2,90}}?)\s+{re.escape(slot)}(?=\s|$)",
        ]

        value = None
        for pat in patterns:
            m = re.search(pat, text, re.I)
            if m:
                value = clean_text(m.group(1))
                break

        if not value:
            continue

        value = re.sub(r"\b(?:Image|Button|MOSTRAR DETALHES)\b", "", value, flags=re.I)
        value = clean_text(value)

        if 2 <= len(value) <= 90 and not any(a["slot"] == slot for a in found):
            found.append({"slot": slot, "name": value})

    return found[:8]


def _find_weapon_block(text, name):
    low = norm(text)
    pos = low.find(norm(name))
    if pos < 0:
        return ""

    return text[max(0, pos - 300):pos + 3500]


def _meta_status(text):
    low = norm(text)

    # Procura o status perto do nome/começo do bloco.
    for key, value in STATUS_MAP.items():
        if re.search(rf"\b{re.escape(key)}\b", low):
            return value

    return None


def _date(text):
    m = re.search(
        r"(?:Atualizada|Atualizado|Updated):?\s*"
        r"([A-ZÀ-ÿ][^|]{4,40}\d{4})",
        text,
        re.I,
    )
    return parse_date(m.group(1)) if m else None


def fetch_weapon(name, game="Black Ops 7"):
    url = _weapon_url(name, game)

    # Primeiro tentamos a página individual, que é muito mais precisa.
    candidates = [url]

    # Depois usamos a página geral como fallback.
    if url != META_URL:
        candidates.append(META_URL)

    last_error = None

    for target in candidates:
        try:
            html = fetch(target)
            soup = soup_from_html(html)
            text = clean_text(soup.get_text(" ", strip=True))
        except Exception as exc:
            last_error = type(exc).__name__
            continue

        block = _find_weapon_block(text, name)

        if not block:
            continue

        attachments = _extract_attachments(block)
        code_match = re.search(
            r"Loadout code\s+([A-Z0-9][A-Z0-9-]{7,})",
            block,
            re.I,
        )

        return {
            "ok": bool(attachments),
            "source": "WZHUB",
            "url": target,
            "meta_status": _meta_status(block),
            "code": code_match.group(1) if code_match else None,
            "updated": _date(block),
            "attachments": attachments,
        }

    result = {
        "ok": False,
        "source": "WZHUB",
        "url": url,
        "attachments": [],
    }
    if last_error:
        result["error"] = last_error
    return result
