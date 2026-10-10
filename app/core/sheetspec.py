"""Formato de cada placa do encaixe: chapa inteira (do tamanho do material) ou retalho (sem Qt).

Uma placa é sempre desenhada/exportada com a origem no canto inferior esquerdo da caixa dela. Para a
chapa inteira, a área útil é o retângulo menos a margem; para um retalho, é o contorno do retalho
encolhido pela margem (e sem os buracos de onde já saíram peças).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from shapely.geometry import Polygon, box

from .models import NestParams


@dataclass(frozen=True)
class SheetSpec:
    width: float
    height: float
    margin: float
    spacing: float
    remnant_id: str = ""
    outline: tuple = ()             # retalho: contorno ((x, y), …) em mm, com a caixa começando em (0, 0)
    holes: tuple = ()               # retalho: buracos (cada um uma tupla de pontos)
    name: str = ""

    @property
    def is_remnant(self) -> bool:
        return bool(self.remnant_id)

    def shape(self) -> Polygon:
        """O pedaço de material (antes da margem)."""
        if self.outline:
            g = Polygon(self.outline, [h for h in self.holes if len(h) >= 3])
            return g if g.is_valid else g.buffer(0)
        return box(0, 0, self.width, self.height)

    def usable(self, extra: float = 0.0) -> Polygon:
        """Área onde a peça (desenho real) pode ficar. ``extra`` alarga (folga de tolerância)."""
        if not self.outline:
            m = self.margin
            return box(m - extra, m - extra, self.width - m + extra, self.height - m + extra)
        g = self.shape().buffer(-self.margin + extra, join_style=2)
        return g

    @property
    def area(self) -> float:
        """Área do material (para o aproveitamento)."""
        return float(self.shape().area)


def standard_spec(params: NestParams, material: Optional[str]) -> SheetSpec:
    q = params.for_material(material)
    return SheetSpec(q.sheet_width, q.sheet_height, q.margin, q.spacing)


def remnant_spec(params: NestParams, remnant: dict) -> SheetSpec:
    """Retalho {"id", "material", "outline", "holes"} -> SheetSpec (margem/espaçamento do material)."""
    q = params.for_material(remnant.get("material", ""))
    outline = tuple(tuple(map(float, p)) for p in remnant["outline"])
    holes = tuple(tuple(tuple(map(float, p)) for p in h) for h in remnant.get("holes", []) or [])
    poly = Polygon(outline)
    x0, y0, x1, y1 = poly.bounds
    if abs(x0) > 1e-9 or abs(y0) > 1e-9:          # normaliza: caixa começa em (0, 0)
        outline = tuple((x - x0, y - y0) for x, y in outline)
        holes = tuple(tuple((x - x0, y - y0) for x, y in h) for h in holes)
    return SheetSpec(x1 - x0, y1 - y0, q.margin, q.spacing, str(remnant["id"]), outline, holes,
                     str(remnant.get("name", "")))


def sheet_specs(params: NestParams, materials: dict[int, str],
                remnants_of_sheet: Optional[dict[int, str]] = None,
                remnants: Optional[list] = None) -> dict[int, SheetSpec]:
    """{placa: SheetSpec} a partir do material de cada placa e do retalho usado (se houver)."""
    by_id = {str(r.get("id")): r for r in (remnants if remnants is not None else params.remnants) or []}
    out = {}
    for si, mat in materials.items():
        rid = (remnants_of_sheet or {}).get(si)
        if rid and rid in by_id:
            out[si] = remnant_spec(params, by_id[rid])
        else:
            out[si] = standard_spec(params, mat)
    return out
