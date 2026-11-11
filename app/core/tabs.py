"""Micro-pontes (tabs): interrompe o corte do contorno EXTERNO em alguns pontos (sem Qt).

Peça pequena cai na colmeia ou levanta com o sopro. Com micro-pontes o laser deixa N trechinhos sem
cortar (padrão 0,4 mm) e a peça fica presa na chapa; depois é só destacar com a mão.

A geometria original continua intacta: a ponte só existe na EXPORTAÇÃO. A primitiva original é dividida
no trecho da ponte — LINE vira duas LINEs, ARC vira dois ARCs, CIRCLE vira arco(s) e a polilinha é
cortada mantendo os bulges (arcos continuam arcos). Só some o trechinho da ponte.

Este módulo também guarda a representação comum de um contorno como sequência de trechos retos e arcos
(``Seg``), usada pela compensação de kerf (``kerf.py``).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from .models import Part, Prim

DEFAULT_TABS = 2
DEFAULT_WIDTH = 0.4          # mm (a validar no laboratório para cada material)
TAB_CHOICES = (0, 2, 3, 4)
MAX_TABS = 8
CORNER_CLEAR = 1.0           # mm: distância preferida entre a ponte e a ponta do trecho (canto)
CORNER_FULL = 5.0            # a partir daqui o canto não pesa mais na escolha
SPLITTABLE = ("LINE", "ARC", "CIRCLE", "POLY")


# --------------------------------------------------------------------------- trechos (reta ou arco)
@dataclass
class Seg:
    """Trecho de contorno: reta p0→p1 ou arco de centro ``c``, raio ``r``, do ângulo ``a0`` (rad),
    varrendo ``sweep`` rad (positivo = anti-horário)."""
    p0: tuple
    p1: tuple
    c: Optional[tuple] = None
    r: float = 0.0
    a0: float = 0.0
    sweep: float = 0.0
    src: int = -1                # índice da primitiva de origem (para copiar camada/cor)

    @property
    def is_arc(self) -> bool:
        return self.c is not None

    @property
    def length(self) -> float:
        return self.r * abs(self.sweep) if self.is_arc else math.dist(self.p0, self.p1)

    def point(self, s: float) -> tuple:
        """Ponto a ``s`` mm do início, medido ao longo do trecho."""
        if self.is_arc:
            a = self.a0 + math.copysign(s / self.r, self.sweep)
            return (self.c[0] + self.r * math.cos(a), self.c[1] + self.r * math.sin(a))
        L = self.length or 1.0
        t = s / L
        return (self.p0[0] + (self.p1[0] - self.p0[0]) * t, self.p0[1] + (self.p1[1] - self.p0[1]) * t)

    def sub(self, s0: float, s1: float) -> "Seg":
        """O pedaço entre ``s0`` e ``s1`` (mm ao longo do trecho)."""
        if not self.is_arc:
            return Seg(self.point(s0), self.point(s1), src=self.src)
        sg = math.copysign(1.0, self.sweep)
        return Seg(self.point(s0), self.point(s1), self.c, self.r, self.a0 + sg * s0 / self.r,
                   sg * (s1 - s0) / self.r, self.src)

    def reversed(self) -> "Seg":
        if not self.is_arc:
            return Seg(self.p1, self.p0, src=self.src)
        return Seg(self.p1, self.p0, self.c, self.r, self.a0 + self.sweep, -self.sweep, self.src)

    def tangent(self, at_end: bool) -> tuple:
        """Direção de percurso (unitária) no início ou no fim."""
        if not self.is_arc:
            L = self.length or 1.0
            return ((self.p1[0] - self.p0[0]) / L, (self.p1[1] - self.p0[1]) / L)
        a = self.a0 + (self.sweep if at_end else 0.0)
        sg = math.copysign(1.0, self.sweep)
        return (-math.sin(a) * sg, math.cos(a) * sg)

    def flatten(self, tol: float = 0.02) -> list[tuple]:
        """Pontos do trecho (sem o último), para conferências com polígonos."""
        if not self.is_arc:
            return [self.p0]
        step = 2.0 * math.acos(max(-1.0, min(1.0, 1.0 - tol / max(self.r, tol * 1.01))))
        n = max(2, int(math.ceil(abs(self.sweep) / max(step, 1e-3))))
        return [(self.c[0] + self.r * math.cos(self.a0 + self.sweep * i / n),
                 self.c[1] + self.r * math.sin(self.a0 + self.sweep * i / n)) for i in range(n)]


def bulge_seg(p0, p1, bulge: float, src: int = -1) -> Seg:
    """Segmento de polilinha (com bulge) como ``Seg``."""
    p0, p1 = (float(p0[0]), float(p0[1])), (float(p1[0]), float(p1[1]))
    d = math.dist(p0, p1)
    if abs(bulge) < 1e-12 or d < 1e-12:
        return Seg(p0, p1, src=src)
    theta = 4.0 * math.atan(bulge)                       # varredura com sinal
    r = d / (2.0 * abs(math.sin(theta / 2.0)))
    h = (d / 2.0) / math.tan(theta / 2.0)                # distância (com sinal) do meio da corda ao centro
    mx, my = (p0[0] + p1[0]) / 2.0, (p0[1] + p1[1]) / 2.0
    nx, ny = -(p1[1] - p0[1]) / d, (p1[0] - p0[0]) / d   # normal à esquerda
    c = (mx + nx * h, my + ny * h)
    return Seg(p0, p1, c, r, math.atan2(p0[1] - c[1], p0[0] - c[0]), theta, src)


def seg_bulge(s: Seg) -> float:
    return math.tan(s.sweep / 4.0) if s.is_arc else 0.0


def prim_segs(p: Prim, src: int = -1) -> Optional[list[Seg]]:
    """Trechos de uma primitiva LINE/ARC/CIRCLE/POLY (None para os outros tipos)."""
    d, k = p.data, p.kind
    if k == "LINE":
        return [Seg((float(d["s"][0]), float(d["s"][1])), (float(d["e"][0]), float(d["e"][1])), src=src)]
    if k in ("ARC", "CIRCLE"):
        c, r = (float(d["c"][0]), float(d["c"][1])), float(d["r"])
        if k == "CIRCLE":
            a0, sweep = 0.0, 2.0 * math.pi
        else:
            a0 = math.radians(d["a0"])
            sweep = math.radians((d["a1"] - d["a0"]) % 360.0)
            if sweep < 1e-9:
                sweep = 2.0 * math.pi
        s = Seg((0, 0), (0, 0), c, r, a0, sweep, src)
        s.p0, s.p1 = s.point(0.0), s.point(s.length)
        return [s]
    if k == "POLY":
        pts = d["pts"]
        n = len(pts)
        cnt = n if d.get("closed") else n - 1
        out = []
        for i in range(max(0, cnt)):
            a, b = pts[i], pts[(i + 1) % n]
            if math.dist(a[:2], b[:2]) < 1e-9:
                continue
            out.append(bulge_seg(a, b, a[2] if len(a) > 2 else 0.0, src))
        return out
    return None


def _like(src: Prim, kind: str, data: dict) -> Prim:
    return Prim(kind, data, src.layer, src.color, src.rgb)


def seg_prim(s: Seg, src: Prim) -> Prim:
    """Um trecho como primitiva solta (LINE ou ARC), com a aparência de ``src``."""
    if not s.is_arc:
        return _like(src, "LINE", {"s": [s.p0[0], s.p0[1]], "e": [s.p1[0], s.p1[1]]})
    a0, a1 = math.degrees(s.a0), math.degrees(s.a0 + s.sweep)
    if s.sweep < 0:                                      # ARC do DXF é sempre anti-horário
        a0, a1 = a1, a0
    if abs(abs(s.sweep) - 2.0 * math.pi) < 1e-9:
        return _like(src, "CIRCLE", {"c": [s.c[0], s.c[1]], "r": s.r})
    return _like(src, "ARC", {"c": [s.c[0], s.c[1]], "r": s.r, "a0": a0 % 360.0, "a1": a1 % 360.0})


def chain_poly(segs: list[Seg], closed: bool, src: Prim) -> Prim:
    """Trechos encadeados como uma polilinha (bulges preservados)."""
    pts = [[s.p0[0], s.p0[1], seg_bulge(s)] for s in segs]
    if not closed:
        pts.append([segs[-1].p1[0], segs[-1].p1[1], 0.0])
    return _like(src, "POLY", {"pts": pts, "closed": bool(closed)})


# --------------------------------------------------------------------------- onde ficam as pontes
@dataclass
class Tab:
    prim: int                # índice (na lista recebida) da primitiva que hospeda a ponte
    seg: int                 # trecho dentro da primitiva
    s0: float                # início e fim da ponte, em mm ao longo do trecho
    s1: float
    p0: tuple                # pontas da ponte (coordenadas da peça)
    p1: tuple
    straight: bool

    @property
    def mid(self) -> tuple:
        return ((self.p0[0] + self.p1[0]) / 2.0, (self.p0[1] + self.p1[1]) / 2.0)


def _span(seg: Seg, width: float) -> float:
    """Comprimento ao longo do trecho que dá uma abertura (corda) de ``width`` mm."""
    if not seg.is_arc:
        return width
    return 2.0 * seg.r * math.asin(min(1.0, width / (2.0 * seg.r)))


def _candidates(prims: list[Prim], idx: list[int], width: float, center: tuple) -> list[dict]:
    out = []
    for i in idx:
        segs = prim_segs(prims[i], i) if prims[i].kind in SPLITTABLE else None
        for k, sg in enumerate(segs or []):
            L = sg.length
            if sg.is_arc and 2.0 * sg.r <= width * 1.5:
                continue
            w = _span(sg, width)
            if L < w + 2.0 * min(CORNER_CLEAR, width):   # precisa sobrar corte dos dois lados
                continue
            fracs = [0.5]
            if L > 6.0 * (w + 2.0 * CORNER_FULL):        # trecho comprido: mais posições possíveis
                fracs = [0.5, 0.25, 0.75]
            if sg.is_arc and abs(sg.sweep) > math.pi * 1.01:   # círculo/arco grande: em volta toda
                n = max(4, int(round(abs(sg.sweep) / (math.pi / 4))))
                fracs = [(j + 0.5) / n for j in range(n)]
            for f in fracs:
                mid = sg.point(L * f)
                corner = min(L * f, L * (1.0 - f)) - w / 2.0
                closed_curve = sg.is_arc and abs(abs(sg.sweep) - 2.0 * math.pi) < 1e-9
                q = (1.0 if not sg.is_arc else 0.6) * (1.0 if closed_curve else
                                                       min(1.0, max(corner, 0.0) / CORNER_FULL) * 0.7 + 0.3)
                out.append({"prim": i, "seg": k, "s": L * f, "w": w, "mid": mid, "q": q, "sg": sg,
                            "ang": math.atan2(mid[1] - center[1], mid[0] - center[0])})
    return out


def _angdiff(a: float, b: float) -> float:
    return abs((a - b + math.pi) % (2.0 * math.pi) - math.pi)


def plan_tabs(prims: list[Prim], outer_idx: list[int], count: int, width: float = DEFAULT_WIDTH,
              center: Optional[tuple] = None) -> list[Tab]:
    """Escolhe onde ficam ``count`` pontes no contorno externo: espalhadas em volta da peça (2 = lados
    opostos), de preferência em trechos retos e longe dos cantos. Devolve menos pontes (ou nenhuma) se
    o contorno não tiver trecho que comporte."""
    count = max(0, min(int(count), MAX_TABS))
    if count <= 0 or width <= 0 or not outer_idx:
        return []
    if center is None:
        xs, ys = [], []
        for i in outer_idx:
            for sg in (prim_segs(prims[i]) or []) if prims[i].kind in SPLITTABLE else []:
                for x, y in sg.flatten(0.2) + [sg.p1]:
                    xs.append(x)
                    ys.append(y)
        if not xs:
            return []
        center = ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)
    cands = _candidates(prims, list(outer_idx), width, center)
    if not cands:
        return []
    step = 2.0 * math.pi / count

    def clash(c, chosen):
        return any(math.dist(c["mid"], o["mid"]) < 3.0 * width + 1e-9 for o in chosen)

    best, best_cost = None, None
    firsts = sorted(range(len(cands)), key=lambda j: (-cands[j]["q"], j))[:48]
    for j in firsts:
        chosen = [cands[j]]
        cost = 1.0 - cands[j]["q"]
        for k in range(1, count):
            target = cands[j]["ang"] + k * step
            pick, pc = None, None
            for c in cands:
                if clash(c, chosen):
                    continue
                cc = _angdiff(c["ang"], target) / (step / 2.0) + (1.0 - c["q"])
                if pc is None or cc < pc - 1e-12:
                    pick, pc = c, cc
            if pick is None:
                cost += 10.0                                   # faltou lugar para esta ponte
                continue
            chosen.append(pick)
            cost += pc
        if best_cost is None or cost < best_cost - 1e-9:
            best, best_cost = chosen, cost
    tabs = []
    for c in best or []:
        sg, s0, s1 = c["sg"], c["s"] - c["w"] / 2.0, c["s"] + c["w"] / 2.0
        tabs.append(Tab(c["prim"], c["seg"], s0, s1, sg.point(s0), sg.point(s1), not sg.is_arc))
    return tabs


# --------------------------------------------------------------------------- dividir as primitivas
def _split_prim(p: Prim, cuts: dict[int, list[tuple[float, float]]]) -> list[Prim]:
    """A primitiva sem os trechos ``cuts`` ({trecho: [(s0, s1)]})."""
    segs = prim_segs(p)
    closed = p.kind == "CIRCLE" or (p.kind == "POLY" and bool(p.data.get("closed")))
    chains: list[list[Seg]] = [[]]
    for k, sg in enumerate(segs):
        pos = 0.0
        for s0, s1 in sorted(cuts.get(k, [])):
            if s0 - pos > 1e-9:
                chains[-1].append(sg.sub(pos, s0))
            chains.append([])
            pos = s1
        if sg.length - pos > 1e-9:
            chains[-1].append(sg.sub(pos, sg.length))
    if closed and len(chains) > 1:                       # o fim do contorno continua no começo dele
        chains[0] = chains.pop() + chains[0]
    out = []
    for ch in chains:
        if not ch:
            continue
        if len(ch) == 1:
            out.append(seg_prim(ch[0], p))
        elif p.kind == "POLY":
            out.append(chain_poly(ch, False, p))
        else:                                            # círculo cortado em volta do ângulo 0: junta
            a, b = ch[0], ch[-1]
            merged = Seg(a.p0, b.p1, a.c, a.r, a.a0, sum(s.sweep for s in ch), a.src)
            out.append(seg_prim(merged, p))
    return out


def apply_tabs(prims: list[Prim], outer_idx: list[int], count: int, width: float = DEFAULT_WIDTH,
               center: Optional[tuple] = None) -> tuple[list[list[Prim]], list[Tab]]:
    """Devolve (para cada primitiva recebida, a lista de primitivas que a substitui; pontes feitas).
    Primitivas sem ponte voltam como estão (o mesmo objeto)."""
    tabs = plan_tabs(prims, outer_idx, count, width, center)
    by_prim: dict[int, dict[int, list]] = {}
    for t in tabs:
        by_prim.setdefault(t.prim, {}).setdefault(t.seg, []).append((t.s0, t.s1))
    out = [[p] if i not in by_prim else _split_prim(p, by_prim[i]) for i, p in enumerate(prims)]
    return out, tabs


def part_tabs(part: Part, width: float = DEFAULT_WIDTH) -> list[Tab]:
    """Pontes da peça em coordenadas LOCAIS (para a interface desenhar onde elas ficam). Sem
    compensação de kerf (a posição muda no máximo kerf/2)."""
    n = int(getattr(part, "tabs", 0) or 0)
    if n <= 0 or part.is_open:
        return []
    return plan_tabs(part.prims, list(part.outer_prim_idx), n, width)
