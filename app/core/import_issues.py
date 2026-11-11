"""Avisos da importação com local e solução.

Cada aviso diz em que arquivo e onde (caixas em mm, no sistema de coordenadas do próprio arquivo)
está o problema, se o Sindri já resolveu sozinho e, para contornos abertos, quais pontas estão perto
o bastante para serem ligadas por um trecho reto ("aberturas").
"""
from __future__ import annotations

import math
import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .models import Prim

FIXED = "fixed"   # o Sindri já resolveu (só informativo)
CHECK = "check"   # conferir: pode ser erro no desenho do aluno
ERROR = "error"   # arquivo não lido ou sem nada para cortar

SEVERITY_ORDER = {ERROR: 0, CHECK: 1, FIXED: 2}

# Distância máxima entre duas pontas para sugerir ligá-las. Acima disso é mais provável faltar um
# trecho de verdade (ou ser uma linha proposital) do que uma folga de desenho.
GAP_SEARCH_MM = 5.0


@dataclass
class Gap:
    """Abertura entre duas pontas de contorno aberto, que pode ser fechada com um trecho reto."""
    a: tuple[float, float]
    b: tuple[float, float]
    dist: float
    layer: str = "0"
    color: int = 7
    rgb: Optional[tuple[int, int, int]] = None

    def to_json(self) -> dict:
        return {"a": list(self.a), "b": list(self.b), "dist": self.dist, "layer": self.layer,
                "color": self.color, "rgb": list(self.rgb) if self.rgb else None}

    @staticmethod
    def from_json(d: dict) -> Gap:
        return Gap(tuple(d["a"]), tuple(d["b"]), float(d["dist"]), d.get("layer", "0"),
                   int(d.get("color", 7)), tuple(d["rgb"]) if d.get("rgb") else None)


@dataclass
class ImportIssue:
    file: str
    kind: str
    severity: str
    message: str                      # frase sem o nome do arquivo
    count: int = 1
    where: list[list[float]] = field(default_factory=list)       # [x0, y0, x1, y1] (mm, coords do arquivo)
    gaps: list[Gap] = field(default_factory=list)
    loose_ends: list[list[float]] = field(default_factory=list)  # pontas sem outra perto

    @property
    def key(self) -> str:
        """Identidade estável entre importações (para lembrar o que o operador já conferiu)."""
        boxes = ";".join(",".join(f"{v:.1f}" for v in b) for b in sorted(self.where))
        return f"{os.path.normcase(os.path.abspath(self.file))}|{self.kind}|{boxes}"

    def fixable_gaps(self, limit: float) -> list[Gap]:
        return [g for g in self.gaps if g.dist <= limit + 1e-9]

    def to_json(self) -> dict:
        return {"file": self.file, "kind": self.kind, "severity": self.severity, "message": self.message,
                "count": self.count, "where": self.where, "gaps": [g.to_json() for g in self.gaps],
                "loose_ends": self.loose_ends}

    @staticmethod
    def from_json(d: dict) -> ImportIssue:
        return ImportIssue(d["file"], d["kind"], d["severity"], d["message"], int(d.get("count", 1)),
                           [list(map(float, b)) for b in d.get("where", [])],
                           [Gap.from_json(g) for g in d.get("gaps", [])],
                           [list(map(float, p)) for p in d.get("loose_ends", [])])


def bbox_of(pts: np.ndarray, pad: float = 0.0) -> list[float]:
    pts = np.asarray(pts, float).reshape(-1, 2)
    x0, y0 = pts.min(axis=0)
    x1, y1 = pts.max(axis=0)
    return [float(x0 - pad), float(y0 - pad), float(x1 + pad), float(y1 + pad)]


def find_gaps(chains: list[tuple[np.ndarray, Prim, Prim]], search: float = GAP_SEARCH_MM) -> tuple[list[Gap], list[list[float]]]:
    """Pareia as pontas de contornos abertos mais próximas entre si (cada ponta uma vez só).

    ``chains``: (pontos, primitiva da 1ª ponta, primitiva da última ponta) de cada contorno aberto.
    Devolve as aberturas (até ``search`` mm) e as pontas que ficaram sem par.
    """
    ends: list[tuple[np.ndarray, int, Prim]] = []
    for ci, (pts, p0, p1) in enumerate(chains):
        if len(pts) < 2:
            continue
        ends.append((np.asarray(pts[0], float), ci, p0))
        ends.append((np.asarray(pts[-1], float), ci, p1))
    cell = max(search, 1e-6)
    grid: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i, (pt, _, _) in enumerate(ends):
        grid[(math.floor(pt[0] / cell), math.floor(pt[1] / cell))].append(i)
    cand = []
    for i, (pt, ci, _) in enumerate(ends):
        gx, gy = math.floor(pt[0] / cell), math.floor(pt[1] / cell)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in grid.get((gx + dx, gy + dy), ()):
                    if j <= i:
                        continue
                    pj, cj, _ = ends[j]
                    # as duas pontas de um mesmo trecho reto não formam peça
                    if cj == ci and len(chains[ci][0]) < 3:
                        continue
                    d = float(np.hypot(*(pt - pj)))
                    if d <= search:
                        cand.append((d, i, j))
    cand.sort()
    used: set[int] = set()
    gaps = []
    for d, i, j in cand:
        if i in used or j in used:
            continue
        used.update((i, j))
        pa, _, prim = ends[i]
        pb = ends[j][0]
        gaps.append(Gap((float(pa[0]), float(pa[1])), (float(pb[0]), float(pb[1])), d,
                        prim.layer, prim.color, prim.rgb))
    loose = [[float(ends[i][0][0]), float(ends[i][0][1])] for i in range(len(ends)) if i not in used]
    return gaps, loose


def repair_prims(repairs: list[dict], factor: float) -> list[Prim]:
    """Trechos retos que fecham aberturas. ``repairs`` guarda as pontas na unidade do desenho (sem a
    conversão para mm), para continuar certo se a unidade do arquivo for trocada depois."""
    out = []
    for r in repairs or []:
        try:
            a = [float(r["a"][0]) * factor, float(r["a"][1]) * factor]
            b = [float(r["b"][0]) * factor, float(r["b"][1]) * factor]
        except (KeyError, TypeError, ValueError, IndexError):
            continue
        if not all(map(math.isfinite, a + b)) or math.hypot(a[0] - b[0], a[1] - b[1]) < 1e-9:
            continue
        rgb = tuple(r["rgb"]) if r.get("rgb") else None
        out.append(Prim("LINE", {"s": a, "e": b, "repair": True}, str(r.get("layer", "0")),
                        int(r.get("color", 7)), rgb))
    return out


def gap_to_repair(g: Gap, factor: float) -> dict:
    f = factor or 1.0
    return {"a": [g.a[0] / f, g.a[1] / f], "b": [g.b[0] / f, g.b[1] / f], "layer": g.layer,
            "color": g.color, "rgb": list(g.rgb) if g.rgb else None}


def summarize(issues: list[ImportIssue]) -> dict:
    """Contagens para o cabeçalho do painel: arquivos afetados e avisos por gravidade."""
    out = {FIXED: 0, CHECK: 0, ERROR: 0}
    files = set()
    for it in issues:
        out[it.severity] = out.get(it.severity, 0) + 1
        if it.severity != FIXED:
            files.add(it.file)
    out["files"] = len(files)
    return out


def student_note(issues: list[ImportIssue], fix_limit: Optional[float] = None) -> str:
    """Recado em linguagem simples para o aluno corrigir o próprio arquivo."""
    by_file: dict[str, list[ImportIssue]] = defaultdict(list)
    for it in issues:
        if it.severity != FIXED:
            by_file[os.path.basename(it.file)].append(it)
    if not by_file:
        return ""
    lines = ["Olá! Conferimos seus arquivos para o corte a laser e encontramos alguns pontos:", ""]
    for name in sorted(by_file):
        lines.append(f"• {name}")
        for it in by_file[name]:
            lines.append(f"   – {STUDENT_TEXT.get(it.kind, it.message)}")
            if it.kind == "open" and it.gaps:
                biggest = max(g.dist for g in it.gaps)
                lines.append(f"     (há {len(it.gaps)} abertura(s) de até {fmt_mm(biggest)} entre as pontas)")
    lines += ["", "Para o laser, cada peça precisa ser uma linha fechada, sem buracos e sem linhas "
              "repetidas por cima. Pode corrigir e reenviar? Obrigado!"]
    return "\n".join(lines)


STUDENT_TEXT = {
    "open": "Há contornos que não fecham (a linha tem uma abertura ou falta um trecho).",
    "invalid_contour": "Há um contorno que cruza a si mesmo; redesenhe a peça como uma linha fechada simples.",
    "loose_mark": "Há textos ou marcas fora de qualquer peça; diga se devem ser gravados ou apague-os.",
    "units_unknown": "O arquivo usa uma unidade desconhecida; exporte o DXF em milímetros.",
    "units_suspicious": "As medidas parecem estar na unidade errada; exporte o DXF em milímetros.",
    "empty": "O arquivo não tem nenhuma linha que o laser consiga cortar.",
    "read_error": "O arquivo não abriu; exporte de novo como DXF (versão R2000 ou mais nova).",
}


def fmt_mm(v: float) -> str:
    """0,3 mm / 1,25 mm / 12 mm (vírgula decimal, sem zeros sobrando)."""
    s = f"{v:.2f}".rstrip("0").rstrip(".") if v < 10 else f"{v:.0f}"
    return s.replace(".", ",") + " mm"
