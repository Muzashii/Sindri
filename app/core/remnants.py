"""Retalhos: sobras de chapa cadastradas por material e usadas pelo encaixe antes de abrir chapa nova.

Um retalho é um contorno (retângulo, polígono desenhado/importado de um DXF, ou a sobra de uma placa
cortada) com buracos opcionais — por exemplo, de onde já saíram peças. Os retalhos ficam em
``retalhos.json``, ao lado do banco de materiais (então também podem ficar na pasta compartilhada).
Sem Qt.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from typing import Iterable, Optional

from shapely.geometry import Polygon, box
from shapely.ops import unary_union

from .fileutil import replace_file

FILE_NAME = "retalhos.json"
SCHEMA = "sindri-retalhos"
VERSION = 1
MIN_AREA_MM2 = 50 * 50        # sobra menor que isso não vale guardar
MIN_WIDTH_MM = 30.0           # nem tira mais estreita que isso


class RemnantError(Exception):
    pass


@dataclass
class Remnant:
    id: str
    material: str
    outline: list                      # [[x, y], …] em mm, caixa começando em (0, 0)
    holes: list = field(default_factory=list)
    name: str = ""
    created: str = ""                  # AAAA-MM-DD
    source: str = ""                   # "retângulo", "DXF …", "sobra da placa 3 (lote …)"
    used: str = ""                     # data em que foi cortado (vazio = disponível)

    @property
    def polygon(self) -> Polygon:
        g = Polygon(self.outline, [h for h in self.holes if len(h) >= 3])
        return g if g.is_valid else g.buffer(0)

    @property
    def size(self) -> tuple[float, float]:
        x0, y0, x1, y1 = self.polygon.bounds
        return x1 - x0, y1 - y0

    @property
    def area(self) -> float:
        return float(self.polygon.area)

    def to_params(self) -> dict:
        """O que o encaixe precisa (NestParams.remnants)."""
        return {"id": self.id, "material": self.material, "outline": [list(p) for p in self.outline],
                "holes": [[list(p) for p in h] for h in self.holes], "name": self.name}

    @staticmethod
    def from_json(d: dict) -> "Remnant":
        try:
            r = Remnant(str(d["id"]), str(d.get("material", "")), [list(map(float, p)) for p in d["outline"]],
                        [[list(map(float, p)) for p in h] for h in d.get("holes", []) or []],
                        str(d.get("name", "")), str(d.get("created", "")), str(d.get("source", "")),
                        str(d.get("used", "")))
        except (KeyError, TypeError, ValueError) as e:
            raise RemnantError(f"retalho inválido: {e}") from e
        if len(r.outline) < 3 or r.polygon.is_empty or r.area <= 0:
            raise RemnantError(f"retalho {r.id} sem área")
        return r


def _normalized(poly: Polygon) -> tuple[list, list]:
    """Contorno e buracos com a caixa começando em (0, 0), simplificados (0,2 mm)."""
    poly = poly.simplify(0.2, preserve_topology=True)
    x0, y0, _, _ = poly.bounds
    ring = lambda r: [[round(x - x0, 3), round(y - y0, 3)] for x, y in list(r.coords)[:-1]]
    return ring(poly.exterior), [ring(h) for h in poly.interiors if len(h.coords) > 3]


def make_remnant(material: str, poly: Polygon, name: str = "", source: str = "") -> Remnant:
    if poly.is_empty or poly.geom_type != "Polygon" or poly.area <= 0:
        raise RemnantError("o retalho precisa ser um polígono com área")
    outline, holes = _normalized(poly)
    return Remnant(uuid.uuid4().hex[:10], material, outline, holes, name,
                   _dt.date.today().isoformat(), source)


def rectangle(material: str, width: float, height: float, name: str = "") -> Remnant:
    if width <= 0 or height <= 0:
        raise RemnantError("informe largura e altura do retalho")
    return make_remnant(material, box(0, 0, width, height), name or f"{width:g} × {height:g}", "retângulo")


def from_dxf(path: str, material: str, name: str = "") -> Remnant:
    """O maior contorno fechado do DXF vira o retalho; os contornos fechados dentro dele, buracos."""
    from .part_builder import build_contours
    from .dxf_import import read_dxf
    raw = read_dxf(path, None, True, None)
    if raw.suggested_units is not None:              # unidade do cabeçalho absurda: mesma correção da importação
        raw = read_dxf(path, raw.suggested_units, True, None)
    cs = [c.polygon for c in build_contours(raw.prims, 0.05, 0.1) if c.closed and c.polygon is not None]
    if not cs:
        raise RemnantError("o DXF não tem nenhum contorno fechado")
    outer = max(cs, key=lambda g: g.area)
    holes = [g for g in cs if g is not outer and outer.contains(g.representative_point()) and g.area < outer.area]
    poly = Polygon(outer.exterior.coords, [list(h.exterior.coords) for h in holes]).buffer(0)
    if poly.geom_type != "Polygon":
        poly = max(poly.geoms, key=lambda g: g.area)
    return make_remnant(material, poly, name or os.path.splitext(os.path.basename(path))[0],
                        f"DXF {os.path.basename(path)}")


def leftover(sheet_shape: Polygon, used: Iterable[Polygon], spacing: float,
             min_area: float = MIN_AREA_MM2, min_width: float = MIN_WIDTH_MM) -> list[Polygon]:
    """O que sobrou de uma placa cortada: a placa menos a envoltória das peças (+ espaçamento), só os
    pedaços grandes o bastante (área e largura) para valer guardar. A margem da borda é aplicada de novo
    quando o retalho for usado."""
    cut = unary_union([g.buffer(max(spacing, 0.0), join_style=2) for g in used])
    rest = sheet_shape.difference(cut)
    out = []
    for g in getattr(rest, "geoms", [rest]):
        if g.geom_type != "Polygon" or g.area < min_area:
            continue
        # tiras estreitas não servem: a abertura com metade da largura mínima tem que sobrar
        core = g.buffer(-min_width / 2, join_style=2)
        if core.is_empty:
            continue
        g = g.intersection(core.buffer(min_width / 2, join_style=2))
        for piece in getattr(g, "geoms", [g]):
            if piece.geom_type == "Polygon" and piece.area >= min_area:
                out.append(piece)
    return sorted(out, key=lambda p: -p.area)


@dataclass
class RemnantStore:
    path: str
    remnants: list[Remnant] = field(default_factory=list)

    @staticmethod
    def load(path: str) -> "RemnantStore":
        if not os.path.isfile(path):
            return RemnantStore(path, [])
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, UnicodeError, json.JSONDecodeError) as e:
            raise RemnantError(f"não consegui ler os retalhos ({path}): {e}") from e
        if not isinstance(data, dict) or data.get("format") != SCHEMA:
            raise RemnantError(f"{path} não é um arquivo de retalhos do Sindri")
        if int(data.get("version", 0)) > VERSION:
            raise RemnantError("arquivo de retalhos de uma versão mais nova do Sindri")
        out = []
        for d in data.get("remnants", []):
            try:
                out.append(Remnant.from_json(d))
            except RemnantError:
                continue                            # um retalho estragado não derruba os outros
        return RemnantStore(path, out)

    def save(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"format": SCHEMA, "version": VERSION,
                       "remnants": [asdict(r) for r in self.remnants]}, fh, ensure_ascii=False, indent=1)
        replace_file(tmp, self.path)

    def _reload(self):
        if os.path.isfile(self.path):
            self.remnants = RemnantStore.load(self.path).remnants

    def add(self, r: Remnant) -> Remnant:
        self._reload()
        self.remnants.append(r)
        self.save()
        return r

    def remove(self, rid: str) -> None:
        self._reload()
        self.remnants = [r for r in self.remnants if r.id != rid]
        self.save()

    def mark_used(self, rid: str, used: bool = True) -> None:
        self._reload()
        for r in self.remnants:
            if r.id == rid:
                r.used = _dt.date.today().isoformat() if used else ""
        self.save()

    def find(self, rid: str) -> Optional[Remnant]:
        return next((r for r in self.remnants if r.id == rid), None)

    def available(self, materials: Optional[Iterable[str]] = None) -> list[Remnant]:
        """Retalhos ainda não cortados (do material pedido), do maior para o menor."""
        from .material_db import key
        mats = {key(m) for m in materials} if materials is not None else None
        out = [r for r in self.remnants if not r.used and (mats is None or key(r.material) in mats)]
        return sorted(out, key=lambda r: -r.area)


def default_path(materials_db_path: str) -> str:
    """retalhos.json na mesma pasta do banco de materiais."""
    return os.path.join(os.path.dirname(os.path.abspath(materials_db_path)), FILE_NAME)

