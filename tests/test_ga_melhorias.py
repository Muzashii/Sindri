"""Genético: mutações de inserção e de inversão, retângulo ocupado por placa e o benchmark determinístico."""
import random

import pytest

from app.core.models import NestParams, NestResult
from app.core.optimizer import GeneticNester, Individual
from app.core.part_builder import import_files
from app.core.placement import occupied_bbox
from app.core.validate import validate_layout
from tests.conftest import fx


def _nester(seed=3, **kw):
    parts = import_files([fx("exemplo_lab.dxf")]).parts
    return parts, GeneticNester(parts, NestParams(population=6, stop_after_seconds=0, **kw), seed=seed, workers=0)


def test_mutacao_continua_uma_permutacao_valida():
    parts, gn = _nester()
    first = gn.first_individual()
    ids = sorted(g[0] for g in first.genes)
    for _ in range(300):
        ind = gn.mutate(first, 0.2)
        assert sorted(g[0] for g in ind.genes) == ids           # nenhuma peça some nem duplica
        for k, rot, _m in ind.genes:
            assert rot in gn.shapes[gn.instances[k][0]].rotations


def test_insercao_leva_uma_peca_para_longe(monkeypatch):
    """Antes só havia troca de vizinhos: uma peça andava uma posição por mutação."""
    parts, gn = _nester()
    monkeypatch.setattr(GeneticNester, "INSERT_RATE", 1.0)
    monkeypatch.setattr(GeneticNester, "INVERT_RATE", 0.0)
    first = gn.first_individual()
    order = [g[0] for g in first.genes]
    far = 0
    for _ in range(50):
        new = [g[0] for g in gn.mutate(first, 0.0).genes]        # taxa 0: só a inserção age
        moved = max(abs(new.index(k) - order.index(k)) for k in order)
        far = max(far, moved)
        rest_old = [k for k in order if abs(new.index(k) - order.index(k)) <= 1]
        assert len(rest_old) >= len(order) - 1                   # só UMA peça mudou de lugar de verdade
    assert far > 5


def test_inversao_inverte_um_trecho(monkeypatch):
    parts, gn = _nester()
    monkeypatch.setattr(GeneticNester, "INSERT_RATE", 0.0)
    monkeypatch.setattr(GeneticNester, "INVERT_RATE", 1.0)
    first = gn.first_individual()
    order = [g[0] for g in first.genes]
    seen_change = False
    for _ in range(30):
        new = [g[0] for g in gn.mutate(first, 0.0).genes]
        diff = [i for i, (a, b) in enumerate(zip(order, new)) if a != b]
        if diff:
            seen_change = True
            i, j = diff[0], diff[-1] + 1
            assert new[i:j] == order[i:j][::-1] and new[:i] == order[:i] and new[j:] == order[j:]
    assert seen_change


def test_taxas_zero_reproduzem_o_comportamento_antigo(monkeypatch):
    """Com as duas mutações novas desligadas, a sequência de números aleatórios é a mesma de antes."""
    parts, gn = _nester(seed=9)
    monkeypatch.setattr(GeneticNester, "INSERT_RATE", 0.0)
    monkeypatch.setattr(GeneticNester, "INVERT_RATE", 0.0)
    first = gn.first_individual()
    got = gn.mutate(first, 0.1)
    # refaz "à mão" a mutação antiga (troca de vizinhos, rotação, critério) com a mesma semente
    rng = random.Random(9)
    genes = list(first.genes)
    n = len(genes)
    for i in range(n):
        if rng.random() < 0.1 and n > 1:
            j = i + 1 if i + 1 < n else i - 1
            genes[i], genes[j] = genes[j], genes[i]
        if rng.random() < 0.1:
            k, r, m = genes[i]
            genes[i] = (k, rng.choice(gn.shapes[gn.instances[k][0]].rotations), m)
    crit = first.criterion
    if rng.random() < 0.1:
        crit = "left" if crit == "bbox" else "bbox"
    assert got == Individual(tuple(genes), crit)


@pytest.mark.parametrize("seed", [1, 2])
def test_encaixe_com_as_mutacoes_novas_e_valido_e_deterministico(seed):
    parts, gn = _nester(seed=seed, sheet_width=300, sheet_height=200)
    res = gn.run(max_generations=4)
    pmap = {p.id: p for p in parts}
    assert validate_layout(pmap, res.placements, gn.params) == []
    parts2, gn2 = _nester(seed=seed, sheet_width=300, sheet_height=200)
    res2 = gn2.run(max_generations=4)
    assert res2.fitness == res.fitness and res2.sheets_used == res.sheets_used
    assert [(p.part_id, p.instance, p.sheet_index, p.x, p.y, p.rotation) for p in res.placements] == \
           [(p.part_id, p.instance, p.sheet_index, p.x, p.y, p.rotation) for p in res2.placements]


def test_retangulo_ocupado_por_placa():
    parts, gn = _nester(sheet_width=300, sheet_height=200)
    res = gn.run(max_generations=1)
    assert len(res.sheet_bbox) == res.sheets_used and len(res.sheet_bbox_area) == res.sheets_used
    real = occupied_bbox({p.id: p for p in parts}, res.placements)
    for si, b in enumerate(res.sheet_bbox):
        r = real[si]
        w, h = b[2] - b[0], b[3] - b[1]
        assert 0 < w <= 300 + 1e-6 and 0 < h <= 200 + 1e-6
        assert res.sheet_bbox_area[si] == pytest.approx(w * h)
        # o retângulo do decodificador contém o das peças (ele inclui a folga do encaixe)
        assert b[0] <= r[0] + 1e-6 and b[1] <= r[1] + 1e-6 and b[2] >= r[2] - 1e-6 and b[3] >= r[3] - 1e-6
        assert (b[2] - b[0]) - (r[2] - r[0]) <= gn.params.spacing + 1.5
    # vai e volta pelo projeto; resultado antigo (sem o campo) continua abrindo
    again = NestResult.from_json(res.to_json())
    assert again.sheet_bbox == [list(b) for b in res.sheet_bbox]
    old = res.to_json()
    old.pop("sheet_bbox")
    assert NestResult.from_json(old).sheet_bbox == [] and NestResult.from_json(old).sheet_bbox_area == []


def test_benchmark_e_deterministico():
    import importlib.util
    import pathlib
    path = pathlib.Path(__file__).resolve().parents[1] / "tools" / "bench_encaixe.py"
    spec = importlib.util.spec_from_file_location("bench_encaixe", path)
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)
    a = bench.run(seeds=(11,), generations=2, only="fx variados")
    b = bench.run(seeds=(11,), generations=2, only="fx variados")
    assert len(a) == 1 and a[0]["invalid"] == 0
    assert (a[0]["fitness_mean"], a[0]["sheets_max"]) == (b[0]["fitness_mean"], b[0]["sheets_max"])
