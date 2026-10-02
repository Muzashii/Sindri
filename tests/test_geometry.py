import math
import random

import numpy as np
import pytest
from shapely.geometry import LineString

from app.core.geometry import (Transform, aci_to_rgb, flatten_prim, int_paths_to_shapely,
                               offset_int, prim_endpoints, prim_is_closed, ring_to_polygon,
                               to_int_path, transform_prim)
from app.core.models import NestParams, Placement, Prim, NestResult

PRIMS = [
    Prim("LINE", {"s": [1, 2], "e": [30, 7]}),
    Prim("CIRCLE", {"c": [5, 5], "r": 12}),
    Prim("ARC", {"c": [3, -2], "r": 20, "a0": 350, "a1": 80}),
    Prim("POLY", {"pts": [[0, 0, 0.4], [20, 0, 0], [20, 10, -0.7], [0, 10, 0]], "closed": True}),
    Prim("POLY", {"pts": [[0, 0, 0], [20, 5, 1.0], [40, 0, 0]], "closed": False}),
    Prim("ELLIPSE", {"c": [10, 4], "major": [15, 5], "ratio": 0.4, "t0": 0.3, "t1": 4.0}),
    Prim("SPLINE", {"degree": 3, "ctrl": [[0, 0], [10, 20], [30, 25], [40, 0], [55, 10]],
                    "knots": [], "weights": [], "fit": [], "closed": False}),
    Prim("SPLINE", {"degree": 3, "ctrl": [], "knots": [], "weights": [],
                    "fit": [[0, 0], [10, 8], [20, 0], [30, 5]], "closed": False}),
]


@pytest.mark.parametrize("prim", PRIMS, ids=[p.kind for p in PRIMS])
@pytest.mark.parametrize("mirror", [False, True])
def test_transformar_primitiva_igual_a_transformar_pontos(prim, mirror):
    """A geometria exportada (prim transformada) coincide com a usada no encaixe."""
    rnd = random.Random(7)
    for _ in range(5):
        tf = Transform(rnd.uniform(0, 360), mirror, rnd.uniform(-100, 100), rnd.uniform(-100, 100),
                       pre=(rnd.uniform(-5, 5), rnd.uniform(-5, 5)))
        a = LineString(tf.pts_array(flatten_prim(prim, 0.01)))
        b = LineString(flatten_prim(transform_prim(prim, tf), 0.01))
        assert a.hausdorff_distance(b) < 0.05


def test_texto_transformado():
    t = Prim("TEXT", {"text": "OI", "p": [0, 0], "h": 5, "rot": 10,
                      "bbox": [[0, 0], [7, 0], [7, 6], [0, 6]]})
    t2 = transform_prim(t, Transform(90, False, 10, 0))
    assert t2.data["rot"] == pytest.approx(100)
    assert t2.data["p"] == pytest.approx([10, 0])
    t3 = transform_prim(t, Transform(0, True))
    assert t3.data["mirror"] is True


def test_fechamento_e_extremidades():
    assert prim_is_closed(PRIMS[1], 0.05)
    assert prim_is_closed(PRIMS[3], 0.05)
    assert not prim_is_closed(PRIMS[0], 0.05)
    e = Prim("ELLIPSE", {"c": [0, 0], "major": [10, 0], "ratio": 0.5, "t0": 0, "t1": 2 * math.pi})
    assert prim_is_closed(e, 0.05)
    a, b = prim_endpoints(PRIMS[2])
    assert np.hypot(*(a - [3 + 20 * math.cos(math.radians(350)), -2 + 20 * math.sin(math.radians(350))])) < 1e-9
    a, b = prim_endpoints(PRIMS[6])
    assert np.allclose(a, [0, 0]) and np.allclose(b, [55, 10])


def test_discretizacao_respeita_tolerancia():
    c = Prim("CIRCLE", {"c": [0, 0], "r": 50})
    for tol in (0.5, 0.1, 0.01):
        pts = flatten_prim(c, tol)
        mid = (pts[:-1] + pts[1:]) / 2
        sag = 50 - np.hypot(mid[:, 0], mid[:, 1])
        assert sag.max() <= tol + 1e-9


def test_poligono_auto_intersectante_corrigido():
    bow = np.array([[0, 0], [10, 10], [10, 0], [0, 10]])
    p = ring_to_polygon(bow)
    assert p is not None and p.is_valid and p.area > 0
    assert ring_to_polygon(np.array([[0, 0], [1, 1]])) is None


def test_offset_e_conversao():
    sq = to_int_path([(0, 0), (10, 0), (10, 10), (0, 10)])
    g = int_paths_to_shapely(offset_int([sq], 1.0))
    # arcos do offset são cordas internas: área um pouco menor que a exata
    assert 142.5 < g.area <= 100 + 40 + math.pi
    assert int_paths_to_shapely([]).is_empty


def test_cores():
    assert aci_to_rgb(1) == (255, 0, 0)
    assert aci_to_rgb(7) == (0, 0, 0)


def test_modelos_json():
    p = NestParams(sheet_width=123, free_rotation=True)
    assert len(p.rotations()) == 24
    assert NestParams.from_json(p.to_json()).sheet_width == 123
    pl = Placement("P1", 0, 1, 2.5, 3.5, 90, True, True)
    assert Placement.from_json(pl.to_json()) == pl
    r = NestResult([pl], 1, 0.5, 1.0, [("P2", 1)])
    r2 = NestResult.from_json(r.to_json())
    assert r2.placements == [pl] and r2.unplaced == [("P2", 1)]
    pr = PRIMS[0]
    assert Prim.from_json(pr.to_json()) == pr
