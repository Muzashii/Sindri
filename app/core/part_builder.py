"""Reconstrução das peças a partir das primitivas soltas.

Etapas:
1. Remove entidades duplicadas/sobrepostas (mesma geometria).
2. Encadeia linhas/arcos soltos em contornos (tolerância de junção).
3. Monta a árvore de contenção: contorno externo = peça; tudo dentro dela
   (furos, rasgos, gravações, textos) pertence à peça.
4. Furos sem nada dentro e com a mesma cor do contorno externo viram áreas
   aproveitáveis para part-in-part.
5. Agrupa peças idênticas (a menos de translação/rotação) com contador.
"""
from __future__ import annotations

import math
import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from shapely.geometry import LineString, MultiLineString, Polygon, MultiPoint, Point
from shapely.geometry import GeometryCollection
from shapely.strtree import STRtree
from shapely import affinity

from .dxf_import import RawFile, read_dxf
from .geometry import (Transform, flatten_prim, prim_endpoints, prim_is_closed, ring_to_polygon,
                       transform_prim, prim_rgb)
from .models import ImportReport, Part, Prim


# ---------------------------------------------------------------------------
# Duplicadas
# ---------------------------------------------------------------------------
def _r(v, q=0.000001):
    return round(float(v) / q) * q


def prim_signature(p: Prim) -> tuple:
    d, k = p.data, p.kind
    if k == "LINE":
        a = (_r(d["s"][0]), _r(d["s"][1]))
        b = (_r(d["e"][0]), _r(d["e"][1]))
        return (k,) + tuple(sorted([a, b]))
    if k == "CIRCLE":
        return (k, _r(d["c"][0]), _r(d["c"][1]), _r(d["r"]))
    if k == "ARC":
        return (k, _r(d["c"][0]), _r(d["c"][1]), _r(d["r"]), _r(d["a0"] % 360, 0.05),
                _r(d["a1"] % 360, 0.05))
    if k in ("TEXT", "MTEXT"):
        return (k, d.get("text"), _r(d["p"][0]), _r(d["p"][1]), _r(d["h"]))
    pts = flatten_prim(p, 0.05)
    fw = tuple((_r(x), _r(y)) for x, y in pts)
    bw = tuple(reversed(fw))
    return (k, min(fw, bw))


def remove_duplicates(prims: list[Prim]) -> tuple[list[Prim], int]:
    seen = set()
    out = []
    removed = 0
    for p in prims:
        try:
            sig = (p.layer, prim_rgb(p), prim_signature(p))
        except Exception:
            out.append(p)
            continue
        if sig in seen:
            removed += 1
            continue
        seen.add(sig)
        out.append(p)
    return out, removed


# ---------------------------------------------------------------------------
# Contornos
# ---------------------------------------------------------------------------
@dataclass
class Contour:
    members: list[tuple[int, bool]]   # (índice da prim, invertida?)
    pts: np.ndarray                   # pontos discretizados
    closed: bool
    polygon: Optional[Polygon] = None
    parent: Optional[int] = None
    depth: int = 0
    children: list[int] = field(default_factory=list)
    repaired: bool = False


class _UF:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, a):
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def _cluster_points(points: np.ndarray, tol: float) -> list[int]:
    """Agrupa pontos a menos de `tol` (grade espacial). Devolve o id do grupo de cada ponto."""
    n = len(points)
    uf = _UF(n)
    cell = max(tol, 1e-6)
    grid: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i, (x, y) in enumerate(points):
        gx, gy = int(math.floor(x / cell)), int(math.floor(y / cell))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in grid.get((gx + dx, gy + dy), ()):
                    if (points[j][0] - x) ** 2 + (points[j][1] - y) ** 2 <= tol * tol:
                        uf.union(i, j)
        grid[(gx, gy)].append(i)
    return [uf.find(i) for i in range(n)]


def build_contours(prims: list[Prim], join_tol: float, curve_tol: float) -> list[Contour]:
    contours: list[Contour] = []
    open_idx = []
    flat_cache: dict[int, np.ndarray] = {}

    def flat(i):
        if i not in flat_cache:
            flat_cache[i] = flatten_prim(prims[i], curve_tol)
        return flat_cache[i]

    for i, p in enumerate(prims):
        if p.kind in ("TEXT", "MTEXT"):
            continue
        if prim_is_closed(p, join_tol):
            pts = flat(i)
            contours.append(Contour([(i, False)], pts, True))
        else:
            open_idx.append(i)

    if open_idx:
        ends = []
        for i in open_idx:
            a, b = prim_endpoints(prims[i], curve_tol)
            ends.append(a)
            ends.append(b)
        node = _cluster_points(np.array(ends), join_tol)
        # grafo: nó -> lista de (aresta local k, extremidade 0/1)
        adj: dict[int, list[tuple[int, int]]] = defaultdict(list)
        for k in range(len(open_idx)):
            adj[node[2 * k]].append((k, 0))
            adj[node[2 * k + 1]].append((k, 1))
        used = [False] * len(open_idx)

        def walk(start_k: int):
            used[start_k] = True
            chain = [(start_k, False)]
            start_node = node[2 * start_k]
            cur = node[2 * start_k + 1]
            closed = cur == start_node
            while not closed:
                nxt = None
                for k, end in adj[cur]:
                    if not used[k]:
                        nxt = (k, end)
                        break
                if nxt is None:
                    break
                k, end = nxt
                used[k] = True
                rev = end == 1
                chain.append((k, rev))
                cur = node[2 * k + (0 if rev else 1)]
                closed = cur == start_node
            return chain, closed, start_node, cur

        # começar pelos nós de grau ímpar (pontas de cadeias abertas) e depois o resto
        order = sorted(range(len(open_idx)),
                       key=lambda k: 0 if (len(adj[node[2 * k]]) % 2 == 1) else 1)
        for k in order:
            if used[k]:
                continue
            chain, closed, sn, en = walk(k)
            if not closed:
                # tenta estender para trás a partir do início
                back, _, _, _ = _extend_back(chain, sn, adj, used, node)
                chain = back + chain
                s_node = node[2 * chain[0][0] + (1 if chain[0][1] else 0)]
                closed = s_node == en
            pts_list = []
            for kk, rev in chain:
                pts = flat(open_idx[kk])
                pts = pts[::-1] if rev else pts
                if pts_list:
                    pts = pts[1:]
                pts_list.append(pts)
            pts = np.vstack(pts_list)
            members = [(open_idx[kk], rev) for kk, rev in chain]
            contours.append(Contour(members, pts, closed))

    for c in contours:
        if c.closed:
            poly = ring_to_polygon(c.pts)
            if poly is None:
                c.closed = False
            else:
                c.polygon = poly
                c.repaired = abs(poly.area - Polygon(c.pts).area) > 1e-6 and not Polygon(c.pts).is_valid
    return contours


def drop_duplicate_contours(contours: list[Contour], tol: float) -> tuple[list[Contour], int]:
    """Contornos fechados praticamente iguais (o mesmo desenho repetido, às vezes começando em outro
    ponto ou no sentido contrário, ou feito com outras entidades): fica só o primeiro."""
    kept: list[Contour] = []
    index: dict[tuple, list[Polygon]] = {}
    removed = 0
    for c in contours:
        if not c.closed or c.polygon is None:
            kept.append(c)
            continue
        poly = c.polygon
        x0, y0, x1, y1 = poly.bounds
        key = (round(x0), round(y0), round(x1), round(y1))
        lim = max(1e-6, poly.length * tol)
        dup = False
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                k2 = (key[0] + dx, key[1] + dy, key[2] + dx, key[3] + dy)
                for other in index.get(k2, ()):
                    if abs(other.area - poly.area) <= lim and other.symmetric_difference(poly).area <= lim:
                        dup = True
                        break
                if dup:
                    break
            if dup:
                break
        if dup:
            removed += 1
            continue
        index.setdefault(key, []).append(poly)
        kept.append(c)
    return kept, removed


def _extend_back(chain, start_node, adj, used, node):
    back = []
    cur = start_node
    while True:
        nxt = None
        for k, end in adj[cur]:
            if not used[k]:
                nxt = (k, end)
                break
        if nxt is None:
            break
        k, end = nxt
        used[k] = True
        # queremos chegar em `cur` pela extremidade final => se a extremidade em cur é 1, sentido normal
        rev = end == 0
        back.insert(0, (k, rev))
        cur = node[2 * k + (1 if rev else 0)]
    return back, None, None, None


# ---------------------------------------------------------------------------
# Peças
# ---------------------------------------------------------------------------
@dataclass
class _RawPart:
    prims: list[Prim]
    outer: Polygon
    holes: list[Polygon]
    outer_prim_idx: list[int]
    is_open: bool
    warnings: list[str]
    source_file: str
    material: str = ""
    tag: str = ""


def build_parts_from_prims(prims: list[Prim], source_file: str, join_tol: float,
                           curve_tol: float) -> tuple[list[_RawPart], list[tuple[Prim, bool]], list[str]]:
    warnings: list[str] = []
    name = os.path.basename(source_file)
    prims, removed = remove_duplicates(prims)
    if removed:
        warnings.append(f"{name}: {removed} entidade(s) duplicada(s)/sobreposta(s) removida(s).")

    contours = build_contours(prims, join_tol, curve_tol)
    contours, dup = drop_duplicate_contours(contours, max(join_tol, curve_tol))
    if dup:
        warnings.append(f"{name}: {dup} contorno(s) desenhado(s) em dobro ignorado(s) "
                        "(a peça não é contada duas vezes nem cortada duas vezes).")
    closed = [i for i, c in enumerate(contours) if c.closed]
    opened = [i for i, c in enumerate(contours) if not c.closed]

    # árvore de contenção (do maior para o menor)
    closed.sort(key=lambda i: -contours[i].polygon.area)
    polys = [contours[i].polygon for i in closed]
    tree = STRtree(polys) if polys else None
    buf_cache: dict[int, Polygon] = {}

    def container_of(geom, self_pos: Optional[int], self_area: float) -> Optional[int]:
        """Menor contorno fechado que contém `geom` (posição em `closed`)."""
        if tree is None:
            return None
        best = None
        best_area = math.inf
        for pos in tree.query(geom):
            pos = int(pos)
            if pos == self_pos:
                continue
            cand = polys[pos]
            if cand.area <= self_area * (1 + 1e-9) or cand.area >= best_area:
                continue
            if pos not in buf_cache:
                buf_cache[pos] = cand.buffer(join_tol)
            if buf_cache[pos].covers(geom):
                best, best_area = pos, cand.area
        return best

    for pos, ci in enumerate(closed):
        par = container_of(polys[pos], pos, polys[pos].area)
        contours[ci].parent = closed[par] if par is not None else None
    # profundidade e raiz
    def depth(ci):
        d = 0
        while contours[ci].parent is not None:
            ci = contours[ci].parent
            d += 1
        return d

    def root(ci):
        while contours[ci].parent is not None:
            ci = contours[ci].parent
        return ci

    for ci in closed:
        contours[ci].depth = depth(ci)
        if contours[ci].parent is not None:
            contours[contours[ci].parent].children.append(ci)

    # peça = contorno de profundidade par sem pai... regra: raiz da árvore
    groups: dict[int, dict] = {}
    for ci in closed:
        r = root(ci)
        g = groups.setdefault(r, {"contours": [], "extra": []})
        g["contours"].append(ci)

    # contornos abertos e textos: pertencem ao menor contorno fechado que os contém
    loose_items: list[tuple[str, object, object]] = []  # (tipo, ref, geom)
    for oi in opened:
        c = contours[oi]
        geom = LineString(c.pts) if len(c.pts) >= 2 else Point(c.pts[0])
        par = container_of(geom, None, 0.0)
        if par is not None:
            groups[root(closed[par])]["extra"].append(("contour", oi))
        else:
            loose_items.append(("contour", oi, geom))
    for pi, p in enumerate(prims):
        if p.kind not in ("TEXT", "MTEXT"):
            continue
        geom = Polygon(p.data["bbox"]).convex_hull
        par = container_of(geom.centroid, None, 0.0)
        if par is not None:
            groups[root(closed[par])]["extra"].append(("prim", pi))
        else:
            loose_items.append(("prim", pi, geom))

    raw_parts: list[_RawPart] = []
    preview: list[tuple[Prim, bool]] = []
    problem_prims: set[int] = set()

    for r, g in groups.items():
        rc = contours[r]
        idxs: list[int] = []
        outer_idx: list[int] = []
        for ci in sorted(g["contours"], key=lambda i: (contours[i].depth != 0, i)):
            for pi, _ in contours[ci].members:
                if ci == r:
                    outer_idx.append(len(idxs))
                idxs.append(pi)
        for kind, ref in g["extra"]:
            if kind == "contour":
                idxs.extend(pi for pi, _ in contours[ref].members)
            else:
                idxs.append(ref)
        # furos aproveitáveis: filhos diretos, sem nada dentro, mesma cor do externo
        outer_colors = {prim_rgb(prims[i]) for i, _ in rc.members}
        holes = []
        extra_geoms = []
        for kind, ref in g["extra"]:
            if kind == "contour":
                extra_geoms.append(LineString(contours[ref].pts) if len(contours[ref].pts) > 1 else None)
            else:
                extra_geoms.append(Polygon(prims[ref].data["bbox"]).convex_hull)
        extra_geoms = [x for x in extra_geoms if x is not None]
        for ch in rc.children:
            cc = contours[ch]
            if cc.children or cc.repaired or rc.repaired:
                continue
            if len(outer_colors) != 1 or {prim_rgb(prims[i]) for i, _ in cc.members} != outer_colors:
                continue
            hp = cc.polygon
            if any(hp.intersects(x) for x in extra_geoms):
                continue
            holes.append(hp)
        outer = Polygon(rc.polygon.exterior.coords)
        msgs = []
        if rc.repaired:
            msgs.append("Contorno inválido: usado envoltório conservador; confira o desenho no CAD.")
            warnings.append(f"{name}: {msgs[0]}")
            problem_prims.update(idxs)
        raw_parts.append(_RawPart([prims[i] for i in idxs], outer, holes, outer_idx, False, msgs,
                                  source_file))

    # itens soltos (contornos abertos e textos fora de peças): agrupar os que se tocam
    if loose_items:
        geoms = [it[2] for it in loose_items]
        uf = _UF(len(geoms))
        tree2 = STRtree([g.buffer(max(join_tol, 0.5)) for g in geoms])
        for i, g in enumerate(geoms):
            for j in tree2.query(g.buffer(max(join_tol, 0.5))):
                j = int(j)
                if j != i:
                    uf.union(i, j)
        clusters: dict[int, list[int]] = defaultdict(list)
        for i in range(len(geoms)):
            clusters[uf.find(i)].append(i)
        for members in clusters.values():
            idxs = []
            has_open = False
            pts_all = []
            for m in members:
                kind, ref, geom = loose_items[m]
                if kind == "contour":
                    has_open = True
                    idxs.extend(pi for pi, _ in contours[ref].members)
                    problem_prims.update(pi for pi, _ in contours[ref].members)
                    pts_all.append(contours[ref].pts)
                else:
                    idxs.append(ref)
                    pts_all.append(np.array(prims[ref].data["bbox"]))
            allpts = np.vstack(pts_all)
            hull = MultiPoint([tuple(p) for p in allpts]).convex_hull
            if not isinstance(hull, Polygon) or hull.area < 1e-6:
                hull = hull.buffer(max(0.1, curve_tol), cap_style=3)
            msgs = []
            if has_open:
                msgs.append("Contorno aberto: a linha não fecha. Ela será encaixada usando o "
                            "contorno envolvente; confira no CAD se falta um trecho.")
            else:
                msgs.append("Marca/texto solto fora de qualquer peça: tratado como peça própria.")
            raw_parts.append(_RawPart([prims[i] for i in idxs], Polygon(hull.exterior.coords), [],
                                      [], has_open, msgs, source_file))

    for i, p in enumerate(prims):
        preview.append((p, i in problem_prims))
    n_open = sum(1 for rp in raw_parts if rp.is_open)
    if n_open:
        warnings.append(f"{name}: {n_open} grupo(s) com contorno aberto (destacado em vermelho).")
    return raw_parts, preview, warnings


# ---------------------------------------------------------------------------
# Peças idênticas
# ---------------------------------------------------------------------------
def _feature_angles(poly: Polygon) -> list[float]:
    c = np.asarray(poly.exterior.coords)
    seg = np.diff(c, axis=0)
    ln = np.hypot(seg[:, 0], seg[:, 1])
    angs = np.degrees(np.arctan2(seg[:, 1], seg[:, 0]))
    order = np.argsort(-ln)[:4]
    out = [float(angs[i]) for i in order]
    # eixo principal (momentos de inércia)
    pts = c[:-1] - np.array(poly.centroid.coords[0])
    if len(pts) >= 3:
        cov = np.cov(pts.T)
        w, v = np.linalg.eigh(cov)
        out.append(float(np.degrees(math.atan2(v[1, -1], v[0, -1]))))
    return out


def _linework(rp: _RawPart, tol: float) -> GeometryCollection:
    lines = []
    for p in rp.prims:
        pts = flatten_prim(p, tol)
        if len(pts) >= 2:
            lines.append(LineString(pts))
    return MultiLineString(lines) if lines else GeometryCollection()


def _same_part(a: _RawPart, b: _RawPart, la, lb, tol: float) -> Optional[float]:
    """Se b é igual a a por rotação+translação, devolve o ângulo (graus) de b->a."""
    if a.is_open != b.is_open or len(a.holes) != len(b.holes) or a.material != b.material \
            or a.tag != b.tag:
        return None
    if abs(a.outer.area - b.outer.area) > max(0.5, 0.005 * a.outer.area):
        return None
    if abs(a.outer.length - b.outer.length) > max(0.5, 0.005 * a.outer.length):
        return None
    def operation(p):
        return (p.layer, prim_rgb(p), p.kind in ("TEXT", "MTEXT"))
    if {operation(p) for p in a.prims} != {operation(p) for p in b.prims}:
        return None
    ta = sorted(p.data.get("text", "") for p in a.prims if p.kind in ("TEXT", "MTEXT"))
    tb = sorted(p.data.get("text", "") for p in b.prims if p.kind in ("TEXT", "MTEXT"))
    if ta != tb:
        return None
    ca = a.outer.centroid
    cb = b.outer.centroid
    la0 = affinity.translate(la, -ca.x, -ca.y)
    lb0 = affinity.translate(lb, -cb.x, -cb.y)
    fa = _feature_angles(a.outer)
    fb = _feature_angles(b.outer)
    cands = set()
    for x in fa[:2]:
        for y in fb:
            for extra in (0.0, 180.0):
                cands.add(round((x - y + extra) % 360.0, 4))
    for ang in sorted(cands):
        rb = affinity.rotate(lb0, ang, origin=(0, 0))
        if la0.hausdorff_distance(rb) <= tol:
            # A geometria de cada cor/camada também precisa coincidir.
            for op in {operation(p) for p in a.prims}:
                def lines(rp, center):
                    seq = [flatten_prim(p, tol / 4) for p in rp.prims if operation(p) == op]
                    geom = MultiLineString([pts for pts in seq if len(pts) >= 2])
                    return affinity.translate(geom, -center.x, -center.y)
                if lines(a, ca).hausdorff_distance(affinity.rotate(lines(b, cb), ang, origin=(0, 0))) > tol:
                    break
            else:
                return ang
    return None


def group_identical(raw: list[_RawPart], curve_tol: float) -> list[tuple[_RawPart, int]]:
    groups: list[list] = []  # [template, count, linework]
    for rp in raw:
        # cópias do mesmo objeto (arquivo multiplicado pela quantidade): mesmo grupo, sem comparar
        same = next((g for g in groups if g[0] is rp), None)
        if same is not None:
            same[1] += 1
            continue
        tol = 0.001  # equivalência de fabricação, independente da resolução do encaixe
        lw = None
        for g in groups:
            a = g[0]
            # testes baratos antes de calcular os contornos (que custam caro)
            if a.is_open != rp.is_open or len(a.holes) != len(rp.holes) or a.material != rp.material \
                    or a.tag != rp.tag or abs(a.outer.area - rp.outer.area) > max(0.5, 0.005 * a.outer.area):
                continue
            if lw is None:
                lw = _linework(rp, tol / 4)
            if g[2] is None:
                g[2] = _linework(a, tol / 4)
            if _same_part(a, rp, g[2], lw, tol) is not None:
                g[1] += 1
                break
        else:
            groups.append([rp, 1, lw])
    return [(g[0], g[1]) for g in groups]


# ---------------------------------------------------------------------------
# API principal
# ---------------------------------------------------------------------------
def make_part(rp: _RawPart, pid: str, name: str, qty: int) -> Part:
    c = rp.outer.centroid
    tf = Transform(0.0, False, 0.0, 0.0, pre=(-c.x, -c.y))
    prims = [transform_prim(p, tf) for p in rp.prims]
    outer = affinity.translate(rp.outer, -c.x, -c.y)
    holes = [affinity.translate(h, -c.x, -c.y) for h in rp.holes]
    return Part(id=pid, name=name, source_file=rp.source_file, outer=outer, holes=holes,
                prims=prims, outer_prim_idx=list(rp.outer_prim_idx), quantity=qty,
                file_quantity=qty, warnings=list(rp.warnings), is_open=rp.is_open,
                material=rp.material, tag=rp.tag)


def import_files(paths: list[str], join_tol: float = 0.05, curve_tol: float = 0.1,
                 raw_files: Optional[list[RawFile]] = None,
                 units_override: Optional[int] = None, ignore_text: bool = False,
                 excluded_layers: Optional[set] = None,
                 multipliers: Optional[dict] = None,
                 file_materials: Optional[dict] = None,
                 file_tags: Optional[dict] = None,
                 file_units: Optional[dict] = None) -> ImportReport:
    """Importa vários DXF e devolve as peças agrupadas."""
    warnings: list[str] = []
    all_raw: list[_RawPart] = []
    preview: list[tuple[Prim, bool]] = []
    unit_notes = {}
    x_offset = 0.0
    files_ok = []
    raws = raw_files if raw_files is not None else []
    if raw_files is None:
        from .dxf_import import DXFImportError
        for path in paths:
            try:
                unit = units_override if units_override is not None else (file_units or {}).get(os.path.abspath(path))
                raws.append(read_dxf(path, unit, ignore_text, excluded_layers))
            except DXFImportError as e:
                warnings.append(str(e))
    suspicious = []
    layers: dict = {}
    for rf in raws:
        for ln, info in rf.layers.items():
            cur = layers.setdefault(ln, {"color": info["color"], "count": 0, "texts": 0})
            cur["count"] += info["count"]
            cur["texts"] += info["texts"]
        if rf.suggested_units is not None:
            suspicious.append((rf.path, rf.declared_units, rf.suggested_units))
        files_ok.append(rf.path)
        unit_notes[rf.path] = rf.unit_note
        warnings.extend(rf.warnings)
        parts, pv, w = build_parts_from_prims(rf.prims, rf.path, join_tol, curve_tol)
        warnings.extend(w)
        if file_materials:
            mat = file_materials.get(os.path.abspath(rf.path), "")
            for rp in parts:
                rp.material = mat
        if file_tags:
            tag = str(file_tags.get(os.path.abspath(rf.path), "") or "")
            for rp in parts:
                rp.tag = tag
        mult = 1
        if multipliers:
            mult = max(1, int(multipliers.get(os.path.abspath(rf.path), 1)))
        all_raw.extend(parts * mult)
        # pré-visualização: arquivos lado a lado
        if pv:
            pts = np.vstack([flatten_prim(p, 1.0) for p, _ in pv])
            minx, miny = pts.min(axis=0)
            maxx = pts[:, 0].max()
            tf = Transform(0, False, x_offset - minx, -miny)
            for p, prob in pv:
                preview.append((transform_prim(p, tf), prob))
            x_offset += (maxx - minx) + 20.0

    # ordem estável: maiores primeiro, depois posição
    all_raw.sort(key=lambda r: (r.material, r.tag, -round(r.outer.area, 3), round(r.outer.centroid.x, 3),
                                round(r.outer.centroid.y, 3)))
    grouped = group_identical(all_raw, curve_tol)
    parts = []
    for i, (rp, qty) in enumerate(grouped):
        w, h = rp.outer.bounds[2] - rp.outer.bounds[0], rp.outer.bounds[3] - rp.outer.bounds[1]
        name = f"Peça {i + 1} ({w:.0f}×{h:.0f})"
        if rp.tag:
            name = f"{rp.tag} · {name}"
        parts.append(make_part(rp, f"P{i + 1:03d}", name, qty))
    return ImportReport(parts=parts, preview=preview, warnings=warnings, files=files_ok,
                        unit_notes=unit_notes, extra={"suspicious_units": suspicious, "layers": layers})
