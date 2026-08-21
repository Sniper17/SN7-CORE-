from __future__ import annotations
import re
from .common import fetch, soup_from_html, clean_text, norm

# URL oficial conhecida da atualização que contém as mudanças do Warzone.
PATCH_URL = (
    "https://www.callofduty.com/patchnotes/2026/08/"
    "call-of-duty-bo7-warzone-season-05-reloaded-patch-notes"
)

ATTRIBUTES = [
    (r"minimum damage", "Dano mínimo"),
    (r"maximum damage", "Dano máximo"),
    (r"damage", "Dano"),
    (r"damage range|range", "Alcance"),
    (r"headshot", "Dano de cabeça"),
    (r"recoil|gun kick", "Estabilidade"),
    (r"aim down sight|ads speed", "ADS"),
    (r"bullet velocity", "Velocidade da bala"),
    (r"fire rate|rate of fire", "Cadência"),
    (r"reload", "Recarga"),
    (r"movement", "Mobilidade"),
    (r"handling", "Manuseio"),
]


def _attribute_names(text):
    result = []
    low = norm(text)
    for pattern, label in ATTRIBUTES:
        if re.search(pattern, low, re.I) and label not in result:
            result.append(label)
    return result[:8]


def _weapon_windows(text, name):
    low = norm(text)
    target = norm(name)
    starts = [m.start() for m in re.finditer(re.escape(target), low)]

    windows = []
    for pos in starts:
        # Patch notes frequentemente têm o nome no cabeçalho e depois
        # vários parágrafos; uma janela ampla evita depender do HTML.
        chunk = text[max(0, pos - 500):pos + 8000]
        windows.append(chunk)

    return windows


def _classify(chunk):
    low = norm(chunk)

    buff_words = (
        r"increased|increase|improved|reduced recoil|faster|raised|boosted|"
        r"decreased recoil"
    )
    nerf_words = (
        r"decreased|decrease|reduced|slower|lowered|worse|removed"
    )

    buffs = len(re.findall(buff_words, low, re.I))
    nerfs = len(re.findall(nerf_words, low, re.I))

    if buffs and not nerfs:
        return "buff"
    if nerfs and not buffs:
        return "nerf"
    if buffs and nerfs:
        return "mixed"
    return None


def _extract_relevant_lines(chunk):
    parts = [
        clean_text(x) for x in re.split(r"(?<=[.;])\s+|•", chunk)
        if clean_text(x)
    ]

    relevant = []
    for part in parts:
        low = norm(part)
        if any(re.search(pat, low, re.I) for pat, _ in ATTRIBUTES):
            relevant.append(part)

    return relevant[:12]


def fetch_weapon(name):
    try:
        html = fetch(PATCH_URL)
        soup = soup_from_html(html)
        text = clean_text(soup.get_text(" ", strip=True))
    except Exception as exc:
        return {
            "ok": False,
            "source": "Activision/Raven Software",
            "url": PATCH_URL,
            "changes": [],
            "type": None,
            "error": type(exc).__name__,
        }

    windows = _weapon_windows(text, name)

    if not windows:
        return {
            "ok": False,
            "source": "Activision/Raven Software",
            "url": PATCH_URL,
            "changes": [],
            "type": None,
        }

    # Escolhemos a janela com maior quantidade de termos de balanceamento.
    best = max(
        windows,
        key=lambda x: (
            len(_attribute_names(x)),
            len(_extract_relevant_lines(x)),
        )
    )

    changes = _attribute_names(best)
    relevant = _extract_relevant_lines(best)
    typ = _classify(best)

    # Se a janela tiver o nome da arma mas nenhum atributo, não inventamos.
    return {
        "ok": True,
        "source": "Activision/Raven Software",
        "url": PATCH_URL,
        "changes": changes,
        "type": typ,
        "details": relevant,
    }
