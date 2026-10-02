"""Geometria: discretização, transformações, offset e utilitários.

Convenções
----------
* Unidades em milímetros, Y para cima.
* Transformação de peça: p' = R(rot) · M(espelho) · (p + pré-deslocamento) + (dx, dy)
  onde M espelha em X (x -> -x) e R gira no sentido anti-horário.
"""
from __future__ import annotations

import math
from typing import Iterable, Optional

import numpy as np
import pyclipper
from ezdxf.math import BSpline, ConstructionEllipse, Vec3, bulge_to_arc, fit_points_to_cad_cv
from shapely.geometry import Polygon, MultiPolygon, LinearRing
from shapely import make_valid

from .models import Prim

CLIPPER_SCALE = 1000.0  # 1 unidade inteira = 1 µm

# ---------------------------------------------------------------------------
# Cores
# ---------------------------------------------------------------------------
try:
    from ezdxf.colors import aci2rgb as _aci2rgb
except Exception:  # pragma: no cover
    _aci2rgb = None


def aci_to_rgb(aci: int) -> tuple[int, int, int]:
    if aci == 7 or aci <= 0 or aci > 255:
        return (0, 0, 0)  # "branco/preto": desenhado em preto no fundo claro
    if _aci2rgb is not None:
        c = _aci2rgb(aci)
        return (int(c[0]), int(c[1]), int(c[2]))
    return (0, 0, 0)  # pragma: no cover


def prim_rgb(p: Prim) -> tuple[int, int, int]:
    return tuple(p.rgb) if p.rgb else aci_to_rgb(p.color)


# ---------------------------------------------------------------------------
# Discretização
# ---------------------------------------------------------------------------
def _arc_points(cx, cy, r, a0_deg, a1_deg, tol) -> np.ndarray:
    a0 = math.radians(a0_deg)
    a1 = math.radians(a1_deg)
    while a1 <= a0 + 1e-12:
        a1 += 2 * math.pi
    sweep = a1 - a0
    n = _segments_for(r, sweep, tol)
    t = np.linspace(a0, a1, n + 1)
    return np.column_stack([cx + r * np.cos(t), cy + r * np.sin(t)])


def _segments_for(r: float, sweep: float, tol: float) -> int:
    if r <= tol or r <= 0:
        return max(1, int(math.ceil(sweep / (math.pi / 2))))
    step = 2.0 * math.acos(max(-1.0, min(1.0, 1.0 - tol / r)))
    if step <= 1e-9:
        step = 1e-3
    return max(2, int(math.ceil(sweep / step)))


def _bulge_segment(p0, p1, bulge, tol) -> np.ndarray:
    if abs(bulge) < 1e-12:
        return np.array([p0, p1], dtype=float)
    center, sa, ea, r = bulge_to_arc(p0, p1, bulge)
    pts = _arc_points(center.x, center.y, r, math.degrees(sa), math.degrees(ea), tol)
    if bulge < 0:  # arco horário: bulge_to_arc devolve no sentido anti-horário
        pts = pts[::-1]
    pts[0] = p0
    pts[-1] = p1
    return pts


def flatten_prim(p: Prim, tol: float = 0.1) -> np.ndarray:
    """Discretiza uma primitiva em uma polilinha (N×2). Fechadas repetem o 1º ponto."""
    d = p.data
    k = p.kind
    if k == "LINE":
        return np.array([d["s"], d["e"]], dtype=float)
    if k == "CIRCLE":
        pts = _arc_points(d["c"][0], d["c"][1], d["r"], 0.0, 360.0, tol)
        pts[-1] = pts[0]
        return pts
    if k == "ARC":
        return _arc_points(d["c"][0], d["c"][1], d["r"], d["a0"], d["a1"], tol)
    if k == "POLY":
        pts = d["pts"]
        if not pts:
            return np.zeros((0, 2))
        out = [np.array([pts[0][:2]], dtype=float)]
        n = len(pts)
        segs = n if d.get("closed") else n - 1
        for i in range(segs):
            a = pts[i]
            b = pts[(i + 1) % n]
            seg = _bulge_segment((a[0], a[1]), (b[0], b[1]), a[2] if len(a) > 2 else 0.0, tol)
            out.append(seg[1:])
        res = np.vstack(out)
        return res
    if k == "ELLIPSE":
        e = ConstructionEllipse(
            center=Vec3(d["c"][0], d["c"][1], 0),
            major_axis=Vec3(d["major"][0], d["major"][1], 0),
            ratio=d["ratio"],
            start_param=d["t0"],
            end_param=d["t1"],
        )
        pts = np.array([(v.x, v.y) for v in e.flattening(tol)], dtype=float)
        return pts
    if k == "SPLINE":
        return np.array([(v.x, v.y) for v in _spline_obj(d).flattening(tol, segments=4)], dtype=float)
    if k in ("TEXT", "MTEXT"):
        bb = np.array(d["bbox"], dtype=float)
        return np.vstack([bb, bb[:1]])
    raise ValueError(f"Tipo de primitiva desconhecido: {k}")


def _spline_obj(d: dict) -> BSpline:
    if d.get("ctrl"):
        ctrl = [Vec3(x, y, 0) for x, y in d["ctrl"]]
        order = int(d["degree"]) + 1
        knots = d.get("knots") or None
        weights = d.get("weights") or None
        if knots is not None and len(knots) != len(ctrl) + order:
            knots = None
        if weights is not None and len(weights) != len(ctrl):
            weights = None
        return BSpline(ctrl, order=order, knots=knots, weights=weights)
    fit = [Vec3(x, y, 0) for x, y in d["fit"]]
    return fit_points_to_cad_cv(fit)


def prim_is_closed(p: Prim, tol: float) -> bool:
    k = p.kind
    if k == "CIRCLE":
        return True
    if k in ("TEXT", "MTEXT"):
        return False
    if k == "POLY" and p.data.get("closed"):
        return True
    if k == "ELLIPSE":
        sweep = (p.data["t1"] - p.data["t0"]) % (2 * math.pi)
        if sweep < 1e-9 or abs(sweep - 2 * math.pi) < 1e-9:
            return True
    if k == "LINE":
        return False
    pts = flatten_prim(p, max(tol, 0.05))
    return len(pts) > 2 and float(np.hypot(*(pts[0] - pts[-1]))) <= tol


def prim_endpoints(p: Prim, tol: float = 0.1) -> tuple[np.ndarray, np.ndarray]:
    d = p.data
    if p.kind == "LINE":
        return np.array(d["s"], float), np.array(d["e"], float)
    if p.kind == "ARC":
        c, r = d["c"], d["r"]
        a0, a1 = math.radians(d["a0"]), math.radians(d["a1"])
        return (np.array([c[0] + r * math.cos(a0), c[1] + r * math.sin(a0)]),
                np.array([c[0] + r * math.cos(a1), c[1] + r * math.sin(a1)]))
    if p.kind == "POLY":
        return np.array(d["pts"][0][:2], float), np.array(d["pts"][-1][:2], float)
    pts = flatten_prim(p, tol)
    return pts[0].copy(), pts[-1].copy()


# ---------------------------------------------------------------------------
# Transformações
# ---------------------------------------------------------------------------
class Transform:
    """p' = R(rot)·M(mirror)·(p + pre) + (dx, dy)."""

    def __init__(self, rot_deg: float = 0.0, mirror: bool = False, dx: float = 0.0,
                 dy: float = 0.0, pre: tuple[float, float] = (0.0, 0.0)):
        self.rot = float(rot_deg)
        self.mirror = bool(mirror)
        self.dx, self.dy = float(dx), float(dy)
        self.pre = (float(pre[0]), float(pre[1]))
        r = math.radians(self.rot)
        self.c, self.s = math.cos(r), math.sin(r)

    def pt(self, p) -> list[float]:
        x = p[0] + self.pre[0]
        y = p[1] + self.pre[1]
        if self.mirror:
            x = -x
        return [self.c * x - self.s * y + self.dx, self.s * x + self.c * y + self.dy]

    def vec(self, v) -> list[float]:
        x, y = v[0], v[1]
        if self.mirror:
            x = -x
        return [self.c * x - self.s * y, self.s * x + self.c * y]

    def angle(self, a_deg: float) -> float:
        """Transforma uma direção (graus)."""
        if self.mirror:
            a_deg = 180.0 - a_deg
        return (a_deg + self.rot) % 360.0

    def pts_array(self, arr: np.ndarray) -> np.ndarray:
        a = np.asarray(arr, float).copy()
        a[:, 0] += self.pre[0]
        a[:, 1] += self.pre[1]
        if self.mirror:
            a[:, 0] = -a[:, 0]
        x = self.c * a[:, 0] - self.s * a[:, 1] + self.dx
        y = self.s * a[:, 0] + self.c * a[:, 1] + self.dy
        return np.column_stack([x, y])


def transform_prim(p: Prim, tf: Transform) -> Prim:
    d = p.data
    k = p.kind
    nd: dict
    if k == "LINE":
        nd = {"s": tf.pt(d["s"]), "e": tf.pt(d["e"])}
    elif k == "CIRCLE":
        nd = {"c": tf.pt(d["c"]), "r": d["r"]}
    elif k == "ARC":
        if tf.mirror:
            a0, a1 = tf.angle(d["a1"]), tf.angle(d["a0"])
        else:
            a0, a1 = tf.angle(d["a0"]), tf.angle(d["a1"])
        nd = {"c": tf.pt(d["c"]), "r": d["r"], "a0": a0, "a1": a1}
    elif k == "POLY":
        sign = -1.0 if tf.mirror else 1.0
        nd = {"pts": [tf.pt(v) + [sign * (v[2] if len(v) > 2 else 0.0)] for v in d["pts"]],
              "closed": d.get("closed", False)}
    elif k == "ELLIPSE":
        t0, t1 = d["t0"], d["t1"]
        if tf.mirror:
            t0, t1 = -d["t1"], -d["t0"]
        nd = {"c": tf.pt(d["c"]), "major": tf.vec(d["major"]), "ratio": d["ratio"],
              "t0": t0, "t1": t1}
    elif k == "SPLINE":
        nd = dict(d)
        nd["ctrl"] = [tf.pt(v) for v in d.get("ctrl") or []]
        nd["fit"] = [tf.pt(v) for v in d.get("fit") or []]
    elif k in ("TEXT", "MTEXT"):
        nd = dict(d)
        nd["p"] = tf.pt(d["p"])
        if d.get("p2") is not None:
            nd["p2"] = tf.pt(d["p2"])
        nd["rot"] = (d.get("rot", 0.0) + tf.rot) % 360.0 if not tf.mirror else (tf.angle(d.get("rot", 0.0)) + 180.0) % 360.0
        if tf.mirror:
            nd["mirror"] = not d.get("mirror", False)
        nd["bbox"] = [tf.pt(v) for v in d["bbox"]]
    else:  # pragma: no cover
        raise ValueError(k)
    return Prim(k, nd, p.layer, p.color, p.rgb)


# ---------------------------------------------------------------------------
# Polígonos
# ---------------------------------------------------------------------------
def ring_to_polygon(pts: np.ndarray) -> Optional[Polygon]:
    """Cria um polígono válido a partir de um anel (pode ter auto-interseção)."""
    if len(pts) < 3:
        return None
    try:
        poly = Polygon(pts)
    except Exception:
        return None
    if not poly.is_valid:
        fixed = make_valid(poly)
        polys = [g for g in getattr(fixed, "geoms", [fixed]) if isinstance(g, Polygon)]
        polys += [pp for g in getattr(fixed, "geoms", []) if isinstance(g, MultiPolygon) for pp in g.geoms]
        if isinstance(fixed, MultiPolygon):
            polys = list(fixed.geoms)
        if not polys:
            return None
        poly = max(polys, key=lambda g: g.area)
    if poly.area <= 1e-9:
        return None
    return Polygon(poly.exterior.coords)


def rotate_coords(coords: np.ndarray, rot_deg: float, mirror: bool = False) -> np.ndarray:
    return Transform(rot_deg, mirror).pts_array(coords)


def to_int_path(coords: Iterable) -> list[tuple[int, int]]:
    return [(int(round(x * CLIPPER_SCALE)), int(round(y * CLIPPER_SCALE))) for x, y in coords]


def from_int_path(path) -> np.ndarray:
    return np.asarray(path, dtype=float) / CLIPPER_SCALE


def offset_int(paths: list, delta_mm: float, arc_tol_mm: float = 0.05) -> list:
    """Offset (positivo cresce) com junção arredondada, via pyclipper."""
    if abs(delta_mm) < 1e-12:
        return [list(p) for p in paths]
    pco = pyclipper.PyclipperOffset(2.0, max(1.0, arc_tol_mm * CLIPPER_SCALE))
    pco.AddPaths(paths, pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
    return pco.Execute(delta_mm * CLIPPER_SCALE)


def polygon_to_int(poly: Polygon) -> list:
    ext = list(poly.exterior.coords)[:-1]
    path = to_int_path(ext)
    if not pyclipper.Orientation(path):
        path.reverse()
    return path


def int_paths_to_shapely(paths: list) -> Polygon | MultiPolygon:
    """Converte caminhos clipper (externos anti-horários, furos horários) em shapely."""
    outers, holes = [], []
    for p in paths:
        if len(p) < 3:
            continue
        arr = from_int_path(p)
        (outers if pyclipper.Orientation(p) else holes).append(arr)
    polys = []
    for o in outers:
        po = Polygon(o)
        hs = [h for h in holes if po.contains(Polygon(h).representative_point())]
        polys.append(Polygon(o, hs))
    if not polys:
        return Polygon()
    if len(polys) == 1:
        return polys[0]
    return MultiPolygon(polys)


def polygon_area_int(path) -> float:
    return abs(pyclipper.Area(path)) / (CLIPPER_SCALE ** 2)
