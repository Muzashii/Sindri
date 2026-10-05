"""Cores e rótulos de "quem é cada peça" (uma cor por solicitação), usados na tela e no relatório."""
from __future__ import annotations

from PySide6.QtGui import QColor

from ..core.models import Part

# cores das solicitações (distintas do azul/laranja usados para os materiais)
OWNER_COLORS = ["#0d9488", "#9333ea", "#16a34a", "#db2777", "#ca8a04", "#0891b2", "#a16207",
                "#65a30d", "#4f46e5", "#dc2626", "#0f766e", "#7c3aed", "#be185d", "#15803d"]


def part_number(p: Part) -> str:
    """'P007' -> '7' (o número curto escrito em cima da peça)."""
    digits = "".join(ch for ch in p.id if ch.isdigit())
    return str(int(digits)) if digits else p.id


def owner_colors(parts) -> dict[str, QColor]:
    """nº da solicitação -> cor (só quando as peças vêm de um lote com nº)."""
    tags = sorted({p.tag for p in parts if p.tag})
    return {t: QColor(OWNER_COLORS[i % len(OWNER_COLORS)]) for i, t in enumerate(tags)}


def part_label(p: Part, many_owners: bool) -> str:
    """Texto escrito em cima da peça: nº da solicitação num lote, senão o nº da peça."""
    return p.tag if (many_owners and p.tag) else part_number(p)


def sheet_numbers(parts: dict, placements) -> dict[int, int]:
    """índice interno da placa -> nº mostrado (mesma ordem do arquivo 'todas as placas' e do relatório)."""
    from ..core.sheets import SheetIndex
    return SheetIndex(parts, placements).number
