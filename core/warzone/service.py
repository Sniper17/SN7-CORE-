from __future__ import annotations

from pathlib import Path

from .catalog import WeaponCatalog
from .meta_engine import MetaEngine
from .formatter import format_class_response

_BASE = Path(__file__).resolve().parent
_catalog = None
_engine = None


def _get_engine():
    global _catalog, _engine
    if _engine is None:
        _catalog = WeaponCatalog(_BASE / "data" / "meta.json")
        _engine = MetaEngine(_catalog)
    return _catalog, _engine


def resolve_wzclass(query: str) -> str:
    """Resolve uma classe Warzone usando somente os dados internos do SN7 Core.

    O !wzclass não consulta fontes externas em tempo real. Isso evita que
    páginas de terceiros contaminem a resposta com textos, datas ou outros
    conteúdos da página. O meta.json já contém o loadout sincronizado.
    """
    query = str(query or "").strip()
    if not query:
        return "⚠️ Informe a arma. Exemplo: !wzclass vst"

    catalog, engine = _get_engine()
    matches = catalog.search(query)

    if not matches:
        return f"🔎 Não encontrei uma arma chamada {query}. Tente o nome completo ou parte do nome."

    if len(matches) > 1:
        names = ", ".join(x.get("name", "Arma") for x in matches[:6])
        return f"🤔 Qual arma você deseja? {names}"

    # IMPORTANTE:
    # live=False impede qualquer consulta ao WZHUB/CODMunity/
    # WarzoneLoadout durante o comando. O comando usa exclusivamente
    # a classe já armazenada no meta.json.
    return format_class_response(engine.resolve(matches[0], live=False))


def reload_wz_data() -> int:
    """Recarrega os dados locais sem reiniciar o SN7 Core."""
    catalog, _ = _get_engine()
    catalog.reload()
    return len(catalog.weapons)
