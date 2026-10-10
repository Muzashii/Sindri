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
    assert out == ["lote_placa02_MDF3mm.dxf"]                  # só a placa 2, sem relatório
    doc = ezdxf.readfile(str(out_dir / "lote_placa02_MDF3mm.dxf"))
    assert len([e for e in doc.modelspace() if e.dxf.layer == "CORTE"]) == 1
    assert not [e for e in doc.modelspace() if e.dxf.layer == "PLACA"]   # arquivo de corte: sem contorno
    w.export()
    # um arquivo de corte por placa + conferência + relatório (o nome livre ganha _2)
    assert sorted(os.listdir(out_dir)) == ["lote_2_placa01_MDF3mm.dxf", "lote_2_placa02_MDF3mm.dxf",
                                           "lote_2_placa03_MDF3mm.dxf", "lote_2_relatorio.pdf",
                                           "lote_2_todas_placas.dxf", "lote_placa02_MDF3mm.dxf"]
    conf = ezdxf.readfile(str(out_dir / "lote_2_todas_placas.dxf"))
    assert [e for e in conf.modelspace() if e.dxf.layer == "PLACA"]     # conferência mantém o contorno
    for n in (1, 2, 3):                                                # origem no canto da chapa
        d = ezdxf.readfile(str(out_dir / f"lote_2_placa0{n}_MDF3mm.dxf"))
        xs = [v[0] for e in d.modelspace() if e.dxftype() == "LWPOLYLINE" for v in e.get_points()]
        assert min(xs) >= 0 and max(xs) <= 300
    assert w.sheet_files[2].endswith("lote_2_placa03_MDF3mm.dxf")
    w._fill_export_menu()
    texts = [a.text() for a in w._export_menu.actions()]
    assert any(t.startswith("   Placa 3") for t in texts)
    w.dirty = False
    w.close()


# ------------------------------------------------------------ uma cor por material + números
def _mpart(pid, material, colors=(7,), size=40, tag=""):
    h = size / 2
    # a 1ª cor é o contorno (camada CORTE); as outras ficam numa camada de nome neutro
    prims = [Prim("POLY", {"pts": [(-h, -h), (h, -h), (h, h), (-h, h)], "closed": True},
                  "CORTE" if i == 0 else "DESENHO", c) for i, c in enumerate(colors)]
    return Part(pid, pid, "x.dxf", box(-h, -h, h, h), [], prims, [0], material=material, tag=tag)


def test_cores_por_material_distintas_e_sem_a_dos_numeros():
    cols = laser.default_material_colors(["MDF 3mm", "MDF 6mm"], {}, 1)
    assert cols == {"MDF 3mm": 7, "MDF 6mm": 5}
    cols = laser.default_material_colors(["MDF 3mm", "MDF 6mm"], {"MDF 6mm": {"color": 7}}, 1)
    assert cols["MDF 6mm"] == 7 and cols["MDF 3mm"] not in (7, 1)


def test_so_o_corte_vai_para_a_cor_do_material():
    # a: contorno preto (corte) + quadrado vermelho dentro (outra cor = gravação por padrão)
    parts = [_mpart("a", "MDF 3mm", (7, 1)), _mpart("b", "MDF 6mm", (7,))]
    op_colors = laser.material_op_colors(parts, {"MDF 3mm": 3, "MDF 6mm": 5}, numbers_color=1)
    assert op_colors[("MDF 3mm", "corte")] == 3 and op_colors[("MDF 6mm", "corte")] == 5
    eng = op_colors[("MDF 3mm", "gravacao_vetorial")]
    assert eng not in (3, 5, 1)                       # nem corte de nenhum material, nem números
    assert ("MDF 6mm", "gravacao_vetorial") not in op_colors
    # o técnico diz que o vermelho é corte: some a camada de gravação
    op_colors = laser.material_op_colors(parts, {"MDF 3mm": 3, "MDF 6mm": 5},
                                         {("MDF 3mm", 1): "corte"}, numbers_color=1)
    assert set(op_colors) == {("MDF 3mm", "corte"), ("MDF 6mm", "corte")}


def test_material_values_vai_para_a_camada_certa():
    cfg = {"MDF 3mm": {"speed": 20, "power": 60}, "MDF 6mm": {"speed": 8, "power": 0}}
    vals = laser.material_values({"MDF 3mm": 7, "MDF 6mm": 5}, cfg,
                                 {"on": True, "color": 1, "speed": 300, "power": 15}, PALETTE)
    # 6 mm sem potência: não mexe; mínima = 65% da máxima (a Ruida usa a mínima nos cantos)
    assert vals == {0: (20.0, 39.0, 60.0), 2: (300.0, 9.8, 15.0)}
    vals = laser.material_values({"MDF 3mm": 3}, {"MDF 3mm": {"speed": 5, "power": 70, "power_min": 50}},
                                 None, PALETTE)
    assert vals == {3: (5.0, 50.0, 70.0)}


def test_numeros_no_canto_das_pecas(tmp_path):
    from app.core.dxf_export import export_all_sheets, LABEL_LAYER
    from shapely.geometry import Point
    parts = [_mpart("a", "MDF 3mm", tag="10"), _mpart("b", "MDF 3mm", tag="11"), _mpart("c", "MDF 3mm", size=3)]
    pls = [Placement("a", 0, 0, 50, 50, 0, False), Placement("b", 0, 0, 120, 50, 0, False),
           Placement("c", 0, 0, 200, 50, 0, False)]
    params = NestParams(sheet_width=300, sheet_height=200, margin=0, spacing=2)
    stats = {}
    out = export_all_sheets(parts, pls, params, str(tmp_path), "t", stats=stats,
                            op_colors=laser.material_op_colors(parts, {"MDF 3mm": 5}),
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
    # 3 mm corte, 3 mm gravação (o quadrado vermelho de A1), 6 mm e números (vel + pot cada)
    assert len(edits) == 8
    QTest.keyClicks(edits[0], "20")
    QTest.keyClicks(edits[1], "60")
    QTest.keyClicks(edits[2], "250")
    QTest.keyClicks(edits[3], "12")
    QTest.keyClicks(edits[6], "300")
    QTest.keyClicks(edits[7], "15")
    assert {k: w.material_cfg()["MDF 3mm"][k] for k in ("color", "speed", "power")} == \
        {"color": 7, "speed": 20.0, "power": 60.0}
    assert w.material_cfg()["MDF 3mm"]["ops"]["gravacao_vetorial"]["speed"] == 250
    assert w.numbers_cfg()["speed"] == 300 and w.numbers_cfg()["power"] == 15
    assert w._material_colors() == {"MDF 3mm": 7, "MDF 6mm": 5}
    assert w._part_labels() == {"A1": "1", "A2": "1", "B1": "2"}
    combos = [c for c in sp.laser_box.findChildren(QComboBox)]
    combos[2].setCurrentIndex(combos[2].findData(3))        # 6 mm em verde
    assert w._material_colors()["MDF 6mm"] == 3
    w.placements = [Placement("A1", 0, 0, 50, 50, 0), Placement("A2", 0, 1, 50, 50, 0),
                    Placement("B1", 0, 0, 120, 50, 0)]
    w.n_sheets = 2
    _, op_colors, vals = w._material_plan()
    assert op_colors[("MDF 3mm", "corte")] == 7 and op_colors[("MDF 6mm", "corte")] == 3
    eng = op_colors[("MDF 3mm", "gravacao_vetorial")]
    assert eng not in (7, 3, 1)                             # gravação nunca na cor de corte
    assert vals[0] == (20.0, 39.0, 60.0) and vals[2] == (300.0, 9.8, 15.0)
    assert vals[laser.nearest_layer(laser.aci_rgb(eng), laser.DEFAULT_PALETTE)] == (250.0, 7.8, 12.0)
    from app.ui.color_ops_dialog import ColorOpsDialog
    asked = []
    monkeypatch.setattr(ColorOpsDialog, "exec", lambda self: asked.append(1) or 1)
    lines = w._laser_report_lines()
    assert any("1 = nº 8787 Aluno A" in x and "2 = nº 8790 Aluno B" in x for x in lines)
    settings().setValue("export/last_dir", str(tmp_path))
    settings().setValue("export/open_rdworks", "false")
    w.files = [str(tmp_path / "x.dxf")]
    w.export()
    assert asked == [1]                                       # duas cores no 3 mm: o técnico confere uma vez
    w.export()
    assert asked == [1]
    dxf = next(tmp_path.glob("*_todas_placas.dxf"))
    msp = ezdxf.readfile(str(dxf)).modelspace()
    assert any(e.dxf.layer == LABEL_LAYER for e in msp)
    assert sum(1 for e in msp if e.dxf.layer == "DESENHO" and e.dxf.color == eng) == 1
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


# ------------------------------------------------------- relatório de bancada: gravação ≠ corte
def _prova_dxf(path):
    """DXF de prova do relatório: retângulo preto (corte) + linha e círculo azuis (gravação)."""
    doc = ezdxf.new("R2000")
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (100, 0), (100, 60), (0, 60)], close=True, dxfattribs={"color": 7})
    msp.add_line((10, 30), (90, 30), dxfattribs={"color": 5})
    msp.add_circle((50, 45), 8, dxfattribs={"color": 5})
    doc.saveas(path)
    return path


def test_modo_por_material_nao_corta_a_gravacao(tmp_path):
    import os
    from app.core import operations
    from app.core.dxf_export import export_all_sheets
    from app.core.part_builder import import_files
    src = _prova_dxf(str(tmp_path / "prova.dxf"))
    parts = import_files([src], file_materials={os.path.abspath(src): "MDF 3mm"}).parts
    assert len(parts) == 1
    part = parts[0]
    ops = operations.prim_operations(part)
    kinds = sorted((p.kind, op) for p, op in zip(part.prims, ops))
    assert kinds == [("CIRCLE", "gravacao_vetorial"), ("LINE", "gravacao_vetorial"), ("POLY", "corte_externo")]
    assert operations.needs_confirmation(parts)
    colors = laser.default_material_colors(["MDF 3mm"], {}, -1)
    op_colors = laser.material_op_colors(parts, colors)
    assert op_colors[("MDF 3mm", "corte")] == 7
    assert op_colors[("MDF 3mm", "gravacao_vetorial")] not in (7,)
    params = NestParams(sheet_width=300, sheet_height=200)
    out = export_all_sheets(parts, [Placement(part.id, 0, 0, 80, 60, 0)], params, str(tmp_path), "t",
                            op_colors=op_colors)
    msp = list(ezdxf.readfile(out).modelspace())
    by_kind = {e.dxftype(): e.dxf.color for e in msp}
    assert by_kind["LWPOLYLINE"] == 7
    assert by_kind["LINE"] != 7 and by_kind["CIRCLE"] != 7          # a linha azul NÃO sai na cor de corte
    assert by_kind["LINE"] == by_kind["CIRCLE"] == op_colors[("MDF 3mm", "gravacao_vetorial")]
    # gravar antes de cortar: o contorno é o último da peça
    assert [e.dxftype() for e in msp][-1] == "LWPOLYLINE"
    # se o técnico disser que o azul é corte, ele vira furo (cor de corte)
    chosen = {("MDF 3mm", 5): operations.CUT}
    op_colors = laser.material_op_colors(parts, colors, chosen)
    out = export_all_sheets(parts, [Placement(part.id, 0, 0, 80, 60, 0)], params, str(tmp_path), "t2",
                            op_colors=op_colors, color_ops=chosen)
    assert {e.dxf.color for e in ezdxf.readfile(out).modelspace()} == {7}


def test_arquivo_todo_preto_continua_todo_corte():
    from app.core import operations
    part = _mpart("a", "MDF 3mm", (7, 7))
    assert operations.default_color_ops([part]) == {("MDF 3mm", 7): "corte"}
    assert not operations.needs_confirmation([part])
    assert operations.prim_operations(part) == ["corte_externo", "corte_interno"]


def test_color_ops_json_ida_e_volta():
    from app.core import operations
    ops = {("MDF 3mm", 5): "gravacao_vetorial", ("Acrílico", 1): "vinco"}
    assert operations.color_ops_from_json(operations.color_ops_to_json(ops)) == ops
    assert operations.color_ops_from_json([["x", 1, "inventado"], "lixo"]) == {}


def test_material_proibido_bloqueia():
    from app.core.material_safety import BLOCKED, WARN, OK, blocked_materials, check_material
    for name in ("PVC 2mm", "Vinil adesivo", "Policarbonato 3 mm", "ABS preto", "Fibra de vidro",
                 "couro sintético", "Lexan"):
        assert check_material(name).status == BLOCKED, name
    for name in ("MDF 3mm", "Acrílico cast 3mm", "Papelão", "Compensado 4mm", "Absinto?"):
        assert check_material(name).status == OK, name
    assert check_material("Acrílico 3mm").status == WARN
    assert check_material("MDF 3mm", forbidden=True).status == BLOCKED
    assert [m for m, _ in blocked_materials(["MDF 3mm", "PVC", "PVC"])] == ["PVC"]


def test_exportacao_bloqueada_para_pvc(tmp_path, monkeypatch):
    import os
    from PySide6.QtWidgets import QMessageBox
    from app.ui.dialogs import settings
    from app.ui.main_window import MainWindow
    shown = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: shown.append(a[2]) or QMessageBox.Ok)
    w = MainWindow(workers=0)
    w.parts = [_mpart("A", "PVC 3mm")]
    w.pmap = {p.id: p for p in w.parts}
    w.files = [str(tmp_path / "x.dxf")]
    w.placements = [Placement("A", 0, 0, 50, 50, 0)]
    w.n_sheets = 1
    settings().setValue("export/last_dir", str(tmp_path))
    settings().setValue("export/open_rdworks", "false")
    w.export()
    assert shown and "PVC" in shown[0] and "cloro" in shown[0]
    assert not [f for f in os.listdir(tmp_path) if f.endswith(".dxf")]
    w.dirty = False
    w.close()


def test_nome_da_camada_ajuda_a_escolher_a_operacao():
    from app.core import operations
    h = 20
    prims = [Prim("POLY", {"pts": [(-h, -h), (h, -h), (h, h), (-h, h)], "closed": True}, "CORTE", 7),
             Prim("CIRCLE", {"c": (0, 0), "r": 4}, "FUROS", 1),
             Prim("LINE", {"s": (-10, 10), "e": (10, 10)}, "Vinco", 3),
             Prim("LINE", {"s": (-10, -10), "e": (10, -10)}, "desenho", 5)]
    part = Part("p", "p", "x.dxf", box(-h, -h, h, h), [], prims, [0], material="MDF")
    assert operations.default_color_ops([part]) == {("MDF", 7): "corte", ("MDF", 1): "corte",
                                                    ("MDF", 3): "vinco", ("MDF", 5): "gravacao_vetorial"}


def test_abrir_placa_no_rdworks_usa_o_arquivo_da_placa(tmp_path, monkeypatch):
    import os
    from app.ui.dialogs import settings
    from app.ui.main_window import MainWindow
    from app.ui.mainwindow import export as mwe
    launched = []
    monkeypatch.setattr(mwe, "find_rdworks", lambda saved=None: "C:/RDWorksV8/RDWorksV8.exe")
    monkeypatch.setattr(mwe, "launch", lambda exe, path: launched.append(path))
    w = MainWindow(workers=0)
    w.parts = [_part("P1", "MDF 3mm", 7), _part("P2", "MDF 3mm", 7)]
    w.pmap = {p.id: p for p in w.parts}
    w.files = ["a.dxf"]
    w.placements = [Placement("P1", 0, 0, 40, 40, 0), Placement("P2", 0, 1, 40, 40, 0)]
    w.n_sheets = 2
    w.request_label = "lote"
    st = settings()
    st.setValue("export/last_dir", str(tmp_path))
    st.setValue("export/open_rdworks", "true")
    w.export()
    assert [os.path.basename(x) for x in launched] == ["lote_placa01_MDF3mm.dxf"]   # 1ª não cortada
    assert "Confira no RDWorks" in w.banner_text.text() and "Saída" in w.banner_text.text()
    w.on_sheet_cut(0, True)
    w.parts_panel.sheet_open_buttons[1].click()                     # ▶ da placa 2: abre o arquivo já pronto
    assert os.path.basename(launched[-1]) == "lote_placa02_MDF3mm.dxf"
    n_files = len(os.listdir(tmp_path))
    w.placements[1].x += 30                                          # a placa mudou: exporta de novo antes
    w.open_sheet_in_rdworks(1)
    assert len(os.listdir(tmp_path)) == n_files + 1
    assert os.path.basename(launched[-1]) == "lote_2_placa02_MDF3mm.dxf"
    w.dirty = False
    w.close()


def test_ordem_comeca_no_canto_escolhido():
    from app.core.dxf_export import order_placements
    parts = {"a": _part("a", "", 7, w=20), "b": _part("b", "", 7, w=20)}
    pls = [Placement("a", 0, 0, 30, 30, 0), Placement("b", 0, 0, 270, 170, 0)]
    assert [p.part_id for p in order_placements(parts, pls)] == ["a", "b"]
    assert [p.part_id for p in order_placements(parts, pls, start=(300, 200))] == ["b", "a"]


def test_conferencia_camada_a_camada():
    lines = laser.layer_checklist([
        laser.LayerCheck(7, "MDF 3mm · corte", "corte", "", 20, 13, 60, 2),
        laser.LayerCheck(3, "MDF 3mm · gravação vetorial", "gravacao_vetorial", "", 300, 0, 15),
        laser.LayerCheck(1, "nº da solicitação", "numeros"),
    ], PALETTE)
    assert [c.op for c in lines] == ["numeros", "gravacao_vetorial", "corte"]   # grava antes de cortar
    assert lines[2].values == "20 mm/s · 13–60% · 2 passadas" and lines[0].values == "valores do RDWorks"
    html = laser.checklist_html(lines)
    assert html.count("<tr>") == 4 and "SIM" in html


# ------------------------------------------------------- parâmetros completos + config do RDWorks
def test_um_tubo_nao_mexe_no_tubo_2_e_le_de_volta(tmp_path):
    data = fake_config()
    t = laser.find_tables(data)[0]
    new = laser.patch(data, {0: (20, 13, 60)})                       # padrão: máquina de 1 tubo
    s, a, b, c, d = struct.unpack_from("<5d", new, t.offset)
    assert (s, a, b) == (20, 13, 60) and (c, d) == (30, 30)          # tubo 2 como estava
    new2 = laser.patch(data, {0: (20, 13, 60)}, single_tube=False)
    assert struct.unpack_from("<5d", new2, t.offset) == (20, 13, 60, 13, 60)
    cfg = tmp_path / "config"
    cfg.write_bytes(data)
    applied = laser.apply_to_config(str(cfg), {0: (20, 13, 60), 2: (300, 15)})
    assert applied == [(0, 20.0, 13.0, 60.0), (2, 300.0, 15.0, 15.0)]
    assert "preto = 20 mm/s 13–60%" in laser.applied_text(applied, PALETTE)
    assert laser.config_version(data) == "8.0.01"


def test_leitura_de_volta_errada_restaura_o_config(tmp_path, monkeypatch):
    import pytest
    data = fake_config()
    cfg = tmp_path / "config"
    cfg.write_bytes(data)
    monkeypatch.setattr(laser, "verify", lambda d, v, tol=1e-6: (_ for _ in ()).throw(ValueError("não bate")))
    with pytest.raises(ValueError):
        laser.apply_to_config(str(cfg), {0: (20, 60)})
    assert cfg.read_bytes() == data                                  # voltou como estava


def test_rd_triple_minima_padrao():
    assert laser.rd_triple(20, 60) == (20.0, 39.0, 60.0)
    assert laser.rd_triple(20, 60, 50) == (20.0, 50.0, 60.0)
    assert laser.rd_triple(300, 40, 0, "scan") == (300.0, 40.0, 40.0)
    assert laser.rd_triple(0, 60) is None and laser.rd_triple(20, 0) is None
    assert laser.rd_triple(20, 60, 80) == (20.0, 39.0, 60.0)         # mínima maior que a máxima: ignora
    c = laser.check_from_entry(7, "MDF 6mm · corte", "corte",
                               {"speed": 8, "power": 80, "passes": 2, "air": "ligado"})
    assert c.values == "8 mm/s · 52–80% · 2 passadas" and c.mode_text == "Corte · sopro ligado"
    r = laser.check_from_entry(2, "MDF · raster", "gravacao_raster", {"speed": 300, "power": 30, "interval": 0.08})
    assert r.mode == "scan" and r.mode_text == "Scan 0.08 mm"


def test_parametros_completos_pelo_painel(monkeypatch):
    from app.ui.layer_params_dialog import LayerParamsDialog
    from app.ui.main_window import MainWindow
    w = MainWindow(workers=0)
    assert w.single_tube()                                           # laboratório: 1 tubo
    w.settings_panel.two_tubes.setChecked(True)
    assert not w.single_tube()
    w.settings_panel.two_tubes.setChecked(False)
    w.parts = [_mpart("A", "MDF 6mm")]
    w.pmap = {p.id: p for p in w.parts}
    w._refresh_cut_panel()

    def fake_exec(dlg):
        dlg.speed.setValue(8)
        dlg.pmax.setValue(80)
        dlg.pmin.setValue(55)
        dlg.passes.setValue(2)
        return 1
    monkeypatch.setattr(LayerParamsDialog, "exec", fake_exec)
    w.edit_layer_params("MDF 6mm", "corte")
    c = w.material_cfg()["MDF 6mm"]
    assert (c["speed"], c["power"], c["power_min"], c["passes"]) == (8, 80, 55, 2)
    _, op_colors, vals = w._material_plan()
    assert vals[0] == (8.0, 55.0, 80.0)
    w.placements = [Placement("A", 0, 0, 50, 50, 0)]
    checks = w._layer_checks(None, w._laser_export_plan())
    assert checks[0].values == "8 mm/s · 55–80% · 2 passadas"
    w.dirty = False
    w.close()


def test_ferramenta_compara_configs():
    import importlib.util
    import os
    spec = importlib.util.spec_from_file_location(
        "config_diff", os.path.join(os.path.dirname(__file__), "..", "tools", "config_diff.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    a = fake_config()
    b = bytearray(a)
    t = laser.find_tables(a)[0]
    rec = t.offset - 8 + 1 * t.stride                 # camada 1 (azul)
    b[rec + 50] = 0x01                                # um campo depois dos 5 doubles
    out = mod.diff(a, bytes(b))
    assert any("tabela 0, camada 1, byte 50 do registro" in x for x in out)
    assert mod.diff(a, a)[-1] == "nenhum byte diferente"
