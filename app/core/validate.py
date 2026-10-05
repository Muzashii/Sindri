"""Verificação final do encaixe com geometria fina (quase exata).

Usa discretização de 0,01 mm do contorno externo original e confere:
* nenhuma peça fora da área útil (margem);
* distância entre peças >= espaçamento (peças dentro de furos são medidas até a borda do furo).
"""
from __future__ import annotations
import math


from shapely.geometry import Polygon, box
from shapely.strtree import STRtree

from .models import NestParams, Part, Placement

FINE_TOL = 0.01


def fine_solid(part: Part) -> Polygon:
    """Polígono sólido (contorno externo fino menos furos) em coordenadas locais."""
    from .part_builder import build_contours
    outer = None
    if part.outer_prim_idx:
        prims = [part.prims[i] for i in part.outer_prim_idx]
        cs = [c for c in build_contours(prims, 0.05, FINE_TOL) if c.closed and c.polygon is not None]
        if cs:
            outer = max((c.polygon for c in cs), key=lambda g: g.area)
    if outer is None:
        outer = part.outer
    holes = [h for h in part.holes if outer.contains(h.representative_point())]
    solid = outer
    for h in holes:
        solid = solid.difference(h)
    return solid


def placed_geometry(solid: Polygon, pl: Placement) -> Polygon:
    from shapely import affinity
    g = solid
    if pl.mirrored:
        g = affinity.scale(g, -1, 1, origin=(0, 0))
    g = affinity.rotate(g, pl.rotation, origin=(0, 0))
    return affinity.translate(g, pl.x, pl.y)


def validate_layout(parts: dict[str, Part], placements: list[Placement], params: NestParams,
                    tol: float = 0.02) -> list[str]:
    issues: list[str] = []
    try:
        params.validate()
    except ValueError as e:
        return [str(e)]
    seen = set()
    solids: dict[str, Polygon] = {}
    by_sheet: dict[int, list[tuple[Placement, Polygon]]] = {}
    m = params.margin
    usable = box(m - tol, m - tol, params.sheet_width - m + tol, params.sheet_height - m + tol)
    for pl in placements:
        key = (pl.part_id, pl.instance)
        if pl.part_id not in parts:
            issues.append(f"Peça desconhecida: {pl.part_id}")
            continue
        part = parts[pl.part_id]
        if key in seen or type(pl.instance) is not int or not 0 <= pl.instance < part.quantity:
            issues.append(f"Quantidade/instância inválida: {part.name} #{pl.instance + 1}")
            continue
        seen.add(key)
        if type(pl.sheet_index) is not int or not 0 <= pl.sheet_index < params.max_sheets or \
                not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (pl.x, pl.y, pl.rotation)):
            issues.append(f"Posição inválida: {part.name}")
            continue
        if pl.part_id not in solids:
            solids[pl.part_id] = fine_solid(part)
        g = placed_geometry(solids[pl.part_id], pl)
        if not usable.covers(g):
            issues.append(f"{part.name} #{pl.instance + 1} fora da área útil da placa {pl.sheet_index + 1}")
        by_sheet.setdefault(pl.sheet_index, []).append((pl, g))
    min_d = params.spacing - tol
    for sheet, items in by_sheet.items():
        if len({parts[pl.part_id].material for pl, _ in items}) > 1:
            issues.append(f"Placa {sheet + 1}: materiais diferentes na mesma placa")
        geoms = [g for _, g in items]
        tree = STRtree(geoms)
        for i, (pl, g) in enumerate(items):
            for j in tree.query(g.buffer(params.spacing)):
                j = int(j)
                if j <= i:
                    continue
                d = g.distance(geoms[j])
                overlap = g.intersection(geoms[j]).area > 1e-8
                if overlap or d < min_d:
                    a = parts[pl.part_id].name
                    b = parts[items[j][0].part_id].name
                    kind = "sobreposição" if overlap else f"distância {d:.2f} mm"
                    issues.append(f"Placa {sheet + 1}: {a} e {b} — {kind} (mínimo {params.spacing} mm)")
    return issues
