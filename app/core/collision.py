"""Checagem rápida de colisão para o ajuste manual (usa os mesmos polígonos do encaixe)."""
from __future__ import annotations

from shapely import affinity
from shapely.geometry import Polygon, box
from shapely.strtree import STRtree

from .geometry import from_int_path
from .models import NestParams, Part, Placement
from .nfp import NFPCache
from .optimizer import shapes_from_parts

TOL_AREA = 1e-4  # mm²


class CollisionChecker:
    def __init__(self, parts: list[Part], params: NestParams):
        self.params = params
        self.material = {p.id: p.material for p in parts}
        shapes = shapes_from_parts(parts, params)
        self.cache = NFPCache(shapes, params.spacing, params.curve_tolerance, params.part_in_part,
                              params.detail)
        self._solids: dict[tuple, Polygon] = {}
        half = params.spacing / 2.0
        m = params.margin
        self.usable = box(m - half - 0.01, m - half - 0.01,
                          params.sheet_width - m + half + 0.01, params.sheet_height - m + half + 0.01)

    def solid(self, pid: str, rot: float, mirror: bool) -> Polygon:
        key = (pid, round(rot % 360.0, 4), bool(mirror))
        g = self._solids.get(key)
        if g is None:
            v = self.cache.variant(pid, rot, mirror)
            g = Polygon(from_int_path(v.path), [from_int_path(h) for h in v.holes])
            if not g.is_valid:
                g = g.buffer(0)
            # encolhe 5 µm: peças que apenas encostam (como o encaixe permite) não contam como colisão
            g = g.buffer(-0.005, join_style=2)
            self._solids[key] = g
        return g

    def placed(self, pl: Placement) -> Polygon:
        return affinity.translate(self.solid(pl.part_id, pl.rotation, pl.mirrored), pl.x, pl.y)

    def colliding(self, placements: list[Placement]) -> set[int]:
        """Índices das peças que colidem com outra ou saem da área útil."""
        bad: set[int] = set()
        by_sheet: dict[int, list[int]] = {}
        geoms = [self.placed(pl) for pl in placements]
        for i, pl in enumerate(placements):
            by_sheet.setdefault(pl.sheet_index, []).append(i)
            if not self.usable.covers(geoms[i]):
                bad.add(i)
        for idxs in by_sheet.values():
            # material errado na placa (ex.: peça de 6mm arrastada para placa de 3mm)
            mats = [self.material.get(placements[i].part_id, "") for i in idxs]
            if len(set(mats)) > 1:
                main = max(set(mats), key=mats.count)
                bad.update(i for i, m in zip(idxs, mats) if m != main)
            gs = [geoms[i] for i in idxs]
            tree = STRtree(gs)
            for a, i in enumerate(idxs):
                for b in tree.query(gs[a]):
                    b = int(b)
                    if b <= a:
                        continue
                    j = idxs[b]
                    if gs[a].intersection(gs[b]).area > TOL_AREA:
                        bad.add(i)
                        bad.add(j)
        return bad
