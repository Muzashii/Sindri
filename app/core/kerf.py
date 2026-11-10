"""Compensação de kerf opcional, por peça (sem Qt).

O laser tira uma faixa de material (kerf). Em peça de encaixe isso deixa o dente fino e o rasgo largo.
Com a compensação ligada NA PEÇA, a exportação desloca o corte em kerf/2: o contorno externo para fora
e os furos/rasgos para dentro — a peça cortada sai com a medida do desenho.

* Só na exportação: o encaixe continua usando o contorno original (o espaçamento precisa cobrir o kerf).
* Deslocamento analítico: reta continua reta, arco continua arco (raio ± kerf/2), círculo continua
  círculo e polilinha continua polilinha (com bulge). Canto vivo entre retas é prolongado (miter);
  canto que envolve arco ganha um arco de raio kerf/2.
* Gravação e vinco não se deslocam.
* SPLINE/ELLIPSE no corte, contorno aberto ou detalhe menor que o kerf: a peça é exportada SEM
  compensação, com aviso (nunca "meio compensada").
"""
from __future__ import annotations

import math
from typing import Optional

from shapely.geometry import Polygon

from .models import Prim
from .tabs import Seg, chain_poly, prim_segs, seg_prim

JOIN_TOL = 0.05              # mm: pontas a até esta distância são o mesmo ponto
MITER_LIMIT = 4.0            # canto mais agudo que isso (ponta > 4 × deslocamento) vira arco
CUT_KINDS = ("LINE", "ARC", "CIRCLE", "POLY")


class KerfError(Exception):
    """Não dá para compensar esta peça (a mensagem diz por quê)."""


# --------------------------------------------------------------------------- montar os contornos
def _closed_prim(p: Prim) -> bool:
    if p.kind == "CIRCLE":
        return True
    if p.kind == "POLY":
        pts = p.data["pts"]
        return bool(p.data.get("closed")) or (len(pts) > 2 and math.dist(pts[0][:2], pts[-1][:2]) <= JOIN_TOL)
    return False


def _chain(strands: list[list[Seg]]) -> tuple[list[list[Seg]], list[list[Seg]]]:
    """Encadeia pedaços soltos pelas pontas. Devolve (contornos fechados, sobras abertas)."""
    rest = list(strands)
    closed, opened = [], []
    while rest:
        cur = list(rest.pop(0))
        while True:
            end = cur[-1].p1
            if math.dist(end, cur[0].p0) <= JOIN_TOL and _is_loop(cur):
                closed.append(cur)
                break
            best, bd, rev = None, JOIN_TOL, False
            for k, st in enumerate(rest):
                for r, pt in ((False, st[0].p0), (True, st[-1].p1)):
                    dd = math.dist(end, pt)
                    if dd <= bd:
                        best, bd, rev = k, dd, r
            if best is None:
                opened.append(cur)
                break
            st = rest.pop(best)
            cur.extend([s.reversed() for s in reversed(st)] if rev else st)
    return closed, opened


def _is_loop(segs: list[Seg]) -> bool:
    return len(segs) > 1 or (segs[0].is_arc and abs(segs[0].sweep) > math.pi)


def _ring(segs: list[Seg]) -> list[tuple]:
    pts = []
    for s in segs:
        pts.extend(s.flatten(0.01))
    return pts


def _signed_area(pts: list[tuple]) -> float:
    a = 0.0
    for i in range(len(pts)):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % len(pts)]
        a += x0 * y1 - x1 * y0
    return a / 2.0


# --------------------------------------------------------------------------- deslocar um contorno
def _line_x_line(p, t, q, u):
    den = t[0] * u[1] - t[1] * u[0]
    if abs(den) < 1e-12:
        return []
    k = ((q[0] - p[0]) * u[1] - (q[1] - p[1]) * u[0]) / den
    return [(p[0] + t[0] * k, p[1] + t[1] * k)]


def _line_x_circle(p, t, c, r):
    fx, fy = p[0] - c[0], p[1] - c[1]
    b = fx * t[0] + fy * t[1]
    disc = b * b - (fx * fx + fy * fy - r * r)
    if disc < -1e-9:
        return []
    sq = math.sqrt(max(0.0, disc))
    return [(p[0] + t[0] * k, p[1] + t[1] * k) for k in (-b - sq, -b + sq)]


def _circle_x_circle(c0, r0, c1, r1):
    d = math.dist(c0, c1)
    if d < 1e-12 or d > r0 + r1 + 1e-9 or d < abs(r0 - r1) - 1e-9:
        return []
    a = (r0 * r0 - r1 * r1 + d * d) / (2.0 * d)
    h = math.sqrt(max(0.0, r0 * r0 - a * a))
    ux, uy = (c1[0] - c0[0]) / d, (c1[1] - c0[1]) / d
    mx, my = c0[0] + ux * a, c0[1] + uy * a
    return [(mx - uy * h, my + ux * h), (mx + uy * h, my - ux * h)]


def _intersections(a: Seg, b: Seg) -> list[tuple]:
    if not a.is_arc and not b.is_arc:
        return _line_x_line(a.p0, a.tangent(False), b.p0, b.tangent(False))
    if a.is_arc and b.is_arc:
        return _circle_x_circle(a.c, a.r, b.c, b.r)
    line, arc = (b, a) if a.is_arc else (a, b)
    return _line_x_circle(line.p0, line.tangent(False), arc.c, arc.r)


def _arc_param(s: Seg, p) -> float:
    """Ângulo percorrido (rad, ≥ 0) do início do arco até ``p``; perto de 2π = um pouco antes do início."""
    sg = math.copysign(1.0, s.sweep)
    return ((math.atan2(p[1] - s.c[1], p[0] - s.c[0]) - s.a0) * sg) % (2.0 * math.pi)


def _on(s: Seg, p, tol: float = 1e-6) -> bool:
    if not s.is_arc:
        t = s.tangent(False)
        k = (p[0] - s.p0[0]) * t[0] + (p[1] - s.p0[1]) * t[1]
        return -tol <= k <= s.length + tol
    d = _arc_param(s, p)
    ang_tol = tol / max(s.r, 1e-9)
    return d <= abs(s.sweep) + ang_tol or d >= 2.0 * math.pi - ang_tol


def _set_end(s: Seg, p) -> None:
    if s.is_arc:
        d = _arc_param(s, p)
        if d > abs(s.sweep) + 1e-3 and d > 2.0 * math.pi - 1e-3:
            d = 0.0
        s.sweep = math.copysign(d, s.sweep)
    s.p1 = (p[0], p[1])


def _set_start(s: Seg, p) -> None:
    if s.is_arc:
        d = _arc_param(s, p)
        if d > 2.0 * math.pi - 1e-3:
            d = 0.0
        sg = math.copysign(1.0, s.sweep)
        s.a0 += sg * d
        s.sweep = sg * (abs(s.sweep) - d)
    s.p0 = (p[0], p[1])


def _offset_seg(s: Seg, d: float) -> Optional[Seg]:
    """Trecho deslocado ``d`` para a DIREITA do percurso (None se o arco some)."""
    if not s.is_arc:
        t = s.tangent(False)
        nx, ny = t[1] * d, -t[0] * d
        return Seg((s.p0[0] + nx, s.p0[1] + ny), (s.p1[0] + nx, s.p1[1] + ny), src=s.src)
    r = s.r + d if s.sweep > 0 else s.r - d
    if r <= 1e-6:
        return None
    o = Seg((0, 0), (0, 0), s.c, r, s.a0, s.sweep, s.src)
    o.p0 = (s.c[0] + r * math.cos(s.a0), s.c[1] + r * math.sin(s.a0))
    o.p1 = (s.c[0] + r * math.cos(s.a0 + s.sweep), s.c[1] + r * math.sin(s.a0 + s.sweep))
    return o


def offset_contour(segs: list[Seg], grow: float) -> list[Seg]:
    """Contorno fechado deslocado: ``grow`` > 0 aumenta a região fechada, < 0 diminui. Levanta KerfError
    se algum detalhe é pequeno demais para o deslocamento."""
    if abs(grow) < 1e-12:
        return list(segs)
    if len(segs) == 1 and segs[0].is_arc and abs(abs(segs[0].sweep) - 2.0 * math.pi) < 1e-9:
        r = segs[0].r + grow
        if r <= 1e-6:
            raise KerfError("furo menor que o kerf")
        s = segs[0]
        o = Seg((0, 0), (0, 0), s.c, r, s.a0, s.sweep, s.src)
        o.p0 = o.p1 = (s.c[0] + r * math.cos(s.a0), s.c[1] + r * math.sin(s.a0))
        return [o]
    ring = _ring(segs)
    if len(ring) < 3:
        raise KerfError("contorno degenerado")
    area0 = _signed_area(ring)
    if area0 < 0:                                         # percorre no sentido anti-horário
        segs = [s.reversed() for s in reversed(segs)]
    d = grow                                              # anti-horário: direita do percurso = fora
    n = len(segs)
    off = [_offset_seg(s, d) for s in segs]
    kept = [i for i in range(n) if off[i] is not None]
    if len(kept) < 2:
        raise KerfError("detalhe menor que o kerf")
    out: list[Seg] = []
    joins: dict[int, Seg] = {}
    for pos, i in enumerate(kept):
        j = kept[(pos + 1) % len(kept)]
        a, b = off[i], off[j]
        dropped = (j - i) % n != 1
        v = segs[i].p1 if not dropped else segs[(i + 1) % n].c     # canto original (ou centro do arco que sumiu)
        ta, tb = segs[i].tangent(True), segs[j].tangent(False)
        cross, dot = ta[0] * tb[1] - ta[1] * tb[0], ta[0] * tb[0] + ta[1] * tb[1]
        if not dropped and (math.dist(a.p1, b.p0) < 1e-7 or (abs(cross) < 0.01 and dot > 0)):
            continue                                      # continuação suave: as pontas já coincidem
        gap = cross * d > 0 and not dropped               # o deslocamento abriu o canto
        if gap and (a.is_arc or b.is_arc):
            joins[i] = _join_arc(v, a.p1, b.p0, abs(d), cross, a.src)
            continue
        pts = _intersections(a, b)
        if gap:                                           # reta com reta: prolonga até a ponta (miter)
            if not pts or math.dist(pts[0], v) > MITER_LIMIT * abs(d) + 1e-9:
                joins[i] = _join_arc(v, a.p1, b.p0, abs(d), cross, a.src)
                continue
            p = pts[0]
        else:
            if a.is_arc or b.is_arc:
                pts = [q for q in pts if _on(a, q) and _on(b, q)]
            if not pts:
                raise KerfError("detalhe menor que o kerf")
            p = min(pts, key=lambda q: math.dist(q, v))
        _set_end(a, p)
        _set_start(b, p)
    for i in kept:
        s, o = segs[i], off[i]
        if o.is_arc:
            if abs(o.sweep) < 1e-9 or abs(o.sweep) > abs(s.sweep) + 1e-6:
                raise KerfError("detalhe menor que o kerf")
        else:
            t = s.tangent(False)
            if (o.p1[0] - o.p0[0]) * t[0] + (o.p1[1] - o.p0[1]) * t[1] <= 1e-9:
                raise KerfError("detalhe menor que o kerf")
        out.append(o)
        if i in joins:
            out.append(joins[i])
    # conferência global: o resultado é um contorno simples com a área esperada
    new_ring = _ring(out)
    try:
        poly, ref = Polygon(new_ring), Polygon(ring)
        if not ref.is_valid:
            ref = ref.buffer(0)
        want = ref.buffer(grow, join_style=2, mitre_limit=MITER_LIMIT)
        ok = (poly.is_valid and _signed_area(new_ring) > 0 and not want.is_empty
              and abs(poly.area - want.area) <= 0.02 * ref.length * abs(grow) + 0.02
              and poly.symmetric_difference(want).area <= 0.05 * ref.length * abs(grow) + 0.05)
    except Exception:
        ok = False
    if not ok:
        raise KerfError("detalhe menor que o kerf")
    return out


def _join_arc(v, p0, p1, r: float, cross: float, src: int) -> Seg:
    a0 = math.atan2(p0[1] - v[1], p0[0] - v[0])
    a1 = math.atan2(p1[1] - v[1], p1[0] - v[0])
    sg = 1.0 if cross > 0 else -1.0
    sweep = ((a1 - a0) * sg) % (2.0 * math.pi)
    return Seg((p0[0], p0[1]), (p1[0], p1[1]), (v[0], v[1]), r, a0, sg * sweep, src)


# --------------------------------------------------------------------------- a peça inteira
def compensate(prims: list[Prim], outer_idx: list[int], inner_cut_idx: list[int],
               kerf: float) -> tuple[list[list[Prim]], list[int]]:
    """Desloca o corte em kerf/2. Devolve (para cada primitiva recebida, as primitivas que a substituem;
    índices — na lista NOVA, achatada — das primitivas do contorno externo). Primitivas que não são
    corte voltam como estão. Levanta KerfError (peça inteira sem compensação) quando não dá."""
    if kerf <= 0:
        raise KerfError("sem kerf")
    half = kerf / 2.0
    outer_idx, inner_cut_idx = list(outer_idx), [i for i in inner_cut_idx if i not in set(outer_idx)]
    for i in outer_idx + inner_cut_idx:
        if prims[i].kind not in CUT_KINDS:
            raise KerfError("curva SPLINE/ELLIPSE no corte")
    repl: list[Optional[list[Prim]]] = [None] * len(prims)

    def contours(idx: list[int]):
        """[(trechos, índices das primitivas, tipo)] fechados + índices que ficaram abertos."""
        done, strands, owner = [], [], []
        for i in idx:
            segs = prim_segs(prims[i], i)
            if not segs:
                continue
            if _closed_prim(prims[i]):
                done.append((segs, [i], "circle" if prims[i].kind == "CIRCLE" else "poly"))
            else:
                strands.append(segs)
                owner.append(i)
        closed, opened = _chain(strands)
        for ch in closed:
            done.append((ch, sorted({s.src for s in ch}), "chain"))
        return done, sorted({s.src for ch in opened for s in ch})

    outer, outer_open = contours(outer_idx)
    if outer_open or len(outer) != 1:
        raise KerfError("contorno externo aberto")
    inner, _open_inner = contours(inner_cut_idx)          # riscos abertos ficam como estão
    polys = []
    for segs, _, _ in inner:
        try:
            pg = Polygon(_ring(segs))
            polys.append(pg if pg.is_valid else pg.buffer(0))
        except Exception:
            raise KerfError("contorno degenerado")
    jobs = [(outer[0], +half, True)]
    for k, c in enumerate(inner):
        pt = polys[k].representative_point()
        depth = sum(1 for m, other in enumerate(polys)
                    if m != k and other.area > polys[k].area and other.contains(pt))
        jobs.append((c, -half if depth % 2 == 0 else +half, False))   # furo encolhe; ilha dentro de furo cresce
    outer_first = None
    for (segs, idx, kind), grow, is_outer in jobs:
        new = offset_contour(segs, grow)
        src = prims[idx[0]]
        if kind == "circle":
            out = [Prim("CIRCLE", {"c": [new[0].c[0], new[0].c[1]], "r": new[0].r}, src.layer, src.color, src.rgb)]
        elif kind == "poly":
            out = [chain_poly(new, True, src)]
        else:
            out, run = [], []
            for s in new + [None]:                        # trechos seguidos da mesma polilinha continuam juntos
                if s is not None and prims[s.src].kind == "POLY" and (not run or run[-1].src == s.src):
                    run.append(s)
                    continue
                if run:
                    out.append(chain_poly(run, False, prims[run[0].src]) if len(run) > 1
                               else seg_prim(run[0], prims[run[0].src]))
                    run = []
                if s is not None:
                    if prims[s.src].kind == "POLY":
                        run = [s]
                    else:
                        out.append(seg_prim(s, prims[s.src]))
        repl[idx[0]] = out
        for i in idx[1:]:
            repl[i] = []
        if is_outer:
            outer_first = idx[0]
    result = [[p] if r is None else r for p, r in zip(prims, repl)]
    new_outer, pos = [], 0
    for i, r in enumerate(result):
        if i == outer_first:
            new_outer = list(range(pos, pos + len(r)))
        pos += len(r)
    return result, new_outer


def spacing_note(spacing: float, kerf: float, material: str = "") -> Optional[str]:
    """Peças compensadas crescem kerf/2 de cada lado: o espaçamento precisa ser pelo menos o kerf."""
    if kerf > 0 and spacing < kerf - 1e-9:
        mat = f" de {material}" if material else ""
        return (f"Espaçamento ({spacing:g} mm) menor que o kerf{mat} ({kerf:g} mm): com a compensação ligada, "
                "os cortes de peças vizinhas se encostam. Aumente o espaçamento.")
    return None


REASONS = {
    "sem kerf": "o material não tem kerf medido no banco de materiais",
    "curva SPLINE/ELLIPSE no corte": "o corte tem curva SPLINE/ELLIPSE (a compensação só aceita linhas, arcos, "
                                     "círculos e polilinhas; converta as curvas em arcos no CAD)",
    "contorno externo aberto": "o contorno externo não fecha",
}


def reason_text(err: Exception) -> str:
    return REASONS.get(str(err), "há um detalhe menor que o kerf (rasgo, furo ou canto)")


def can_compensate(part) -> Optional[str]:
    """None se a peça aceita compensação; senão o motivo (para avisar antes de exportar)."""
    from .operations import CUT_INNER, prim_operations
    ops = prim_operations(part)
    cut = set(part.outer_prim_idx) | {i for i, op in enumerate(ops) if op == CUT_INNER}
    if part.is_open:
        return REASONS["contorno externo aberto"]
    if any(part.prims[i].kind not in CUT_KINDS for i in cut):
        return REASONS["curva SPLINE/ELLIPSE no corte"]
    return None
