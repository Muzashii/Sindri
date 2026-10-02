import random

import numpy as np
import pyclipper
import pytest
from shapely.geometry import Polygon, Point

from app.core.geometry import from_int_path
from app.core.models import NestParams, Placement
from app.core.nfp import NFPCache, PartShape
from app.core.optimizer import shapes_from_parts
from app.core.part_builder import import_files
from app.core.placement import Decoder
from app.core.validate import validate_layout
from tests.conftest import fx


def _shape(pid, coords, holes=(), rots=(0.0,)):
    arr = np.asarray(coords, float)
    arr = arr - Polygon(arr).centroid.coords[0]
    return PartShape(pid, arr, [np.asarray(h, float) for h in holes], Polygon(arr).area, list(rots))


L = [(0, 0), (60, 0), (60, 15), (15, 15), (15, 50), (0, 50)]
SQ = [(0, 0), (20, 0), (20, 20), (0, 20)]
BIG = [(0, 0), (120, 0), (120, 90), (0, 90)]


@pytest.mark.parametrize("a,b", [(L, SQ), (SQ, L), (L, L), (BIG, SQ), (SQ, BIG)])
def test_nfp_separa_colisao_de_nao_colisao(a, b):
    shapes = {"A": _shape("A", a, rots=[0, 90]), "B": _shape("B", b, rots=[0, 90])}
    cache = NFPCache(shapes, spacing=0.0, curve_tol=0.01, part_in_part=False)
    rnd = random.Random(3)
    for ra in (0, 90):
        for rb in (0, 90):
            va, vb = cache.variant("A", ra), cache.variant("B", rb)
            nfp = cache.nfp(va, vb)
            pa = Polygon(from_int_path(va.path))
            pb = Polygon(from_int_path(vb.path))
            region = None
            for _ in range(300):
                x, y = rnd.uniform(-150, 150), rnd.uniform(-150, 150)
                pt = (int(x * 1000), int(y * 1000))
                inside = sum(1 if pyclipper.Orientation(p) else -1
                             for p in nfp if pyclipper.PointInPolygon(pt, p) == 1) > 0
                moved = Polygon(np.asarray(pb.exterior.coords) + [x, y])
                overlap = pa.intersection(moved).area > 1e-3
                assert inside == overlap, (ra, rb, x, y)


def test_nfp_cache_reutiliza():
    shapes = {"A": _shape("A", L), "B": _shape("B", SQ)}
    c = NFPCache(shapes, 2.0, 0.1)
    va, vb = c.variant("A", 0), c.variant("B", 0)
    c.nfp(va, vb)
    c.nfp(va, vb)
    assert c.stats()["hits"] == 1 and c.stats()["misses"] == 1


def test_ifp_placa_menor_que_peca():
    shapes = {"B": _shape("B", BIG)}
    c = NFPCache(shapes, 0, 0.01)
    assert c.ifp_rect((0, 0, 50000, 50000), c.variant("B", 0)) is None


def test_hole_ifp_peca_cabe_no_furo():
    frame = [(0, 0), (200, 0), (200, 200), (0, 200)]
    hole = [(40, 40), (160, 40), (160, 160), (40, 160)]
    c0 = Polygon(frame).centroid
    shapes = {"F": _shape("F", frame, holes=[np.asarray(hole) - [c0.x, c0.y]]),
              "S": _shape("S", SQ)}
    c = NFPCache(shapes, 2.0, 0.05)
    vf, vs = c.variant("F", 0), c.variant("S", 0)
    regs = c.hole_ifp(vf, 0, vs)
    assert regs
    # qualquer vértice da região deixa o quadrado inteiro dentro do furo
    hp = Polygon(from_int_path(vf.holes[0])).buffer(1e-3)
    sq = Polygon(from_int_path(vs.path))
    for r in regs:
        for x, y in from_int_path(r):
            assert hp.covers(Polygon(np.asarray(sq.exterior.coords) + [x, y]))
