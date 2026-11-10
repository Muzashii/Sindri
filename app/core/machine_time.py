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
    raw_seconds: float = 0.0          # estimativa antes da calibração (0 = não calibrada)
    calibrated_n: int = 0             # nº de cortes reais usados na calibração (0 = sem calibração)
    factor: float = 1.0

    @property
    def minutes(self) -> float:
        return self.seconds / 60.0

    @property
    def text(self) -> str:
        m = self.minutes
        txt = f"~{m:.0f} min" if m >= 1 else f"~{max(1, round(self.seconds)):.0f} s"
        return txt + (" (estimado com velocidade padrão)" if self.fallback else "")

    @property
    def raw(self) -> float:
        """Segundos da estimativa pura (sem o fator dos cortes reais)."""
        return self.raw_seconds if self.calibrated_n else self.seconds

    @property
    def calibration_text(self) -> str:
        if not self.calibrated_n:
            return ""
        return f"calibrado com {self.calibrated_n} cortes reais (×{fmt_num(self.factor, 2)})"


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


# ------------------------------------------------------------------ tempo real × estimado
HISTORY_NAME = "tempos_reais.json"
HISTORY_SCHEMA = "sindri-tempos"
MIN_REAL_S = 20.0                 # menos que isso: marcou sem cortar
MAX_REAL_S = 4 * 3600.0           # mais que isso: esqueceu de marcar (almoço, outro dia)
MIN_MEASURES = 3
FACTOR_RANGE = (0.5, 3.0)


def valid_measure(estimated_s: float, real_s: float) -> bool:
    """Medida que serve para calibrar: placa aberta e cortada num tempo plausível."""
    try:
        e, r = float(estimated_s), float(real_s)
    except (TypeError, ValueError):
        return False
    return e == e and r == r and e > 0 and MIN_REAL_S <= r <= MAX_REAL_S


def correction_factor(history: Iterable[dict], min_n: int = MIN_MEASURES,
                      limits: tuple[float, float] = FACTOR_RANGE) -> tuple[float, int]:
    """(fator, nº de medidas usadas): mediana de real ÷ estimado dos cortes válidos, limitada a 0,5–3×.
    Com menos de ``min_n`` medidas válidas devolve (1.0, 0) — a estimativa fica como está."""
    ratios = []
    for h in history or []:
        if not isinstance(h, dict):
            continue
        e, r = h.get("estimado_s"), h.get("real_s")
        if valid_measure(e, r):
            ratios.append(float(r) / float(e))
    if len(ratios) < max(1, min_n):
        return 1.0, 0
    ratios.sort()
    n = len(ratios)
    med = ratios[n // 2] if n % 2 else (ratios[n // 2 - 1] + ratios[n // 2]) / 2.0
    return min(limits[1], max(limits[0], med)), n


def calibrated(t: SheetTime, factor: float, n: int) -> SheetTime:
    """Cópia da estimativa multiplicada pelo fator dos cortes reais (guarda a estimativa pura)."""
    if not n or factor <= 0:
        return t
    return SheetTime(seconds=t.seconds * factor, cut_length=t.cut_length, travel=t.travel, starts=t.starts,
                     by_op={k: v * factor for k, v in t.by_op.items()}, fallback=set(t.fallback),
                     raw_seconds=t.seconds, calibrated_n=int(n), factor=float(factor))


def fmt_duration(seconds: float) -> str:
    """'14 min', '1 h 05 min' ou '45 s'."""
    s = max(0.0, float(seconds))
    if s < 60:
        return f"{max(1, round(s)):.0f} s"
    m = int(round(s / 60.0))
    return f"{m} min" if m < 60 else f"{m // 60} h {m % 60:02d} min"


def fmt_clock(seconds: float) -> str:
    """Cronômetro: '03:27' ou '1:03:27'."""
    s = int(max(0.0, float(seconds)))
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def load_time_history(path: str) -> list[dict]:
    """Cortes medidos ([] se o arquivo não existe ou está estragado — nunca derruba a exportação)."""
    import json
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, UnicodeError, ValueError):
        return []
    if not isinstance(data, dict) or data.get("format") != HISTORY_SCHEMA:
        return []
    return [h for h in data.get("cortes", []) if isinstance(h, dict)]


def _save_time_history(path: str, items: list[dict]) -> None:
    import json
    from .fileutil import replace_file
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"format": HISTORY_SCHEMA, "version": 1, "cortes": items}, fh, ensure_ascii=False, indent=1)
    replace_file(tmp, path)


def append_time_history(path: str, entry: dict, keep: int = 2000) -> bool:
    """Acrescenta um corte medido ``{id, placa, arquivo, material, estimado_s, real_s, data}``.
    Medida absurda (< 20 s, > 4 h, sem estimativa) é descartada: devolve False e não grava."""
    if not valid_measure(entry.get("estimado_s"), entry.get("real_s")):
        return False
    items = load_time_history(path)
    items.append({**entry, "estimado_s": round(float(entry["estimado_s"]), 1),
                  "real_s": round(float(entry["real_s"]), 1)})
    _save_time_history(path, items[-keep:])
    return True


def remove_time_entry(path: str, entry_id: str) -> bool:
    """Tira uma medida (placa desmarcada logo depois de marcar por engano)."""
    items = load_time_history(path)
    kept = [h for h in items if h.get("id") != entry_id]
    if not entry_id or len(kept) == len(items):
        return False
    _save_time_history(path, kept)
    return True
