"""A peça sobrevive ao laser? Checagens de fabricabilidade e selo por solicitação (sem Qt).

São avisos (não bloqueiam): parede fina, furo menor que o kerf, peça pequena demais para a mesa, peça
maior que a área útil, rasgo de encaixe sem compensação e texto que não virou curva. O que bloqueia é o
material (veja material_safety). Os limites são pontos de partida; a espessura, o kerf e a peça mínima
vêm do banco de materiais quando o material está cadastrado.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Iterable, Optional

from shapely.geometry import Polygon
from shapely.ops import unary_union

from . import material_safety
from .models import Part

THIN_FACTOR = 1.5            # parede mais fina que 1,5 × a espessura quebra ou queima
DEFAULT_THICKNESS = 3.0      # material sem espessura cadastrada
DEFAULT_KERF = 0.15          # material sem kerf medido (ponto de partida)
DEFAULT_MIN_PART = 10.0      # menor que 10 × 10 mm cai na colmeia
SLOT_TOL = 0.02              # rasgo com largura = espessura nominal (± 0,02 mm): sem compensação


@dataclass
class Issue:
    kind: str                               # parede_fina | furo_pequeno | peca_pequena | maior_que_chapa |
    message: str                            # rasgo_sem_compensacao | texto
    geom: Optional[object] = None           # região do problema, em coordenadas da peça (para pintar)


@dataclass
class MaterialLimits:
    thickness: float = 0.0
    kerf: float = 0.0
    min_part: float = DEFAULT_MIN_PART
    usable: Optional[tuple[float, float]] = None   # largura × altura úteis da chapa (mm)

    @staticmethod
    def from_db(m, usable=None) -> "MaterialLimits":
        if m is None:
            return MaterialLimits(usable=usable)
        return MaterialLimits(float(m.thickness or 0), float(m.kerf or 0),
                              float(m.min_part or DEFAULT_MIN_PART), usable)


def _inner_cut_polygons(part: Part) -> list[Polygon]:
    """Contornos internos que são CORTE (furos e rasgos), como polígonos em coordenadas da peça."""
    from .operations import CUT_INNER, prim_operations
    from .part_builder import build_contours
    ops = prim_operations(part)
    prims = [p for p, op in zip(part.prims, ops) if op == CUT_INNER]
    if not prims:
        return []
    return [c.polygon for c in build_contours(prims, 0.05, 0.05) if c.closed and c.polygon is not None]


def thin_regions(solid, limit: float):
    """Trechos de material mais finos que ``limit``: abertura morfológica com quinas vivas (cantos vivos
    não contam como "fino") e só o que sobra com área relevante."""
    if solid is None or solid.is_empty or limit <= 0:
        return None
    r = limit / 2.0
    opened = solid.buffer(-r, join_style=2, mitre_limit=10).buffer(r, join_style=2, mitre_limit=10)
    diff = solid.difference(opened)
    keep = [g for g in getattr(diff, "geoms", [diff])
            if not g.is_empty and g.area > 0.25 * limit * limit and g.geom_type == "Polygon"
            and g.buffer(-limit * 0.05).area > 0]
    return unary_union(keep) if keep else None


def check_part(part: Part, limits: MaterialLimits) -> list[Issue]:
    from .validate import fine_solid
    issues: list[Issue] = []
    t = limits.thickness or DEFAULT_THICKNESS
    kerf = limits.kerf or DEFAULT_KERF
    w, h = part.size
    # peça pequena demais para a mesa
    mp = limits.min_part or DEFAULT_MIN_PART
    if w < mp and h < mp:
        issues.append(Issue("peca_pequena",
                            f"Peça pequena ({w:.1f} × {h:.1f} mm): pode cair na colmeia ou levantar com o sopro. "
                            "Considere micro-pontes (tabs) só nesta peça."))
    # maior que a área útil (aproximado pela caixa: nunca encaixa em nenhuma rotação de 90°)
    if limits.usable:
        uw, uh = limits.usable
        if min(w, h) > min(uw, uh) or max(w, h) > max(uw, uh):
            issues.append(Issue("maior_que_chapa", f"Maior que a área útil da chapa ({uw:g} × {uh:g} mm): "
                                                   "não vai ter lugar."))
    if part.is_open:
        return issues
    solid = fine_solid(part)
    # paredes finas (entre furo e borda, tiras estreitas)
    limit = THIN_FACTOR * t
    thin = thin_regions(solid, limit)
    if thin is not None and not thin.is_empty:
        issues.append(Issue("parede_fina", f"Parede mais fina que {limit:.1f} mm (1,5 × espessura): pode quebrar "
                                           "ou queimar. Os trechos aparecem em laranja.", thin))
    # furos e rasgos
    for hole in _inner_cut_polygons(part):
        x0, y0, x1, y1 = hole.bounds
        small, big = sorted((x1 - x0, y1 - y0))
        if small < 2 * kerf:
            issues.append(Issue("furo_pequeno", f"Furo de {small:.2f} mm, menor que 2 × kerf ({2 * kerf:.2f} mm): "
                                                "vira um ponto queimado.", hole))
            continue
        rect_like = hole.area >= 0.97 * (x1 - x0) * (y1 - y0) and big > 2 * small
        if limits.thickness and rect_like and abs(small - limits.thickness) <= SLOT_TOL:
            issues.append(Issue("rasgo_sem_compensacao",
                                f"Rasgo de {small:.2f} mm = espessura nominal: sem compensar o kerf o encaixe fica "
                                f"frouxo (kerf {kerf:.2f} mm). Desenhe o rasgo com espessura − kerf.", hole))
    # texto que não virou curva
    n_text = sum(1 for p in part.prims if p.kind in ("TEXT", "MTEXT"))
    if n_text:
        issues.append(Issue("texto", f"{n_text} texto(s) não convertido(s) em curva: no RDWorks a fonte pode trocar "
                                     "ou o texto sumir. Converta em curvas no CAD (ou use “Ignorar textos”)."))
    return issues


# ---------------------------------------------------------------------------------- selo por solicitação
@dataclass
class Seal:
    status: str = material_safety.OK                # ok | atencao | bloqueado
    reasons: list[str] = field(default_factory=list)

    @property
    def icon(self) -> str:
        return {"ok": "✓", "atencao": "⚠", "bloqueado": "⛔"}[self.status]

    @property
    def label(self) -> str:
        return {"ok": "OK", "atencao": "Atenção", "bloqueado": "Bloqueado"}[self.status]

    def to_json(self) -> dict:
        return {"status": self.status, "reasons": list(self.reasons)}


def seal_for(materials: Iterable[str], issues: Iterable[Issue] = (), non_dxf: int = 0,
             lookup=None) -> Seal:
    """Selo de uma solicitação: material (bloqueia) + fabricabilidade e arquivos que não são DXF (atenção)."""
    reasons: list[str] = []
    results = []
    for m in dict.fromkeys(materials):
        flag, why = lookup(m) if lookup else (False, "")
        r = material_safety.check_material(m, flag, why)
        results.append(r)
        if r.status != material_safety.OK:
            reasons.append(f"{m or 'sem material'}: {r.reason}")
    status = material_safety.worst(results).status
    kinds: dict[str, int] = {}
    for i in issues:
        kinds[i.kind] = kinds.get(i.kind, 0) + 1
    names = {"parede_fina": "parede fina", "furo_pequeno": "furo menor que o kerf", "peca_pequena": "peça pequena",
             "maior_que_chapa": "peça maior que a chapa", "rasgo_sem_compensacao": "rasgo sem compensação",
             "texto": "texto não convertido"}
    for k, n in kinds.items():
        reasons.append(f"{n} peça(s) com {names.get(k, k)}")
    if non_dxf:
        reasons.append(f"{non_dxf} arquivo(s) que não são DXF")
    if status == material_safety.OK and (kinds or non_dxf):
        status = material_safety.WARN
    return Seal(status, reasons)


class SealStore:
    """Selos já calculados (por nº da solicitação), para a lista da intranet mostrar antes de juntar no lote."""

    def __init__(self, path: str):
        self.path = path

    def load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def get(self, code) -> Optional[Seal]:
        d = self.load().get(str(code))
        if not isinstance(d, dict) or d.get("status") not in ("ok", "atencao", "bloqueado"):
            return None
        return Seal(d["status"], [str(x) for x in d.get("reasons", [])])

    def put_many(self, seals: dict) -> None:
        data = self.load()
        data.update({str(k): v.to_json() for k, v in seals.items()})
        if len(data) > 2000:                       # só os mais recentes
            data = dict(list(data.items())[-2000:])
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        from .fileutil import replace_file
        replace_file(tmp, self.path)


def default_seal_path() -> str:
    from .intranet import default_base_folder
    return os.path.join(os.path.dirname(default_base_folder()), "selos.json")
