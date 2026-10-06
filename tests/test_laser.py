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
    assert out == ["lote_placa2.dxf"]                   # só a placa 2, sem relatório
    doc = ezdxf.readfile(str(out_dir / "lote_placa2.dxf"))
    assert len([e for e in doc.modelspace() if e.dxf.layer == "CORTE"]) == 1
    w.export()
    assert sorted(os.listdir(out_dir)) == ["lote_placa2.dxf", "lote_relatorio.pdf", "lote_todas_placas.dxf"]
    w._fill_export_menu()
    texts = [a.text() for a in w._export_menu.actions()]
    assert any(t.startswith("   Placa 3") for t in texts)
    w.dirty = False
    w.close()
