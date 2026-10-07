import random

import numpy as np
import pyclipper
import pytest
from shapely.geometry import Polygon

from app.core.geometry import from_int_path
from app.core.nfp import NFPCache, PartShape


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


def test_contorno_com_muitos_vertices_fica_leve_e_seguro():
    """Engrenagem de 300 dentes miúdos no modo Preciso: poucos vértices no NFP, sem cortar a peça."""
    import math
    from app.core.nfp import MAX_OUTLINE_VERTICES
    pts = []
    for i in range(300):                       # 300 dentes retos: 4 vértices cada
        for frac, r in ((0.0, 98.5), (0.2, 100), (0.6, 100), (0.8, 98.5)):
            a = 2 * math.pi * (i + frac) / 300
            pts.append((r * math.cos(a), r * math.sin(a)))
    shapes = {"G": _shape("G", pts)}
    cache = NFPCache(shapes, spacing=0.0, curve_tol=0.1, part_in_part=False, detail=0)
    v = cache.variant("G", 0)
    assert len(v.path) <= MAX_OUTLINE_VERTICES * 2       # o offset em quina viva pode duplicar alguns
    outline = Polygon(from_int_path(v.path))
    assert outline.buffer(1e-6).covers(Polygon(shapes["G"].outer))


def test_furo_falso_do_nfp_e_descartado_e_o_verdadeiro_fica():
    """Bolsão real (B cabe dentro do recorte de A) continua; fenda numérica que poria B em cima de A sai."""
    # A: quadrado 100 com um bolsão 40×40 ligado ao lado de fora por uma fenda de 5 mm
    A = [(0, 0), (100, 0), (100, 100), (52.5, 100), (52.5, 70), (70, 70), (70, 30), (30, 30), (30, 70),
         (47.5, 70), (47.5, 100), (0, 100)]
    B = [(0, 0), (30, 0), (30, 30), (0, 30)]
    shapes = {"A": _shape("A", A), "B": _shape("B", B)}
    cache = NFPCache(shapes, spacing=0.0, curve_tol=0.01, part_in_part=False, detail=0)
    a, b = cache.variant("A", 0), cache.variant("B", 0)
    res = cache.nfp(a, b)
    holes = [p for p in res if not pyclipper.Orientation(p)]
    assert holes, "o bolsão onde B cabe precisa continuar como posição possível"
    # furo inventado no meio da parte maciça de A: precisa ser descartado
    outer = [p for p in res if pyclipper.Orientation(p)]
    s = 10 ** 4
    fake = [(int(-30 * s), int(-30 * s)), (int(-30 * s), int(-29 * s)), (int(-29 * s), int(-29 * s)),
            (int(-29 * s), int(-30 * s))]
    from app.core.geometry import CLIPPER_SCALE
    k = CLIPPER_SCALE / s
    fake = [(int(x * k), int(y * k)) for x, y in fake]
    if pyclipper.Orientation(fake):
        fake = fake[::-1]
    kept = NFPCache._real_holes(outer + holes + [fake], a, b)
    assert fake not in kept and all(h in kept for h in holes)
