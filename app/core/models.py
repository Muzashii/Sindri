"""Modelos de dados do Sindri.

Toda a geometria interna está em milímetros, com Y para cima (convenção DXF).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict, fields
import hashlib
import json
import math
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
    def from_json(d: dict) -> Prim:
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
    def identity(self) -> str:
        """Identidade do desenho local, independente do número na lista e do caminho."""
        def normalized(value):
            if isinstance(value, float):
                return round(value, 8)
            if isinstance(value, (list, tuple)):
                return [normalized(v) for v in value]
            if isinstance(value, dict):
                return {k: normalized(v) for k, v in value.items()}
            return value
        prims = sorted(json.dumps(normalized(p.to_json()), sort_keys=True) for p in self.prims)
        payload = json.dumps([self.material, self.tag, prims], ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

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
    def from_json(d: dict) -> Placement:
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
    sheet_remnants: list[str] = field(default_factory=list)   # id do retalho de cada placa ("" = chapa)

    def to_json(self) -> dict:
        return {
            "placements": [p.to_json() for p in self.placements],
            "sheets_used": self.sheets_used,
            "utilization": self.utilization,
            "fitness": self.fitness,
            "unplaced": [list(u) for u in self.unplaced],
            "sheet_materials": list(self.sheet_materials),
            "sheet_remnants": list(self.sheet_remnants),
        }

    @staticmethod
    def from_json(d: dict) -> NestResult:
        return NestResult(
            placements=[Placement.from_json(p) for p in d["placements"]],
            sheets_used=d["sheets_used"],
            utilization=d["utilization"],
            fitness=d["fitness"],
            unplaced=[tuple(u) for u in d.get("unplaced", [])],
            sheet_materials=list(d.get("sheet_materials", [])),
            sheet_remnants=[str(x) for x in d.get("sheet_remnants", [])],
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
    # chapa por material (do banco de materiais): {material: {"sheet_width", "sheet_height", "margin",
    # "spacing", "grain"}} — só as chaves informadas substituem os valores acima
    material_sheets: dict = field(default_factory=dict)
    # retalhos que podem ser usados ANTES de abrir chapa nova: [{"id", "material", "outline", "holes"}]
    remnants: list = field(default_factory=list)
    # placas que já existem e são retalhos (encaixar "só o que falta"): [[índice da placa, id do retalho]]
    sheet_remnants: list = field(default_factory=list)

    def import_kwargs(self) -> dict:
        return {"units_override": self.units_override if self.units_override >= 0 else None,
                "ignore_text": self.ignore_text, "excluded_layers": set(self.excluded_layers)}

    def rotations(self, material: Optional[str] = None) -> list[float]:
        """Rotações permitidas. Material com veio (``grain``): só 0° e 180°."""
        steps = 24 if self.free_rotation else max(1, int(self.rotation_steps))
        rots = [round(360.0 * i / steps, 6) for i in range(steps)]
        if material is not None and (self.material_sheets.get(material) or {}).get("grain"):
            rots = [r for r in rots if r in (0.0, 180.0)] or [0.0]
        return rots

    MATERIAL_KEYS = ("sheet_width", "sheet_height", "margin", "spacing")

    def for_material(self, material: Optional[str]) -> "NestParams":
        """Cópia com a chapa/margem/espaçamento daquele material (banco de materiais), se houver."""
        o = self.material_sheets.get(material or "") if material is not None else None
        if not o:
            return self
        import copy
        p = copy.copy(self)
        for k in self.MATERIAL_KEYS:
            v = o.get(k)
            if isinstance(v, (int, float)) and math.isfinite(v) and (v > 0 or (k == "margin" and v >= 0)):
                setattr(p, k, float(v))
        return p

    def to_json(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_json(d: dict) -> NestParams:
        p = NestParams()
        names = {f.name for f in fields(p)}
        for k, v in d.items():
            if k in names:
                setattr(p, k, v)
        p.validate()
        return p

    def validate(self) -> None:
        for name in ("sheet_width", "sheet_height", "curve_tolerance", "join_tolerance"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name}: informe um número positivo e finito.")
        for name in ("margin", "spacing", "stop_after_seconds", "mutation_rate"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{name}: informe um número não negativo e finito.")
        if 2 * self.margin >= min(self.sheet_width, self.sheet_height):
            raise ValueError("A margem deixa a placa sem área útil.")
        for name in ("rotation_steps", "max_sheets", "population"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name}: informe um inteiro positivo.")
        if self.mutation_rate > 1:
            raise ValueError("mutation_rate deve estar entre 0 e 1.")
        if not isinstance(self.material_sheets, dict) or not isinstance(self.remnants, list):
            raise ValueError("material_sheets/remnants inválidos.")
        for m in self.material_sheets:
            q = self.for_material(m)
            if 2 * q.margin >= min(q.sheet_width, q.sheet_height):
                raise ValueError(f"{m}: a margem deixa a chapa sem área útil.")


@dataclass
class ImportReport:
    """Resultado da importação de um ou mais arquivos."""
    parts: list[Part]
    preview: list[tuple[Prim, bool]]   # (primitiva em coordenadas do arquivo, problema?)
    warnings: list[str]
    files: list[str]
    unit_notes: dict[str, str] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)
