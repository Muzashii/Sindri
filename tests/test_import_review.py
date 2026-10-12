"""Conferência da importação: avisos com local, aberturas fecháveis e reparos por arquivo."""
import os

import ezdxf
import pytest

from app.core import import_issues as ii
from app.core.models import NestParams
from app.core.part_builder import import_files
from app.core.project import load_project, save_project
from tests.conftest import fx


def _gappy(path, units=4, gap=0.3):
    """Retângulo 100×50 de linhas soltas com uma folga de ``gap`` num canto, mais um L com duas
    folgas (0,4 e 1,2) e uma linha solta longe de tudo (ponta sem par)."""
    doc = ezdxf.new("R2000")
    doc.header["$INSUNITS"] = units
    m = doc.modelspace()
    m.add_line((0, 0), (100, 0), dxfattribs={"layer": "CORTE", "color": 1})
    m.add_line((100, 0), (100, 50), dxfattribs={"layer": "CORTE", "color": 1})
    m.add_line((100, 50), (0, 50), dxfattribs={"layer": "CORTE", "color": 1})
    m.add_line((0, 50), (0, gap), dxfattribs={"layer": "CORTE", "color": 1})
    # L em 200..260
    m.add_line((200, 0), (260, 0))
    m.add_line((260, 0.4), (260, 20))
    m.add_line((260, 20), (220, 20))
    m.add_line((220, 20), (220, 60))
    m.add_line((220, 60), (200, 60))
    m.add_line((200, 60), (200, 1.2))
    m.add_line((400, 0), (450, 30))
    doc.saveas(path)
    return path


def _open_issue(rep):
    return next(i for i in rep.issues if i.kind == "open")


def test_avisos_tem_arquivo_gravidade_e_local():
    rep = import_files([fx("duplicadas.dxf"), fx("contorno_aberto.dxf"), fx("variados.dxf"),
                        fx("corrompido.dxf")])
    kinds = {(os.path.basename(i.file), i.kind): i for i in rep.issues}
    dup = kinds[("duplicadas.dxf", "duplicate_entities")]
    assert dup.severity == ii.FIXED and dup.count == len(dup.where) > 0
    op = kinds[("contorno_aberto.dxf", "open")]
    assert op.severity == ii.CHECK and op.where
    # a folga de ~41 mm do arquivo de exemplo é um trecho faltando, não uma abertura para ligar
    assert op.gaps == [] and len(op.loose_ends) == 2
    assert kinds[("contorno_aberto.dxf", "loose_mark")].severity == ii.CHECK
    assert kinds[("variados.dxf", "ignored")].severity == ii.FIXED
    assert kinds[("corrompido.dxf", "read_error")].severity == ii.ERROR
    # o texto continua igual para a linha de comando e para quem já lia os avisos
    assert any("duplicada" in w for w in rep.warnings)
    frames = rep.extra["frames"]
    assert set(frames) == {os.path.abspath(f) for f in rep.files}


def test_aberturas_pareadas_pela_menor_distancia(tmp_path):
    rep = import_files([_gappy(str(tmp_path / "a.dxf"))])
    op = _open_issue(rep)
    assert op.count == 3
    dists = sorted(round(g.dist, 2) for g in op.gaps)
    assert dists == [0.3, 0.4, 1.2]
    assert len(op.loose_ends) == 2                     # a linha solta não tem par perto
    g = min(op.gaps, key=lambda g: g.dist)
    assert g.layer == "CORTE" and g.color == 1          # o trecho novo sai na mesma cor/camada
    assert [round(g.dist, 2) for g in op.fixable_gaps(0.5)] == [0.3, 0.4]


def test_fechar_aberturas_vira_peca_fechada(tmp_path):
    path = _gappy(str(tmp_path / "a.dxf"))
    rep = import_files([path])
    gaps = _open_issue(rep).fixable_gaps(2.0)
    repairs = {os.path.abspath(path): [ii.gap_to_repair(g, 1.0) for g in gaps]}
    fixed = import_files([path], file_repairs=repairs)
    closed = [p for p in fixed.parts if not p.is_open]
    assert {(round(p.size[0]), round(p.size[1])) for p in closed} == {(100, 50), (60, 60)}
    assert abs(next(p for p in closed if round(p.size[0]) == 100).area - 5000) < 20
    op = _open_issue(fixed)
    assert op.count == 1 and op.gaps == []             # sobra só a linha solta
    rp = next(i for i in fixed.issues if i.kind == "repaired")
    assert rp.severity == ii.FIXED and rp.count == 3
    # o trecho novo é exportado como LINE comum, marcado como reparo
    rect = next(p for p in closed if round(p.size[0]) == 100)
    assert sum(1 for pr in rect.prims if pr.data.get("repair")) == 1


def test_reparo_acompanha_a_troca_de_unidade(tmp_path):
    path = _gappy(str(tmp_path / "cm.dxf"), units=5, gap=0.03)   # mesmo desenho, em cm
    rep = import_files([path])
    g = min(_open_issue(rep).gaps, key=lambda g: g.dist)
    assert abs(g.dist - 0.3) < 1e-6                     # 0,03 cm = 0,3 mm
    repairs = {os.path.abspath(path): [ii.gap_to_repair(g, 10.0)]}
    in_mm = import_files([path], file_repairs=repairs, file_units={os.path.abspath(path): 4})
    rect = max(in_mm.parts, key=lambda p: p.area)
    assert not rect.is_open and round(rect.size[0]) == 100   # lido em mm, o reparo continua no lugar


def test_reparo_invalido_e_ignorado():
    assert ii.repair_prims([{"a": [0, 0], "b": [0, 0]}, {"a": ["x"], "b": [1, 1]}, {}], 1.0) == []


def test_projeto_guarda_reparos_e_avisos(tmp_path):
    path = _gappy(str(tmp_path / "a.dxf"))
    rep = import_files([path])
    repairs = {os.path.abspath(path): [ii.gap_to_repair(g, 1.0) for g in _open_issue(rep).fixable_gaps(0.5)]}
    rep = import_files([path], file_repairs=repairs)
    proj_path = str(tmp_path / "p.sindri")
    save_project(proj_path, rep.files, NestParams(), rep.parts, None, report=rep, file_repairs=repairs)
    proj = load_project(proj_path)
    assert proj.repairs == repairs
    assert [i.kind for i in proj.report.issues] == [i.kind for i in rep.issues]
    assert proj.report.extra["frames"].keys() == rep.extra["frames"].keys()
    os.remove(path)                                     # origem sumiu: usa o desenho incorporado
    proj = load_project(proj_path)
    assert any(i.kind == "repaired" for i in proj.report.issues)


def test_chave_estavel_e_resumo(tmp_path):
    path = _gappy(str(tmp_path / "a.dxf"))
    a = _open_issue(import_files([path]))
    b = _open_issue(import_files([path]))
    assert a.key == b.key
    s = ii.summarize(import_files([path, fx("duplicadas.dxf")]).issues)
    assert s[ii.CHECK] == 1 and s[ii.FIXED] == 1 and s["files"] == 1


def test_recado_para_o_aluno(tmp_path):
    rep = import_files([_gappy(str(tmp_path / "a.dxf")), fx("duplicadas.dxf")])
    note = ii.student_note(rep.issues)
    assert "a.dxf" in note and "não fecham" in note and "1,2 mm" in note
    assert "duplicadas.dxf" not in note                 # o que já foi resolvido não vira cobrança
    assert ii.student_note([]) == ""


@pytest.mark.parametrize("v,txt", [(0.3, "0,3 mm"), (1.25, "1,25 mm"), (12.4, "12 mm"), (2.0, "2 mm")])
def test_formato_mm(v, txt):
    assert ii.fmt_mm(v) == txt


def test_grade_de_visualizacao_nao_sobrepoe():
    from app.core.part_builder import _preview_grid
    boxes = [(0, 0, 100, 50), (-20, 10, 30, 90), (5, 5, 400, 30), (0, 0, 10, 10), (0, 0, 60, 60)]
    offs = _preview_grid(boxes)
    placed = [(x0 + dx, y0 + dy, x1 + dx, y1 + dy) for (x0, y0, x1, y1), (dx, dy) in zip(boxes, offs, strict=True)]
    for i, a in enumerate(placed):
        for b in placed[i + 1:]:
            assert a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]
    assert placed[0][3] == pytest.approx(0)              # 1º desenho no topo, à esquerda
    assert _preview_grid([]) == []
