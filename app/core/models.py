"""Modelos de dados do Sindri.

Toda a geometria interna está em milímetros, com Y para cima (convenção DXF).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from shapely.geometry import Polygon


# ---------------------------------------------------------------------------
# Primitivas de geometria ORIGINAL (usadas para exportação sem discretizar)
# ---------------------------------------------------------------------------
@dataclass
class Prim:
    """Uma entidade DXF normalizada (em mm, coordenadas WCS, extrusão +Z).

    kind: LINE | ARC | CIRCLE | POLY | ELLIPSE | SPLINE | TEXT | MTEXT
    data: parâmetros específicos do tipo (ver geometry.py)
    layer/color/rgb: aparência original (cor ACI já resolvida, sem BYLAYER/BYBLOCK)
    """
    kind: str
    data: dict
    layer: str = "0"
    color: int = 7
    rgb: Optional[tuple[int, int, int]] = None

    def to_json(self) -> dict:
        d = asdict(self)
        return d

    @staticmethod
    def from_json(d: dict) -> "Prim":
        rgb = tuple(d["rgb"]) if d.get("rgb") else None
        return Prim(d["kind"], d["data"], d.get("layer", "0"), d.get("color", 7), rgb)


@dataclass
class Part:
    """Uma peça (contorno externo + tudo que está dentro dele).

    A geometria está em coordenadas LOCAIS: o centroide do contorno externo
    fica em (0, 0). Rotação e espelhamento são aplicados em torno da origem.
    """
    id: str
    name: str
    source_file: str
    outer: Polygon                     # contorno externo discretizado (local)
    holes: list[Polygon]               # furos aproveitáveis (part-in-part)
    prims: list[Prim]                  # geometria ORIGINAL em coordenadas locais
    outer_prim_idx: list[int]          # índices de prims que formam o contorno externo
    quantity: int = 1
    file_quantity: int = 1
    rotation_locked: bool = False
    warnings: list[str] = field(default_factory=list)
    is_open: bool = False              # peça formada por contorno(s) aberto(s)
    material: str = ""                 # ex.: "MDF 3mm" — peças de materiais diferentes nunca dividem placa
    tag: str = ""                      # nº da solicitação (lote com várias): identifica de quem é a peça

    @property
    def area(self) -> float:
        return float(self.outer.area)

    @property
    def size(self) -> tuple[float, float]:
        minx, miny, maxx, maxy = self.outer.bounds
        return maxx - minx, maxy - miny

    @property
    def display_color(self) -> tuple[int, int, int]:
        from .geometry import prim_rgb
        if self.outer_prim_idx:
            return prim_rgb(self.prims[self.outer_prim_idx[0]])
        if self.prims:
            return prim_rgb(self.prims[0])
        return (0, 0, 0)


@dataclass
class Placement:
    part_id: str
    instance: int
    sheet_index: int
    x: float
    y: float
    rotation: float
    mirrored: bool = False
    locked: bool = False

    def to_json(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_json(d: dict) -> "Placement":
        return Placement(**d)


@dataclass
class NestResult:
    placements: list[Placement]
    sheets_used: int
    utilization: float          # área das peças / área usada das placas
    fitness: float
    unplaced: list[tuple[str, int]] = field(default_factory=list)
    generation: int = 0
    evaluated: int = 0
    sheet_materials: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "placements": [p.to_json() for p in self.placements],
            "sheets_used": self.sheets_used,
            "utilization": self.utilization,
            "fitness": self.fitness,
            "unplaced": [list(u) for u in self.unplaced],
            "sheet_materials": list(self.sheet_materials),
        }

    @staticmethod
    def from_json(d: dict) -> "NestResult":
        return NestResult(
            placements=[Placement.from_json(p) for p in d["placements"]],
            sheets_used=d["sheets_used"],
            utilization=d["utilization"],
            fitness=d["fitness"],
            unplaced=[tuple(u) for u in d.get("unplaced", [])],
            sheet_materials=list(d.get("sheet_materials", [])),
        )


@dataclass
class NestParams:
    sheet_width: float = 600.0
    sheet_height: float = 400.0
    margin: float = 5.0
    spacing: float = 2.0
    rotation_steps: int = 4            # 4 = 0/90/180/270; 8 = a cada 45°
    free_rotation: bool = False        # "livre" = passos de 15°
    allow_mirror: bool = False
    part_in_part: bool = True
    multi_sheet: bool = True
    max_sheets: int = 50
    curve_tolerance: float = 0.1
    join_tolerance: float = 0.05
    population: int = 12
    mutation_rate: float = 0.10
    max_generations_without_improvement: int = 0   # 0 = até o usuário parar
    stop_after_seconds: float = 40.0               # para sozinho após X s sem melhorar (0 = nunca)
    units_override: int = -1          # -1 = usar a unidade declarada no DXF; senão código $INSUNITS
    detail: int = 1                   # contorno no encaixe: 0 preciso, 1 equilibrado, 2 rápido
    ignore_text: bool = True          # textos costumam ser nomes/anotações, não gravação
    excluded_layers: list = field(default_factory=list)   # camadas que não entram no encaixe
    closed_sheets: list = field(default_factory=list)     # placas já cortadas: não recebem peças novas

    def import_kwargs(self) -> dict:  # noqa: D401
        return {"units_override": self.units_override if self.units_override >= 0 else None,
                "ignore_text": self.ignore_text, "excluded_layers": set(self.excluded_layers)}

    def rotations(self) -> list[float]:
        steps = 24 if self.free_rotation else max(1, int(self.rotation_steps))
        return [round(360.0 * i / steps, 6) for i in range(steps)]

    def to_json(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_json(d: dict) -> "NestParams":
        p = NestParams()
        for k, v in d.items():
            if hasattr(p, k):
                setattr(p, k, v)
        return p


@dataclass
class ImportReport:
    """Resultado da importação de um ou mais arquivos."""
    parts: list[Part]
    preview: list[tuple[Prim, bool]]   # (primitiva em coordenadas do arquivo, problema?)
    warnings: list[str]
    files: list[str]
    unit_notes: dict[str, str] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)
