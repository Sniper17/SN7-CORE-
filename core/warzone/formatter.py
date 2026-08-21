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
        return " | ".join(f"{x} ↓" for x in labels)
    if arrow == "+":
        return " + ".join(labels)
    return " | ".join(labels)


def _parts(atts):
    rows = sorted(
        (x for x in (atts or []) if x.get("slot") and x.get("name")),
        key=lambda x: ORDER.get(x.get("slot"), 99),
    )
    # Regra do Warzone: nunca enviar mais de 5 acessórios.
    return [
        f"{x.get('slot')}: {x.get('name')}"
        for x in rows[:MAX_ATTACHMENTS]
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
    """Resposta do !classe: somente o nome da arma + loadout confirmado + buff/nerf."""
    w = r["weapon"]
    name = w.get("name", "Arma")
    changes = (r.get("patch") or {}).get("changes") or []
    typ = r.get("status")
    atts = (r.get("attachments") or [])[:MAX_ATTACHMENTS]

    if not atts:
        if typ == "buff" and changes:
            return _fit_bytes([
                f"⚡ {name}",
                "📈 Buff: " + _changes(changes, "+"),
                "Loadout ainda não confirmado",
            ])
        if typ == "nerf" and changes:
            return _fit_bytes([
                f"⚡ {name}",
                "📉 Nerf: " + _changes(changes, "↓"),
                "Loadout ainda não confirmado",
            ])
        return f"🔎 {name} | Loadout ainda não confirmado."

    # A API não faz mais nenhuma marcação de usuário.
    # O StreamElements fica responsável por $(user).
    parts = [f"⚡ {name}"]

    if typ == "buff" and changes:
        parts.append("📈 Buff: " + _changes(changes, "+"))
    elif typ == "nerf" and changes:
        parts.append("📉 Nerf: " + _changes(changes, "↓"))

    parts.extend(_parts(atts))
    return _fit_bytes(parts)

