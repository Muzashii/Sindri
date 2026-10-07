"""No-Fit Polygon (NFP) e Inner-Fit Polygon (IFP), com cache.

Todas as operações usam coordenadas inteiras do Clipper (1 unidade = 1 µm).

Para cada peça, uma *variante* é a peça girada/espelhada, simplificada e com
offset de folga. Garantia geométrica (ver DECISIONS.md):

    offset = espaçamento/2 + tol. de curva + tol. de simplificação + tol. dos arcos do offset

assim, se as variantes de duas peças não se sobrepõem, a geometria real fica
a pelo menos `espaçamento` de distância.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import math

import numpy as np
import pyclipper
from shapely.geometry import Polygon

from .geometry import (CLIPPER_SCALE, Transform, offset_int, polygon_to_int, from_int_path)

MIN_NFP_HOLE_MM2 = 0.25  # furos de NFP menores que isso são artefatos numéricos
EPS_INT = 2  # 0.002 mm: permite encostar exatamente (evita regiões degeneradas)


@dataclass
class PartShape:
    """Dados mínimos (serializáveis) de uma peça para o encaixe."""
    id: str
    outer: np.ndarray            # coordenadas locais (N×2), sem repetir o 1º ponto
    holes: list[np.ndarray]
    net_area: float
    rotations: list[float]
    material: str = ""


@dataclass
class Variant:
    key: tuple
    path: list                   # contorno com folga (int, anti-horário)
    bbox: tuple[int, int, int, int]
    holes: list[list]            # furos encolhidos (int, anti-horário)
    hole_areas: list[float]
    area_int: float              # área do contorno com folga (unid²)


def round_rot(r: float) -> float:
    return round(float(r) % 360.0, 4)


# nível de detalhe do contorno usado no encaixe: (raio de fechamento de reentrâncias, simplificação) em mm
DETAIL_LEVELS = {0: (0.0, 0.05), 1: (10.0, 0.2), 2: (20.0, 0.5)}
# Contornos com muitos vértices (engrenagens, textos vetorizados) deixam o NFP lentíssimo: no modo
# "Preciso" uma engrenagem de 800 vértices levava ~40 s só para a 1ª solução (agora ~3 s). Acima deste número a
# simplificação aumenta (até MAX_SIMPLIFY_MM) e a folga da peça cresce junto, então continua seguro.
MAX_OUTLINE_VERTICES = 100
MAX_SIMPLIFY_MM = 0.4


def _ok(g) -> bool:
    return not g.is_empty and g.geom_type == "Polygon"


def _close(poly: Polygon, r: float) -> Polygon:
    """Fechamento morfológico (preenche reentrâncias com abertura < 2r); nunca perde área."""
    if r <= 0:
        return poly
    closed = poly.buffer(r, join_style=2, mitre_limit=5).buffer(-r, join_style=2, mitre_limit=5)
    if not _ok(closed):
        return poly
    # a união com o original garante que nada da peça fica de fora (o "chanfro" das quinas vivas
    # que o buffer com limite de esquadria corta volta pela união)
    out = closed.union(poly)
    if out.geom_type != "Polygon":
        out = max(getattr(out, "geoms", [out]), key=lambda g: g.area)
    if not _ok(out):
        return poly
    out = Polygon(out.exterior.coords)
    return out if out.covers(poly.buffer(-1e-6)) else poly


class NFPCache:
    def __init__(self, shapes: dict[str, PartShape], spacing: float, curve_tol: float,
                 part_in_part: bool = True, detail: int = 1):
        self.shapes = shapes
        self.spacing = float(spacing)
        self.curve_tol = float(curve_tol)
        self.close_r, simp = DETAIL_LEVELS.get(int(detail), DETAIL_LEVELS[1])
        self.simplify_tol = max(simp, self.curve_tol * 0.5)
        # furos usam offset arredondado: as cordas dos arcos ficam até arc_tol para dentro
        self.arc_tol = max(0.02, self.curve_tol / 2)
        # contorno externo usa offset em quina viva (miter), que sempre contém o arredondado
        self.offset = self.spacing / 2.0 + self.curve_tol + self.simplify_tol
        self.hole_shrink = self.spacing / 2.0 + self.curve_tol + self.arc_tol
        self._base: dict[str, np.ndarray] = {}
        self._simp: dict[str, float] = {}       # tolerância de simplificação usada em cada peça
        self.part_in_part = part_in_part
        self._variants: dict[tuple, Variant] = {}
        self._nfp: dict[tuple, list] = {}
        self._nfp_np: dict[tuple, tuple] = {}
        self._hole_ifp: dict[tuple, list] = {}
        self.hits = 0
        self.misses = 0
        self.max_entries = 4096

    def _trim(self, cache):
        while len(cache) > self.max_entries:
            cache.pop(next(iter(cache)))

    # ------------------------------------------------------------------
    def variant(self, pid: str, rot: float, mirror: bool = False) -> Variant:
        key = (pid, round_rot(rot), bool(mirror))
        v = self._variants.get(key)
        if v is not None:
            return v
        sh = self.shapes[pid]
        tf = Transform(rot, mirror)
        simp = Polygon(tf.pts_array(self._base_outline(pid)))
        if not simp.is_valid:
            simp = simp.buffer(0)
        base = polygon_to_int(simp)
        pco = pyclipper.PyclipperOffset(2.0)
        pco.AddPath(base, pyclipper.JT_MITER, pyclipper.ET_CLOSEDPOLYGON)
        extra = self._simp.get(pid, self.simplify_tol) - self.simplify_tol
        # simplificação maior que a do nível de detalhe: folga extra proporcional (garante que o contorno
        # usado no cálculo contém a peça inteira, mesmo com os arredondamentos do offset)
        offset = self.offset + (extra * 1.25 + 0.05 if extra > 1e-9 else 0.0)
        off = pco.Execute(offset * CLIPPER_SCALE)
        off = [p for p in off if pyclipper.Orientation(p)]
        path = max(off, key=lambda p: abs(pyclipper.Area(p))) if off else base
        arr = np.asarray(path)
        bbox = (int(arr[:, 0].min()), int(arr[:, 1].min()), int(arr[:, 0].max()), int(arr[:, 1].max()))
        holes, hole_areas = [], []
        if self.part_in_part:
            for h in sh.holes:
                hp = Polygon(tf.pts_array(h))
                if not hp.is_valid:
                    hp = hp.buffer(0)
                if hp.is_empty or hp.geom_type != "Polygon":
                    continue
                shrunk = offset_int([polygon_to_int(hp)], -self.hole_shrink, arc_tol_mm=self.arc_tol)
                for s in shrunk:
                    if pyclipper.Orientation(s) and abs(pyclipper.Area(s)) > 0:
                        holes.append(s)
                        hole_areas.append(abs(pyclipper.Area(s)))
        v = Variant(key, path, bbox, holes, hole_areas, abs(pyclipper.Area(path)))
        self._variants[key] = v
        self._trim(self._variants)
        return v

    def _base_outline(self, pid: str) -> np.ndarray:
        """Contorno externo simplificado (e com reentrâncias estreitas fechadas) em coordenadas locais.

        O fechamento morfológico (cresce r e encolhe r, com quinas vivas) só ACRESCENTA área:
        preenche os dentes/rasgos do contorno com abertura menor que 2r. Isso reduz muito o
        número de vértices (peças com encaixes tipo "finger joint") e acelera o NFP.
        Contornos que continuam com vértices demais (engrenagens, letras) recebem simplificação e
        fechamento maiores só para eles — sempre aumentando a área, nunca cortando a peça.
        """
        b = self._base.get(pid)
        if b is not None:
            return b
        poly = Polygon(self.shapes[pid].outer)
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.geom_type != "Polygon":
            poly = max(getattr(poly, "geoms", [poly]), key=lambda g: g.area)
        closes = [self.close_r] + [r for r in (2.0, 5.0, 10.0) if r > self.close_r]
        x0, y0, x1, y1 = poly.bounds
        # peças grandes (meio metro ou mais) toleram simplificar mais: 1 mm numa peça de 1 m é nada,
        # e o NFP entre duas delas é o que mais pesa (vértices × vértices)
        max_tol = max(MAX_SIMPLIFY_MM, min(1.0, 0.0008 * math.hypot(x1 - x0, y1 - y0)))
        b, tol = None, self.simplify_tol
        for close_r in closes:
            closed = _close(poly, close_r)
            tol = self.simplify_tol
            simp = closed.simplify(tol, preserve_topology=True)
            while (_ok(simp) and len(simp.exterior.coords) > MAX_OUTLINE_VERTICES and tol < max_tol):
                tol = min(max_tol, tol * 1.6)
                simp = closed.simplify(tol, preserve_topology=True)
            if not _ok(simp):
                simp, tol = closed, self.simplify_tol
            b = np.asarray(simp.exterior.coords)[:-1]
            if len(b) <= MAX_OUTLINE_VERTICES:
                break
        self._base[pid] = b
        self._simp[pid] = tol
        return b

    def export_state(self) -> tuple:
        return (self._base, self._variants, self._nfp, self._simp)

    def import_state(self, state: tuple):
        base, variants, nfp = state[:3]
        if len(state) > 3:
            self._simp.update(state[3])
        self._base.update(base)
        self._variants.update(variants)
        self._nfp.update(nfp)

    # ------------------------------------------------------------------
    def nfp(self, a: Variant, b: Variant) -> list:
        """Região proibida para o ponto de referência de B com A na origem."""
        key = (a.key, b.key)
        r = self._nfp.get(key)
        if r is not None:
            self.hits += 1
            return r
        self.misses += 1
        neg_b = [(-x, -y) for x, y in b.path]
        ms = pyclipper.MinkowskiSum(neg_b, a.path, True)
        pc = pyclipper.Pyclipper()
        if ms:
            pc.AddPaths(ms, pyclipper.PT_SUBJECT, True)
        # preenche o interior: B inteira dentro de A  (A − b0)  e  A inteira dentro de B  (a0 − B)
        dx, dy = neg_b[0]
        pc.AddPath([(x + dx, y + dy) for x, y in a.path], pyclipper.PT_SUBJECT, True)
        ax, ay = a.path[0]
        pc.AddPath([(x + ax, y + ay) for x, y in neg_b], pyclipper.PT_SUBJECT, True)
        res = pc.Execute(pyclipper.CT_UNION, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)
        # encolhe levemente para permitir contato exato
        pco = pyclipper.PyclipperOffset()
        pco.AddPaths(res, pyclipper.JT_MITER, pyclipper.ET_CLOSEDPOLYGON)
        res = pco.Execute(-EPS_INT)
        # remove "furos" minúsculos (artefatos numéricos da soma de Minkowski)
        min_hole = MIN_NFP_HOLE_MM2 * CLIPPER_SCALE ** 2
        res = [p for p in res if pyclipper.Orientation(p) or abs(pyclipper.Area(p)) >= min_hole]
        if any(not pyclipper.Orientation(p) for p in res):
            res = self._real_holes(res, a, b)
        self._nfp[key] = res
        self._trim(self._nfp)
        return res

    @staticmethod
    def _real_holes(res: list, a: Variant, b: Variant) -> list:
        """Um furo no NFP = posições em que B cabe num recorte de A. Emendas numéricas da soma de
        Minkowski às vezes deixam "furos" falsos (fendas finíssimas) e o decodificador colocava B em
        cima de A (lote 8787/8788/8791). Cada furo é conferido: B posicionado num ponto de dentro dele
        não pode sobrepor A; se sobrepõe, o furo é descartado."""
        from shapely import affinity
        A = Polygon(a.path).buffer(0)
        B = Polygon(b.path).buffer(0)
        tol = (0.05 * CLIPPER_SCALE) ** 2 * 10
        out = []
        for p in res:
            if pyclipper.Orientation(p):
                out.append(p)
                continue
            hole = Polygon(p).buffer(0)
            if hole.is_empty:
                continue
            q = hole.representative_point()
            moved = affinity.translate(B, q.x, q.y)
            if A.intersection(moved).area <= tol:
                out.append(p)
        return out

    def nfp_np(self, a: Variant, b: Variant) -> tuple[list[np.ndarray], tuple[int, int, int, int]]:
        """NFP como arrays numpy (translação rápida) + caixa delimitadora."""
        key = (a.key, b.key)
        r = self._nfp_np.get(key)
        if r is None:
            paths = [np.asarray(p, dtype=np.int64) for p in self.nfp(a, b) if len(p) >= 3]
            if paths:
                allp = np.vstack(paths)
                bb = (int(allp[:, 0].min()), int(allp[:, 1].min()), int(allp[:, 0].max()), int(allp[:, 1].max()))
            else:
                bb = (0, 0, -1, -1)
            r = (paths, bb)
            self._nfp_np[key] = r
            self._trim(self._nfp_np)
        else:
            self.hits += 1
        return r

    # ------------------------------------------------------------------
    def ifp_rect(self, rect: tuple[int, int, int, int], b: Variant) -> Optional[list]:
        """Retângulo de posições do ponto de referência de B dentro de `rect`."""
        x0 = rect[0] - b.bbox[0] - EPS_INT
        y0 = rect[1] - b.bbox[1] - EPS_INT
        x1 = rect[2] - b.bbox[2] + EPS_INT
        y1 = rect[3] - b.bbox[3] + EPS_INT
        if x1 < x0 or y1 < y0:
            return None
        if x1 == x0:
            x1 += 1
        if y1 == y0:
            y1 += 1
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]

    # ------------------------------------------------------------------
    def hole_ifp(self, a: Variant, hole_idx: int, b: Variant) -> list:
        """Posições de B (referência) totalmente dentro do furo `hole_idx` de A (A na origem)."""
        key = (a.key, hole_idx, b.key)
        r = self._hole_ifp.get(key)
        if r is not None:
            return r
        hole = a.holes[hole_idx]
        res: list = []
        if b.area_int < a.hole_areas[hole_idx]:
            harr = np.asarray(hole)
            hb = (int(harr[:, 0].min()), int(harr[:, 1].min()), int(harr[:, 0].max()), int(harr[:, 1].max()))
            rect = self.ifp_rect(hb, b)
            if rect is not None:
                neg_b = [(-x, -y) for x, y in b.path]
                sweep = pyclipper.MinkowskiSum(neg_b, hole, True)
                pc = pyclipper.Pyclipper()
                pc.AddPath(rect, pyclipper.PT_SUBJECT, True)
                if sweep:
                    pc.AddPaths(sweep, pyclipper.PT_CLIP, True)
                cand = pc.Execute(pyclipper.CT_DIFFERENCE, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)
                hpoly = Polygon(from_int_path(hole)).buffer(1e-6)
                bpoly = Polygon(from_int_path(b.path))
                for comp in cand:
                    if not pyclipper.Orientation(comp) or len(comp) < 3:
                        continue
                    cp = Polygon(from_int_path(comp))
                    if cp.is_empty:
                        continue
                    pt = cp.representative_point()
                    test = Polygon(np.asarray(bpoly.exterior.coords) + [pt.x, pt.y])
                    if hpoly.covers(test):
                        res.append(comp)
        self._hole_ifp[key] = res
        self._trim(self._hole_ifp)
        return res

    def stats(self) -> dict:
        return {"variants": len(self._variants), "nfp": len(self._nfp), "hits": self.hits,
                "misses": self.misses}
