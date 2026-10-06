"""Remove trechos de corte repetidos antes de exportar (sem Qt).

Com espaço 0 entre peças (ou desenhos com linhas duplicadas), o mesmo trecho aparece duas vezes:
a borda de uma peça encostada na outra. O laser passaria duas vezes no mesmo lugar — demora mais e
queima a borda. Aqui cada trecho reto só sai uma vez (a parte já coberta por outro trecho colinear é
retirada) e arcos/círculos idênticos saem uma vez só. A geometria que sobra é a mesma.
"""
from __future__ import annotations

import math
from typing import Iterable

from .models import Prim

TOL = 0.05            # mm: distância/folga para considerar dois trechos sobrepostos
ANG_STEP = 0.002      # rad: agrupamento por direção


def _key_line(x0, y0, x1, y1):
    """Direção normalizada, deslocamento da reta e parâmetro ao longo dela."""
    dx, dy = x1 - x0, y1 - y0
    L = math.hypot(dx, dy)
    if L < 1e-9:
        return None
    ux, uy = dx / L, dy / L
    # sentido canônico pela componente dominante: retas quase horizontais/verticais nunca "viram"
    if (abs(ux) >= abs(uy) and ux < 0) or (abs(ux) < abs(uy) and uy < 0):
        ux, uy = -ux, -uy
    theta = math.atan2(uy, ux)                        # (-π/4, 3π/4]
    off = -uy * x0 + ux * y0                          # distância (com sinal) da reta à origem
    t0, t1 = ux * x0 + uy * y0, ux * x1 + uy * y1
    return theta, off, (ux, uy), min(t0, t1), max(t0, t1)


class _Lines:
    def __init__(self, tol: float):
        self.tol = tol
        self.buckets: dict[tuple, list] = {}           # (ângulo, deslocamento) -> [(t0, t1)]

    def _keys(self, theta, off):
        a, o = round(theta / ANG_STEP), round(off / self.tol)
        for da in (-1, 0, 1):
            for do in (-1, 0, 1):
                yield (a + da, o + do)

    def subtract(self, theta, off, t0, t1) -> list[tuple[float, float]]:
        """Partes de [t0, t1] ainda não cortadas; registra o trecho como cortado."""
        covered = []
        for k in self._keys(theta, off):
            covered.extend(self.buckets.get(k, ()))
        free = [(t0, t1)]
        for c0, c1 in sorted(covered):
            nxt = []
            for f0, f1 in free:
                if c1 <= f0 + self.tol or c0 >= f1 - self.tol:
                    nxt.append((f0, f1))
                    continue
                if c0 > f0 + self.tol:
                    nxt.append((f0, c0))
                if c1 < f1 - self.tol:
                    nxt.append((c1, f1))
            free = nxt
            if not free:
                break
        key = (round(theta / ANG_STEP), round(off / self.tol))
        self.buckets.setdefault(key, []).append((t0, t1))
        return [(a, b) for a, b in free if b - a > self.tol]


def _point(u, off, t):
    """Ponto da reta (direção u, deslocamento off) no parâmetro t."""
    ux, uy = u
    return (ux * t - uy * off, uy * t + ux * off)


def remove_overlaps(prims: Iterable[Prim], tol: float = TOL) -> tuple[list[Prim], int]:
    """Devolve (primitivas sem trechos repetidos, quantos trechos/peças foram retirados)."""
    lines = _Lines(tol)
    seen_curves: set = set()
    out: list[Prim] = []
    removed = 0

    def q(v):
        return round(v / tol)

    for p in prims:
        k, d = p.kind, p.data
        if k == "LINE":
            segs = [(d["s"][0], d["s"][1], d["e"][0], d["e"][1])]
        elif k == "POLY" and not any(len(v) > 2 and abs(v[2]) > 1e-9 for v in d["pts"]):
            pts = [(v[0], v[1]) for v in d["pts"]]
            if d.get("closed") and len(pts) >= 2 and math.dist(pts[0], pts[-1]) > 1e-9:
                pts = pts + [pts[0]]                  # fechada de 2 pontos = "ida e volta" no mesmo trecho
            segs = [(a[0], a[1], b[0], b[1]) for a, b in zip(pts, pts[1:])]
        else:
            if k == "CIRCLE":
                key = ("C", q(d["c"][0]), q(d["c"][1]), q(d["r"]))
            elif k == "ARC":
                key = ("A", q(d["c"][0]), q(d["c"][1]), q(d["r"]), round(d["a0"] % 360, 2), round(d["a1"] % 360, 2))
            else:
                key = None
            if key is not None and key in seen_curves:
                removed += 1
                continue
            if key is not None:
                seen_curves.add(key)
            out.append(p)
            continue
        kept_all, pieces = True, []
        for x0, y0, x1, y1 in segs:
            kk = _key_line(x0, y0, x1, y1)
            if kk is None:
                continue
            theta, off, u, t0, t1 = kk
            free = lines.subtract(theta, off, t0, t1)
            if len(free) != 1 or abs(free[0][0] - t0) > 1e-9 or abs(free[0][1] - t1) > 1e-9:
                kept_all = False
            for a, b in free:
                pieces.append((_point(u, off, a), _point(u, off, b)))
        if kept_all:
            out.append(p)                             # nada repetido: mantém a entidade original
            continue
        removed += 1
        out.extend(_chain(pieces, p, tol))
    return out, removed


def _chain(pieces: list, src: Prim, tol: float) -> list[Prim]:
    """Junta trechos que continuam um no outro numa polilinha (o laser não para entre eles)."""
    chains: list[list] = []
    for a, b in pieces:
        if chains and math.dist(chains[-1][-1], a) <= tol:
            chains[-1].append(b)
        else:
            chains.append([a, b])
    if len(chains) > 1 and math.dist(chains[-1][-1], chains[0][0]) <= tol:
        chains[0] = chains.pop()[:-1] + chains[0]       # o contorno fechado foi cortado no meio
    out = []
    for c in chains:
        if len(c) == 2:
            out.append(Prim("LINE", {"s": c[0], "e": c[1]}, src.layer, src.color, src.rgb))
        else:
            out.append(Prim("POLY", {"pts": [(x, y, 0.0) for x, y in c], "closed": False},
                            src.layer, src.color, src.rgb))
    return out
