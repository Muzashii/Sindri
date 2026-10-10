"""Tempo de máquina estimado por placa e rateio por solicitação (sem Qt).

Estimativa simples e calibrável:
    vetor  = comprimento ÷ velocidade × passadas + inícios de contorno × constante
    scan   = (altura ÷ intervalo) × (largura ÷ velocidade + volta) × passadas
    deslocamento entre peças ÷ velocidade de deslocamento
Calibre as duas constantes comparando com o tempo que o RDWorks mostra para a mesma placa.
"""
from __future__ import annotations

import csv
import io
import math
import os
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

from .models import Part, Placement

# velocidade usada quando o material ainda não tem a camada cadastrada (estimativa, não valor de máquina)
FALLBACK_SPEED = {"corte": 15.0, "vinco": 80.0, "gravacao_vetorial": 100.0, "gravacao_raster": 300.0}


@dataclass
class TimeModel:
    travel_speed: float = 300.0       # mm/s com o laser desligado
    start_overhead: float = 0.3       # s por contorno (acelerar, acender, frear)
    scan_turnaround: float = 0.05     # s por linha de scan (volta da cabeça)


@dataclass
class SheetTime:
    seconds: float = 0.0
    cut_length: float = 0.0           # mm de laser ligado (vetor), já com passadas
    travel: float = 0.0               # mm de deslocamento entre peças
    starts: int = 0
    by_op: dict = field(default_factory=dict)       # camada -> segundos
    fallback: set = field(default_factory=set)      # camadas estimadas sem velocidade cadastrada

    @property
    def minutes(self) -> float:
        return self.seconds / 60.0

    @property
    def text(self) -> str:
        m = self.minutes
        txt = f"~{m:.0f} min" if m >= 1 else f"~{max(1, round(self.seconds)):.0f} s"
        return txt + (" (estimado com velocidade padrão)" if self.fallback else "")


def _part_profile(part: Part, color_ops: Optional[dict]) -> dict:
    """{camada: (comprimento mm, nº de contornos, caixa da gravação raster ou None)} — cacheado na peça."""
    from .geometry import flatten_prim
    from .operations import is_text, layer_op, prim_operations
    from .part_builder import build_contours
    key = ("_time_profile", tuple(sorted((color_ops or {}).items())))
    cache = part.__dict__.setdefault("_time_cache", {})
    if key in cache:
        return cache[key]
    ops = prim_operations(part, color_ops)
    by: dict[str, list] = {}
    for p, op in zip(part.prims, ops):
        if is_text(p):
            continue
        by.setdefault(layer_op(op), []).append(p)
    out = {}
    for op, prims in by.items():
        length = 0.0
        for p in prims:
            pts = flatten_prim(p, 0.2)
            if len(pts) > 1:
                length += float(sum(math.hypot(*(b - a)) for a, b in zip(pts[:-1], pts[1:])))
        contours = build_contours(prims, 0.05, 0.2)
        bbox = None
        if op == "gravacao_raster":
            xs = [pt for p in prims for pt in flatten_prim(p, 0.5)]
            if xs:
                import numpy as np
                arr = np.asarray(xs)
                bbox = (float(arr[:, 0].max() - arr[:, 0].min()), float(arr[:, 1].max() - arr[:, 1].min()))
        out[op] = (length, max(1, len(contours)), bbox)
    cache[key] = out
    return out


def sheet_time(pmap: dict[str, Part], placements: list[Placement], op_params: Callable[[str, str], dict],
               color_ops: Optional[dict] = None, model: Optional[TimeModel] = None,
               start: tuple[float, float] = (0.0, 0.0)) -> SheetTime:
    """Tempo de UMA placa. ``op_params(material, camada)`` devolve {"speed", "passes", "interval", "mode"}."""
    model = model or TimeModel()
    out = SheetTime()
    if not placements:
        return out
    cx, cy = start
    for pl in sorted(placements, key=lambda q: (q.x - start[0]) ** 2 + (q.y - start[1]) ** 2):
        out.travel += math.hypot(pl.x - cx, pl.y - cy)
        cx, cy = pl.x, pl.y
        part = pmap[pl.part_id]
        for op, (length, n_contours, bbox) in _part_profile(part, color_ops).items():
            e = op_params(part.material or "", op) or {}
            speed = float(e.get("speed") or 0)
            if speed <= 0:
                speed = FALLBACK_SPEED.get(op, 50.0)
                out.fallback.add(op)
            passes = max(1, int(e.get("passes") or 1))
            mode = e.get("mode") or ("scan" if op == "gravacao_raster" else "corte")
            if mode == "scan" and bbox:
                interval = float(e.get("interval") or 0.1)
                lines = bbox[1] / max(0.01, interval)
                t = lines * (bbox[0] / speed + model.scan_turnaround) * passes
            else:
                t = (length / speed + n_contours * model.start_overhead) * passes
                out.cut_length += length * passes
                out.starts += n_contours * passes
            out.by_op[op] = out.by_op.get(op, 0.0) + t
            out.seconds += t
    out.seconds += out.travel / max(1.0, model.travel_speed)
    return out


@dataclass
class RequestShare:
    tag: str
    material: str
    sheets: set = field(default_factory=set)
    parts_area: float = 0.0           # mm² líquidos das peças
    sheet_area: float = 0.0           # mm² de chapa rateados pela área ocupada
    minutes: float = 0.0              # minutos de máquina rateados
    copies: int = 0
    saved: float = 0.0                # mm² de sobra guardada como retalho (rateada): não é perda


def request_shares(pmap: dict[str, Part], placements: list[Placement], sheet_area: dict[int, float],
                   sheet_minutes: dict[int, float],
                   sheet_saved: Optional[dict[int, float]] = None) -> dict[tuple[str, str], RequestShare]:
    """Rateio de chapa e de minutos por (solicitação, material), pela área líquida que cada uma ocupa
    em cada placa. Peças sem solicitação entram como solicitação ""."""
    from .sheets import net_area
    by_sheet: dict[int, list[Placement]] = {}
    for pl in placements:
        if pl.part_id in pmap:
            by_sheet.setdefault(pl.sheet_index, []).append(pl)
    out: dict[tuple[str, str], RequestShare] = {}
    for si, pls in by_sheet.items():
        total = sum(net_area(pmap[pl.part_id]) for pl in pls) or 1.0
        for pl in pls:
            part = pmap[pl.part_id]
            k = (part.tag or "", part.material or "")
            share = out.setdefault(k, RequestShare(k[0], k[1]))
            a = net_area(part)
            share.sheets.add(si)
            share.parts_area += a
            share.copies += 1
            share.sheet_area += sheet_area.get(si, 0.0) * a / total
            share.minutes += sheet_minutes.get(si, 0.0) * a / total
            share.saved += (sheet_saved or {}).get(si, 0.0) * a / total
    return out


CSV_FIELDS = ["data", "lote", "solicitacao", "aluno", "material", "placas", "copias", "area_pecas_cm2",
              "area_chapa_cm2", "minutos_maquina", "aproveitamento_pct", "retalho_salvo_cm2"]


def append_monthly_csv(folder: str, rows: Iterable[dict], when=None) -> str:
    """Grava/atualiza ``AAAA-MM.csv`` (separador ';', vírgula decimal, para abrir direto no Excel em pt-BR).
    Linhas do mesmo lote + solicitação + material são substituídas (exportar de novo não duplica)."""
    import datetime as _dt
    when = when or _dt.date.today()
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{when:%Y-%m}.csv")
    existing: list[dict] = []
    if os.path.isfile(path):
        with open(path, encoding="utf-8-sig", newline="") as fh:
            existing = list(csv.DictReader(fh, delimiter=";"))
    rows = list(rows)
    keys = {(r["lote"], r["solicitacao"], r["material"]) for r in rows}
    kept = [r for r in existing if (r.get("lote"), r.get("solicitacao"), r.get("material")) not in keys]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_FIELDS, delimiter=";", extrasaction="ignore")
    w.writeheader()
    for r in kept + rows:
        w.writerow(r)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as fh:
        fh.write(buf.getvalue())
    from .fileutil import replace_file
    replace_file(tmp, path)
    return path


def fmt_num(v: float, dec: int = 1) -> str:
    return f"{v:.{dec}f}".replace(".", ",")
