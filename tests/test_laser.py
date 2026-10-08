import struct

import ezdxf

from app.core import laser
from app.core.models import NestParams, Part, Placement, Prim
from shapely.geometry import box

PALETTE = [(0, 0, 0), (0, 0, 255), (255, 0, 0), (0, 255, 0), (250, 128, 114), (255, 255, 0)]


def fake_config(stride=300, tables=2):
    """Imita o 'config' do RDWorks: cabeçalho + tabelas com um registro por cor."""
    out = bytearray(b"\x07\x00\xc8\x00\x00\x00\x0fRDFILEVER8.0.01" + b"\x11" * 50)
    for t in range(tables):
        for rgb in PALETTE:
            rec = bytearray(b"\x22" * 20) + bytes(rgb) + b"\x00" * 5
            rec += struct.pack("<5d", 100.0, 30.0, 30.0, 30.0, 30.0)
            rec += b"\x33" * (stride - len(rec))
            out += rec
        out += b"\x44" * 37
    return bytes(out)


def test_tabelas_paleta_e_gravacao(tmp_path):
    data = fake_config()
    tables = laser.find_tables(data)
    assert len(tables) == 2 and all(t.count == len(PALETTE) for t in tables)
    assert laser.palette_of(data, tables[0]) == PALETTE
    new = laser.patch(data, {0: (16, 85), 2: (300, 12)})
    for t in laser.find_tables(new):
        assert laser.read_layer(new, t, 0) == (16, 85, 85)
        assert laser.read_layer(new, t, 2) == (300, 12, 12)
        assert laser.read_layer(new, t, 1) == (100, 30, 30)
    assert len(new) == len(data) and sum(a != b for a, b in zip(data, new)) <= 2 * 2 * 40
    cfg = tmp_path / "config"
    cfg.write_bytes(data)
    laser.apply_to_config(str(cfg), {0: (20, 70)})
    assert (tmp_path / laser.BACKUP_NAME).read_bytes() == data       # cópia do original
    t = laser.find_tables(cfg.read_bytes())[0]
    assert laser.read_layer(cfg.read_bytes(), t, 0) == (20, 70, 70)


def test_arquivo_desconhecido_nao_e_alterado():
    import pytest
    with pytest.raises(ValueError):
        laser.patch(b"\x00" * 5000, {0: (10, 10)})


def _part(pid, material, color, w=50):
    h = w / 2
    prims = [Prim("POLY", {"pts": [(-h, -15), (h, -15), (h, 15), (-h, 15)], "closed": True}, "CORTE", color)]
    return Part(pid, pid, "a.dxf", box(-h, -15, h, 15), [], prims, [0], material=material)


def test_materiais_diferentes_viram_camadas_diferentes(tmp_path):
    parts = [_part("P1", "MDF 3mm", 7), _part("P2", "MDF 6mm", 7), _part("P3", "MDF 6mm", 1)]
    groups = laser.plan_layers(laser.groups_from_parts(parts), PALETTE, ["MDF 3mm", "MDF 6mm"])
    by = {(g.material, g.aci): g for g in groups}
    assert by[("MDF 3mm", 7)].rd_index == 0 and by[("MDF 3mm", 7)].target_aci == 7
    assert by[("MDF 6mm", 1)].rd_index == 2                              # vermelho continua vermelho
    moved = by[("MDF 6mm", 7)]
    assert moved.rd_index not in (0, 2) and moved.target_aci != 7         # preto do 6mm vai p/ outra cor
    assert len({g.rd_index for g in groups}) == 3
    from app.core.dxf_export import export_all_sheets
    p = NestParams(sheet_width=300, sheet_height=200)
    pls = [Placement("P1", 0, 0, 30, 30, 0), Placement("P2", 0, 1, 30, 30, 0), Placement("P3", 0, 1, 30, 120, 0)]
    path = export_all_sheets(parts, pls, p, str(tmp_path), "t", color_map=laser.color_map(groups))
    colors = sorted(e.dxf.color for e in ezdxf.readfile(path).modelspace() if e.dxf.layer == "CORTE")
    assert colors == sorted([7, moved.target_aci, 1])


def test_ajudante_grava_config(tmp_path):
    import json
    cfg = tmp_path / "config"
    cfg.write_bytes(fake_config())
    job = tmp_path / "job.json"
    job.write_text(json.dumps({"config": str(cfg), "values": {"1": [55, 40]}, "exe": "", "file": ""}))
    assert laser.run_helper(str(job)) == 0
    data = cfg.read_bytes()
    assert laser.read_layer(data, laser.find_tables(data)[0], 1) == (55, 40, 40)


def test_painel_mostra_camadas_e_lembra(tmp_path):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QLineEdit
    QApplication.instance() or QApplication([])
    from app.ui.main_window import MainWindow
    w = MainWindow(workers=0)
    w.set_material_mode(False)                         # modo antigo: uma linha por cor do desenho
    w.parts = [_part("P1", "MDF 3mm", 7), _part("P2", "MDF 6mm", 1)]
    w.pmap = {p.id: p for p in w.parts}
    w._refresh_cut_panel()
    assert w.settings_panel.laser_box.isVisibleTo(w.settings_panel)
    edits = w.settings_panel.laser_box.findChildren(QLineEdit)
    assert len(edits) == 4
    QTest.keyClicks(edits[0], "16,5")                  # digitar de verdade (vírgula ou ponto)
    QTest.keyClicks(edits[1], "85")
    assert w.laser_values() == {"MDF 3mm|7": [16.5, 85.0]}
    QTest.keyClicks(edits[1], "9")                     # 859 não passa de 100%
    assert w.laser_values()["MDF 3mm|7"][1] == 100.0
    edits[1].clear()                                   # em branco = não mexer
    assert w.laser_values() == {}
    edits[1].setText("85")
    w.placements = [Placement("P1", 0, 0, 40, 40, 0)]
    assert w._laser_report_lines() == ["Laser · MDF 3mm · CORTE: 16.5 mm/s · 85%"]
    w.set_material_mode(True)
    w.dirty = False
    w.close()


def test_exporta_so_uma_placa(tmp_path, monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from app.ui.dialogs import settings
    from app.ui.main_window import MainWindow
    w = MainWindow(workers=0)
    w.parts = [_part("P1", "MDF 3mm", 7), _part("P2", "MDF 3mm", 7), _part("P3", "MDF 3mm", 7)]
    w.pmap = {p.id: p for p in w.parts}
    w.files = ["a.dxf"]
    w.placements = [Placement("P1", 0, 0, 40, 40, 0), Placement("P2", 0, 1, 40, 40, 0),
                    Placement("P3", 0, 2, 40, 40, 0)]
    w.n_sheets = 3
    w.request_label = "lote"
    st = settings()
    out_dir = tmp_path / "saida"
    out_dir.mkdir()
    st.setValue("export/last_dir", str(out_dir))
    st.setValue("export/open_rdworks", "false")
    st.setValue("export/outline2", "true")
    w.export(sheet=1)
    out = sorted(os.listdir(out_dir))
    assert out == ["lote_placa2_MDF3mm.dxf"]                   # só a placa 2, sem relatório
    doc = ezdxf.readfile(str(out_dir / "lote_placa2_MDF3mm.dxf"))
    assert len([e for e in doc.modelspace() if e.dxf.layer == "CORTE"]) == 1
    w.export()
    assert sorted(os.listdir(out_dir)) == ["lote_placa2_MDF3mm.dxf", "lote_relatorio.pdf", "lote_todas_placas.dxf"]
    w._fill_export_menu()
    texts = [a.text() for a in w._export_menu.actions()]
    assert any(t.startswith("   Placa 3") for t in texts)
    w.dirty = False
    w.close()


# ------------------------------------------------------------ uma cor por material + números
def _mpart(pid, material, colors=(7,), size=40, tag=""):
    h = size / 2
    prims = [Prim("POLY", {"pts": [(-h, -h), (h, -h), (h, h), (-h, h)], "closed": True}, "CORTE", c)
             for c in colors]
    return Part(pid, pid, "x.dxf", box(-h, -h, h, h), [], prims, [0], material=material, tag=tag)


def test_cores_por_material_distintas_e_sem_a_dos_numeros():
    cols = laser.default_material_colors(["MDF 3mm", "MDF 6mm"], {}, 1)
    assert cols == {"MDF 3mm": 7, "MDF 6mm": 5}
    cols = laser.default_material_colors(["MDF 3mm", "MDF 6mm"], {"MDF 6mm": {"color": 7}}, 1)
    assert cols["MDF 6mm"] == 7 and cols["MDF 3mm"] not in (7, 1)


def test_material_color_map_junta_todas_as_cores_do_material():
    parts = [_mpart("a", "MDF 3mm", (7, 1, 5)), _mpart("b", "MDF 6mm", (7,))]
    cmap = laser.material_color_map(parts, {"MDF 3mm": 3, "MDF 6mm": 5})
    assert cmap == {("MDF 3mm", 7): 3, ("MDF 3mm", 1): 3, ("MDF 3mm", 5): 3, ("MDF 6mm", 7): 5}


def test_material_values_vai_para_a_camada_certa():
    cfg = {"MDF 3mm": {"speed": 20, "power": 60}, "MDF 6mm": {"speed": 8, "power": 0}}
    vals = laser.material_values({"MDF 3mm": 7, "MDF 6mm": 5}, cfg,
                                 {"on": True, "color": 1, "speed": 300, "power": 15}, PALETTE)
    assert vals == {0: (20.0, 60.0), 2: (300.0, 15.0)}          # 6 mm sem potência: não mexe
    vals = laser.material_values({"MDF 3mm": 3}, {"MDF 3mm": {"speed": 5, "power": 70}}, None, PALETTE)
    assert vals == {3: (5.0, 70.0)}


def test_numeros_no_canto_das_pecas(tmp_path):
    from app.core.dxf_export import export_all_sheets, LABEL_LAYER
    from shapely.geometry import Point
    parts = [_mpart("a", "MDF 3mm", tag="10"), _mpart("b", "MDF 3mm", tag="11"), _mpart("c", "MDF 3mm", size=3)]
    pls = [Placement("a", 0, 0, 50, 50, 0, False), Placement("b", 0, 0, 120, 50, 0, False),
           Placement("c", 0, 0, 200, 50, 0, False)]
    params = NestParams(sheet_width=300, sheet_height=200, margin=0, spacing=2)
    stats = {}
    out = export_all_sheets(parts, pls, params, str(tmp_path), "t", stats=stats,
                            color_map=laser.material_color_map(parts, {"MDF 3mm": 5}),
                            part_labels={"a": "1", "b": "2", "c": "3"}, label_aci=1, label_height=3)
    assert stats["labels"] == 2 and stats["labels_skipped"] == 1   # a peça de 3 mm não cabe número
    msp = ezdxf.readfile(out).modelspace()
    nums = [e for e in msp if e.dxf.layer == LABEL_LAYER]
    assert nums and all(e.dxf.color in (1, 256) for e in nums)
    cuts = [e for e in msp if e.dxf.layer != LABEL_LAYER and e.dxf.layer.upper() not in ("CONTORNO", "PLACAS")]
    assert cuts and all(e.dxf.color == 5 for e in cuts if e.dxf.color != 256)
    # todos os pontos dos números ficam dentro das peças a ou b
    xs = [p[0] for e in nums if e.dxftype() == "LWPOLYLINE" for p in e.get_points()] + \
         [v for e in nums if e.dxftype() == "LINE" for v in (e.dxf.start.x, e.dxf.end.x)]
    assert xs and all(30 <= x <= 140 for x in xs)


def test_painel_por_material_e_numeros_do_lote(tmp_path, monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QMessageBox
    QApplication.instance() or QApplication([])
    from app.ui.dialogs import settings
    from app.ui.main_window import MainWindow
    from app.core.dxf_export import LABEL_LAYER
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    for k in ("laser/materials", "laser/numbers", "laser/params", "laser/material_mode"):
        settings().remove(k)
    w = MainWindow(workers=0)
    w.parts = [_mpart("A1", "MDF 3mm", (7, 1), tag="8787"), _mpart("A2", "MDF 6mm", tag="8787"),
               _mpart("B1", "MDF 3mm", tag="8790")]
    w.pmap = {p.id: p for p in w.parts}
    w.request_info = {"batch": True, "materials": ["MDF 3mm", "MDF 6mm"],
                      "requests": [{"code": "8787", "nome": "Aluno A"}, {"code": "8790", "nome": "Aluno B"}]}
    w._refresh_cut_panel()
    sp = w.settings_panel
    edits = [e for e in sp.laser_box.findChildren(QLineEdit) if e.objectName() != "qt_spinbox_lineedit"]
    assert len(edits) == 6                                   # 3 mm, 6 mm e números (vel + pot cada)
    QTest.keyClicks(edits[0], "20")
    QTest.keyClicks(edits[1], "60")
    QTest.keyClicks(edits[4], "300")
    QTest.keyClicks(edits[5], "15")
    assert w.material_cfg()["MDF 3mm"] == {"color": 7, "speed": 20.0, "power": 60.0}
    assert w.numbers_cfg()["speed"] == 300 and w.numbers_cfg()["power"] == 15
    assert w._material_colors() == {"MDF 3mm": 7, "MDF 6mm": 5}
    assert w._part_labels() == {"A1": "1", "A2": "1", "B1": "2"}
    combos = [c for c in sp.laser_box.findChildren(QComboBox)]
    combos[1].setCurrentIndex(combos[1].findData(3))        # 6 mm em verde
    assert w._material_colors()["MDF 6mm"] == 3
    w.placements = [Placement("A1", 0, 0, 50, 50, 0), Placement("A2", 0, 1, 50, 50, 0),
                    Placement("B1", 0, 0, 120, 50, 0)]
    w.n_sheets = 2
    _, cmap, vals = w._material_plan()
    assert cmap[("MDF 3mm", 1)] == 7 and cmap[("MDF 6mm", 7)] == 3
    assert vals[0] == (20.0, 60.0) and vals[2] == (300.0, 15.0)
    lines = w._laser_report_lines()
    assert any("1 = nº 8787 Aluno A" in x and "2 = nº 8790 Aluno B" in x for x in lines)
    settings().setValue("export/last_dir", str(tmp_path))
    settings().setValue("export/open_rdworks", "false")
    w.files = [str(tmp_path / "x.dxf")]
    w.export()
    dxf = next(tmp_path.glob("*_todas_placas.dxf"))
    msp = ezdxf.readfile(str(dxf)).modelspace()
    assert any(e.dxf.layer == LABEL_LAYER for e in msp)
    # a tela mostra o número onde ele vai ser gravado (filho da peça)
    from PySide6.QtWidgets import QGraphicsPathItem
    w.tabs.setCurrentIndex(1)
    w._redraw()
    marks = [c for it in w.canvas.part_items for c in it.childItems() if isinstance(c, QGraphicsPathItem)]
    assert len(marks) == 3
    # os números não dependem de "uma cor por material"
    w.set_material_mode(False)
    assert w._part_labels() == {"A1": "1", "A2": "1", "B1": "2"}
    assert any("1 = nº 8787" in x for x in w._laser_report_lines())
    w.set_material_mode(True)
    for k in ("laser/materials", "laser/numbers"):
        settings().remove(k)
    w.dirty = False
    w.close()
