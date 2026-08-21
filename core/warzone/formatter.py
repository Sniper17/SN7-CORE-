import random

PREFIX = {
    "buff": ["⚠️", "🔥", "⚡"],
    "nerf": ["📉", "🎮", "⚡", "💀"],
    "confirmed_loadout": ["🎯", "🔎", "⚡"],
    "meta": ["🔥", "🎯", "⚡"],
    "boa": ["🎯", "⚡", "👍"],
}

ORDER = {
    "Boca": 1, "Cano": 2, "Lente": 3, "Carregador": 4,
    "Acoplamento": 5, "Cabo": 6, "Coronha": 7, "Laser": 8,
    "Munição": 9, "Mods de disparo": 10, "Pente": 11,
    "Kit de conversão": 12,
}

MAX_BYTES = 500
MAX_ATTACHMENTS = 5


def _changes(changes, arrow=None):
    labels = []
    for c in changes or []:
        c = str(c).strip().rstrip("+↓ ")
        if c and c not in labels:
            labels.append(c)

    if arrow == "↓":
        return " • ".join(f"{x} ↓" for x in labels)
    if arrow == "+":
        return " • ".join(labels)
    return " • ".join(labels)


def _parts(atts):
    rows = sorted(
        (x for x in (atts or []) if isinstance(x, dict) and x.get("slot") and x.get("name")),
        key=lambda x: ORDER.get(x.get("slot"), 99),
    )

    # Regra fixa do SN7 Core: nunca enviar mais de 5 acessórios.
    rows = rows[:MAX_ATTACHMENTS]

    return [
        f"{x.get('slot')}: {x.get('name')}"
        for x in rows
    ]


def _fit_bytes(parts, limit=MAX_BYTES):
    if not parts:
        return ""

    result = parts[0]
    for part in parts[1:]:
        candidate = result + " • " + part
        if len(candidate.encode("utf-8")) > limit:
            break
        result = candidate
    return result


def format_class_response(r):
    """Formata !wzclass de forma limpa: arma + até 5 acessórios.

    Não inclui nome de fonte, página, data, código ou texto extra.
    Buff/nerf só aparece quando já estiver registrado localmente.
    """
    w = r.get("weapon") or {}
    name = w.get("name", "Arma")

    # O serviço interno já entrega os acessórios do meta.json.
    # Ainda aplicamos o limite aqui como segunda camada de segurança.
    atts = (r.get("attachments") or [])[:MAX_ATTACHMENTS]

    if not atts:
        return f"⚠️ {name}: classe não encontrada."

    parts = [f"⚡ {name}"]

    changes = (r.get("patch") or {}).get("changes") or []
    typ = r.get("status")

    # Mantém apenas uma eventual informação local de buff/nerf.
    if typ == "buff" and changes:
        parts.append("📈 Buff: " + _changes(changes, "+"))
    elif typ == "nerf" and changes:
        parts.append("📉 Nerf: " + _changes(changes, "↓"))

    parts.extend(_parts(atts))
    return _fit_bytes(parts)
