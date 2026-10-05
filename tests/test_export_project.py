import collections
import json
import os

import ezdxf
import pytest
from ezdxf import bbox

from app.core.collision import CollisionChecker
from app.core.dxf_export import export_sheets
from app.core.models import NestParams, Placement
from app.core.optimizer import nest
from app.core.part_builder import import_files
from app.core.project import ProjectError, load_project, save_project
from tests.conftest import fx


@pytest.fixture(scope="module")
def nested():
    files = [fx("exemplo_lab.dxf"), fx("spline_elipse.dxf")]
    parts = import_files(files).parts
    params = NestParams()
    res = nest(parts, params, time_limit=10, max_generations=2, workers=0, seed=1)
    return files, parts, params, res


@pytest.mark.parametrize("version", ["R2000", "R12"])
def test_exporta_geometria_original(nested, tmp_path, version):
    files, parts, params, res = nested
    out = export_sheets(parts, res.placements, params, str(tmp_path), "proj", version,
                        combined=True, sheet_outline=True)
    n = res.sheets_used
    assert [os.path.basename(f) for f in out] == \
        [f"proj_placa{i + 1:02d}.dxf" for i in range(n)] + ["proj_todas_placas.dxf"]
    counts = collections.Counter()
    for f in out[:-1]:
        doc = ezdxf.readfile(f)
        if version != "R12":  # R12 não tem campo de unidade
            assert doc.header["$INSUNITS"] == 4
        msp = doc.modelspace()
        counts.update(e.dxftype() for e in msp)
        ext = bbox.extents(e for e in msp if e.dxf.layer != "PLACA")
        assert ext.extmin.x >= params.margin - 0.05 and ext.extmin.y >= params.margin - 0.05
        assert ext.extmax.x <= params.sheet_width - params.margin + 0.05
        assert ext.extmax.y <= params.sheet_height - params.margin + 0.05
        layers = {e.dxf.layer for e in msp}
        assert {"CORTE", "GRAVACAO", "PLACA"} <= layers | {"GRAVACAO"}
        for e in msp:
            if e.dxf.layer == "CORTE":
                assert e.dxf.color == 1
            if e.dxf.layer == "PLACA":
                assert e.dxf.color == 8
    # arcos e círculos continuam arcos e círculos
    assert counts["ARC"] >= 15 and counts["CIRCLE"] >= 10
    if version == "R2000":
        assert counts["LWPOLYLINE"] > 0 and counts["SPLINE"] == 1 and counts["ELLIPSE"] == 2
    else:
        assert counts["POLYLINE"] > 0 and counts["LWPOLYLINE"] == 0 and counts["SPLINE"] == 0


def test_reimportar_exportado_bate_com_encaixe(nested, tmp_path):
    files, parts, params, res = nested
    out = export_sheets(parts, res.placements, params, str(tmp_path), "p", "R2000")
    rep = import_files(out)
    orig = collections.Counter((round(p.area), ) for p in parts for _ in range(p.quantity))
    got = collections.Counter((round(p.area), ) for p in rep.parts for _ in range(p.quantity))
    assert orig == got


def test_ordem_internos_antes_dos_externos(nested, tmp_path):
    files, parts, params, res = nested
    out = export_sheets(parts, res.placements, params, str(tmp_path), "o", "R2000", inner_first=True)
    msp = ezdxf.readfile(out[0]).modelspace()
    max(parts, key=lambda p: p.area)
    # na tampa, os furos (círculo e rasgos) são escritos antes do contorno externo
    types = [e.dxftype() for e in msp]
    first_circle = types.index("CIRCLE")
    assert first_circle < len(types)


def test_projeto_ida_e_volta(nested, tmp_path):
    files, parts, params, res = nested
    parts[0].quantity = parts[0].file_quantity
    path = str(tmp_path / "x.dxfnest")
    res.placements[0].locked = True
    save_project(path, files, params, parts, res)
    proj = load_project(path)
    assert [p.to_json() for p in proj.result.placements] == [p.to_json() for p in res.placements]
    assert proj.params == params
    assert [p.id for p in proj.parts] == [p.id for p in parts]
    assert proj.warnings == []
    res.placements[0].locked = False


def test_projeto_invalido(tmp_path):
    p = tmp_path / "bad.dxfnest"
    p.write_text("{}")
    with pytest.raises(ProjectError):
        load_project(str(p))
    p.write_text("não é json")
    with pytest.raises(ProjectError):
        load_project(str(p))
    p.write_text(json.dumps({"format": "dxfnest", "files": [str(tmp_path / "sumiu.dxf")]}))
    with pytest.raises(ProjectError):
        load_project(str(p))


def test_colisao_manual(nested):
    files, parts, params, res = nested
    cc = CollisionChecker(parts, params)
    assert cc.colliding(res.placements) == set()
    moved = [Placement(**p.to_json()) for p in res.placements]
    moved[1].x, moved[1].y = moved[0].x, moved[0].y
    moved[1].sheet_index = moved[0].sheet_index
    assert {0, 1} <= cc.colliding(moved)
    moved[2].x = -500
    assert 2 in cc.colliding(moved)


def test_cli(tmp_path, capsys):
    from app.cli import main
    rc = main([fx("simples.dxf"), "--placa", "300x200", "--tempo", "2", "--processos", "0",
               "--saida", str(tmp_path), "--versao", "R12"])
    assert rc == 0
    assert os.path.isfile(tmp_path / "simples_placa01.dxf")
    assert main([fx("simples.dxf"), "--placa", "abc"]) == 2
    assert main([fx("corrompido.dxf"), "--saida", str(tmp_path)]) == 1


def test_exporta_variados_r12_e_r2000(tmp_path):
    parts = import_files([fx("variados.dxf")]).parts
    params = NestParams(sheet_width=300, sheet_height=200)
    res = nest(parts, params, time_limit=5, max_generations=1, workers=0)
    for v in ("R12", "R2000"):
        out = export_sheets(parts, res.placements, params, str(tmp_path / v), "v", v)
        types = collections.Counter(e.dxftype() for e in ezdxf.readfile(out[0]).modelspace())
        assert types["ARC"] == 1 and types["CIRCLE"] == 1
        assert (types["MTEXT"] == 1) if v == "R2000" else (types["TEXT"] >= 3)
        rep = import_files(out)
        assert sum(p.quantity for p in rep.parts) == sum(p.quantity for p in parts)
