import time

import ezdxf
import pytest

from app.core.models import NestParams, Placement
from app.core.optimizer import GeneticNester, nest, shapes_from_parts
from app.core.part_builder import import_files
from app.core.placement import Decoder
from app.core.validate import validate_layout
from tests.conftest import fx

CASES = [
    ("exemplo_lab.dxf", NestParams()),
    ("exemplo_lab.dxf", NestParams(rotation_steps=8, allow_mirror=True, spacing=0.5, margin=0)),
    ("spline_elipse.dxf", NestParams(sheet_width=200, sheet_height=150, spacing=3)),
    ("blocos.dxf", NestParams(sheet_width=120, sheet_height=100)),
    ("furos.dxf", NestParams(sheet_width=200, sheet_height=150, free_rotation=True)),
]


@pytest.mark.parametrize("name,params", CASES)
def test_sem_sobreposicao_e_dentro_da_placa(name, params):
    parts = import_files([fx(name)]).parts
    gn = GeneticNester(parts, params, seed=5, workers=0)
    pmap = {p.id: p for p in parts}
    for k in range(6):
        ind = gn.first_individual() if k == 0 else gn.mutate(gn.first_individual(), 0.4)
        res = gn.evaluate_local(ind)
        assert validate_layout(pmap, res.placements, params) == []
        assert len(res.placements) == sum(p.quantity for p in parts) - len(res.unplaced)


def test_primeira_solucao_rapida_50_pecas(lab_parts):
    # 35 peças do arquivo de exemplo + mais 15 = 50 peças
    parts = [p for p in lab_parts]
    for p in parts:
        p.quantity = p.file_quantity
    parts[0].quantity += 2
    parts[2].quantity += 7
    parts[3].quantity += 6
    assert sum(p.quantity for p in parts) == 50
    t = time.time()
    gn = GeneticNester(parts, NestParams(), workers=0)
    gn.evaluate_local(gn.first_individual())
    assert time.time() - t < 2.0 * 2.5   # 2 s numa máquina normal; folga para CI lento
    for p in parts:
        p.quantity = p.file_quantity


def _pip_doc(path):
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 4
    m = doc.modelspace()
    m.add_lwpolyline([(0, 0), (188, 0), (188, 138), (0, 138)], close=True)
    m.add_lwpolyline([(20, 20), (168, 20), (168, 118), (20, 118)], close=True)
    m.add_circle((400, 50), 20)
    doc.saveas(path)


def test_part_in_part(tmp_path):
    p = str(tmp_path / "pip.dxf")
    _pip_doc(p)
    parts = import_files([p]).parts
    params = NestParams(sheet_width=200, sheet_height=150, multi_sheet=False)
    res = nest(parts, params, time_limit=5, max_generations=1, workers=0)
    assert res.sheets_used == 1 and not res.unplaced
    frame = [pl for pl in res.placements if pl.part_id == parts[0].id][0]
    disc = [pl for pl in res.placements if pl.part_id == parts[1].id][0]
    assert abs(disc.x - frame.x) < 60 and abs(disc.y - frame.y) < 40
    assert validate_layout({q.id: q for q in parts}, res.placements, params) == []
    # sem part-in-part o disco não cabe (placa única)
    params2 = NestParams(sheet_width=200, sheet_height=150, multi_sheet=False, part_in_part=False)
    res2 = nest(parts, params2, time_limit=5, max_generations=1, workers=0)
    assert len(res2.unplaced) == 1


def test_peca_maior_que_a_placa(lab_parts):
    params = NestParams(sheet_width=200, sheet_height=120)
    gn = GeneticNester(lab_parts, params, workers=0)
    big = {pid for pid, _ in gn.too_big}
    assert any(round(p.size[0]) == 300 for p in lab_parts if p.id in big)
    res = gn.run(max_generations=1)
    assert len(res.unplaced) >= 6
    assert validate_layout({p.id: p for p in lab_parts}, res.placements, params) == []


def test_pecas_travadas_ficam_no_lugar(lab_parts):
    params = NestParams()
    lid = max(lab_parts, key=lambda p: p.area)
    lock = Placement(lid.id, 0, 0, 400.0, 250.0, 90.0, False, True)
    res = nest(lab_parts, params, time_limit=10, max_generations=1, workers=0, locked=[lock])
    got = [pl for pl in res.placements if pl.part_id == lid.id and pl.instance == 0]
    assert got == [lock]
    assert validate_layout({p.id: p for p in lab_parts}, res.placements, params) == []


def test_ga_nunca_piora(lab_parts):
    params = NestParams(population=6)
    seen = []
    gn = GeneticNester(lab_parts, params, seed=2, workers=0)
    gn.run(on_best=lambda r: seen.append(r.fitness), max_generations=3)
    assert seen == sorted(seen, reverse=True)
    assert gn.generation == 3


def test_ga_paralelo_e_parada(lab_parts):
    import threading
    params = NestParams(population=4)
    stop = threading.Event()
    gn = GeneticNester(lab_parts, params, seed=1, workers=2)
    progress = []

    def on_prog(info):
        progress.append(info)
        if info["generation"] >= 1:
            stop.set()

    res = gn.run(on_progress=on_prog, stop_event=stop, time_limit=120)
    assert res is not None and progress[-1]["finished"]
    assert validate_layout({p.id: p for p in lab_parts}, res.placements, params) == []


def test_parada_por_geracoes_sem_melhora(lab_parts):
    params = NestParams(population=4, max_generations_without_improvement=2)
    gn = GeneticNester(lab_parts, params, seed=1, workers=0)
    gn.run(time_limit=120)
    assert gn.generation >= 2


def test_quantidade_zero():
    parts = import_files([fx("simples.dxf")]).parts
    for p in parts:
        p.quantity = 0
    res = nest(parts, NestParams(), time_limit=1, workers=0)
    assert res.placements == [] and res.sheets_used == 0


def test_detalhe_rapido_fecha_dentes_e_continua_valido(tmp_path):
    """Peças com dentes (finger joint): o modo equilibrado reduz vértices e o encaixe segue sem colisão."""
    import math
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 4
    m = doc.modelspace()
    for k in range(3):
        pts = []
        x0, y0, w, h, t, f = 0, k * 200, 300, 150, 3, 15
        for i in range(int(w // f)):
            d = t if i % 2 else 0
            pts += [(x0 + i * f, y0 + d), (x0 + (i + 1) * f, y0 + d)]
        pts += [(x0 + w, y0 + h), (x0, y0 + h)]
        m.add_lwpolyline(pts, close=True)
    p = str(tmp_path / "dentes.dxf")
    doc.saveas(p)
    parts = import_files([p]).parts
    from app.core.nfp import NFPCache
    shapes = shapes_from_parts(parts, NestParams())
    fino = NFPCache(shapes, 0, 0.1, detail=0).variant(parts[0].id, 0)
    rapido = NFPCache(shapes, 0, 0.1, detail=1).variant(parts[0].id, 0)
    assert len(rapido.path) < len(fino.path) / 4
    for detail in (0, 1, 2):
        params = NestParams(sheet_width=400, sheet_height=300, spacing=0.5, detail=detail)
        res = nest(parts, params, time_limit=10, max_generations=1, workers=0)
        assert validate_layout({q.id: q for q in parts}, res.placements, params) == []
