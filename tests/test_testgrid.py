"""Grade de teste velocidade × potência: cores distintas por combinação, valores por camada e resultado
gravado no banco de materiais."""
import ezdxf
import pytest

from app.core import material_db, testgrid
from app.core.laser import DEFAULT_PALETTE, aci_rgb, nearest_layer


def test_passos():
    assert testgrid.steps(10, 30, 3, 0) == [10, 20, 30]
    assert testgrid.steps(30, 10, 3, 0) == [10, 20, 30]
    assert testgrid.steps(40, 40, 5) == [40]
    assert testgrid.steps(1, 2, 5, 0) == [1, 2]                      # sem repetidos depois de arredondar


def test_cores_distintas_e_numeros_em_camada_propria():
    colors = testgrid.cell_colors()
    layers = [layer for _, layer in colors]
    assert len(layers) == len(set(layers)) >= 10
    assert nearest_layer(aci_rgb(testgrid.LABEL_ACI), DEFAULT_PALETTE) not in layers


def test_grade_12_combinacoes_cabe_num_arquivo(tmp_path):
    plan = testgrid.plan_grid("MDF 3mm", [10, 20, 30], [40, 50, 60, 80], "corte")
    assert len(plan.sheets) == 1 and len(plan.cells) == 12
    s = plan.sheets[0]
    assert len({c.rd_layer for c in s.cells}) == 12
    c = next(c for c in s.cells if c.speed == 20 and c.power_max == 50)
    assert c.power_min == pytest.approx(32.5)                       # corte: mínima = 65% da máxima
    vals = testgrid.sheet_values(s)
    assert vals[c.rd_layer] == (20, 32.5, 50) and vals[s.label_layer] == (300.0, 12.0, 12.0)
    assert s.width < 600 and s.height < 400                          # cabe numa chapa comum
    [path] = testgrid.write_plan(plan, str(tmp_path))
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    squares = [e for e in msp.query("LWPOLYLINE") if e.dxf.layer.startswith("GRADE_")]
    assert len(squares) == 12 and all(e.closed for e in squares)
    assert len({e.dxf.color for e in squares}) == 12
    labels = msp.query('LWPOLYLINE[layer=="NUMEROS"]')
    assert len(labels) > 20 and {e.dxf.color for e in labels} == {testgrid.LABEL_ACI}
    first = list(msp)[0]
    assert first.dxf.layer == "NUMEROS"                              # números antes dos cortes
    assert doc.header["$INSUNITS"] == 4


def test_grade_maior_vira_varios_arquivos(tmp_path):
    plan = testgrid.plan_grid("Acrílico 3mm", testgrid.steps(5, 25, 5, 0), testgrid.steps(30, 90, 7, 0), "corte")
    cap = len(testgrid.cell_colors())
    assert len(plan.cells) == 35 and len(plan.sheets) > 1
    assert all(len(s.cells) <= cap for s in plan.sheets)
    assert all(len({c.rd_layer for c in s.cells}) == len(s.cells) for s in plan.sheets)
    combos = {(c.speed, c.power_max) for c in plan.cells}
    assert len(combos) == 35
    paths = testgrid.write_plan(plan, str(tmp_path))
    assert len(paths) == len(plan.sheets) and all("de" in p for p in paths)
    wide = testgrid.plan_grid("X", [10], list(range(5, 100, 5)), "corte")          # 19 potências: divide colunas
    assert all(len(s.cells) <= cap for s in wide.sheets) and len(wide.cells) == 19


def test_gravacao_preenche_os_quadrados(tmp_path):
    plan = testgrid.plan_grid("MDF 3mm", [200, 300], [10, 20], "gravacao", size=10, interval=0.5)
    s = plan.sheets[0]
    assert all(c.power_min == c.power_max for c in s.cells)         # gravação: mínima = máxima
    g = testgrid.sheet_geometry(s, plan)
    assert len(g["hatch"]) == 4 * 19
    [path] = testgrid.write_plan(plan, str(tmp_path))
    assert len(ezdxf.readfile(path).modelspace().query("LINE")) == 76


def test_entradas_invalidas():
    with pytest.raises(ValueError):
        testgrid.plan_grid("X", [], [10])
    with pytest.raises(ValueError):
        testgrid.plan_grid("X", [10], [150])
    with pytest.raises(ValueError):
        testgrid.plan_grid("X", [10], [10], "outro")


def test_resultado_vai_para_o_banco(tmp_path):
    db = material_db.MaterialDB(str(tmp_path / "materiais.json"))
    plan = testgrid.plan_grid("MDF 3mm", [10, 20], [40, 60])
    cell = next(c for c in plan.cells if c.speed == 20 and c.power_max == 60)
    m = testgrid.record_result(db, "MDF 3mm", cell, "corte", "Léo", today="2026-11-11")
    assert m.ops["corte"]["speed"] == 20 and m.ops["corte"]["power"] == 60 and m.ops["corte"]["power_min"] == 39
    assert m.tested == "2026-11-11" and m.validated_by == "Léo" and not m.is_stale(
        today=__import__("datetime").date(2026, 11, 20))
    g = testgrid.plan_grid("Novo", [300], [15], "gravacao")
    m2 = testgrid.record_result(db, "Novo", g.cells[0], "gravacao")
    assert m2.ops["gravacao_vetorial"]["speed"] == 300 and db.find("novo") is m2


def test_dialogo_gera_abre_e_grava(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QMessageBox
    from app.ui.main_window import MainWindow
    from app.ui.testgrid_dialog import TestGridDialog
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    w = MainWindow(workers=0)
    try:
        opened = []
        monkeypatch.setattr(w, "_open_in_rdworks", lambda files, groups=None, values=None: opened.append((files, values)) or "")
        dlg = TestGridDialog(w, "MDF 3mm")
        dlg.v_n.setValue(5)
        dlg.p_n.setValue(7)
        assert len(dlg.plan.sheets) > 1 and "arquivos" in dlg.summary.text()
        dlg.generate()
        assert len(opened) == 1 and dlg.b_next.isVisibleTo(dlg)
        files, values = opened[0]
        assert files[0].endswith(".dxf") and len(values) == len(dlg.plan.sheets[0].cells) + 1
        dlg.open_next()
        assert len(opened) == 2
        dlg.pick_v.setCurrentIndex(1)
        dlg.pick_p.setCurrentIndex(2)
        dlg.who.setText("Léo")
        dlg.save_result()
        m = w.material_db().find("MDF 3mm")
        assert m.ops["corte"]["speed"] == dlg.pick_v.currentData() and m.validated_by == "Léo"
        assert "Gravado" in dlg.result.text()
        dlg.close()
    finally:
        w.close()
