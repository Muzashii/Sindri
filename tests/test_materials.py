"""Banco de materiais compartilhado e chapa/espaçamento/veio por material no encaixe."""
import datetime as dt
import json
import os

import pytest
from shapely.geometry import box

from app.core import material_db
from app.core.material_db import Material, MaterialDB, MaterialDBError
from app.core.models import NestParams, Part, Placement, Prim
from app.core.optimizer import GeneticNester
from app.core.validate import validate_layout


def _rect_part(pid, material, w=80, h=50, qty=1):
    prims = [Prim("POLY", {"pts": [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)],
                           "closed": True}, "CORTE", 7)]
    p = Part(pid, pid, "x.dxf", box(-w / 2, -h / 2, w / 2, h / 2), [], prims, [0], material=material)
    p.quantity = p.file_quantity = qty
    return p


def test_banco_criado_com_pontos_de_partida_e_migracao(tmp_path):
    path = str(tmp_path / "lab" / material_db.FILE_NAME)
    db = MaterialDB.load(path, migrate=lambda: {"MDF 3mm": {"speed": 20, "power": 60, "color": 5},
                                                "Papelão": {"speed": 80, "power": 20}})
    assert os.path.isfile(path)
    data = json.load(open(path, encoding="utf-8"))
    assert data["format"] == "sindri-materiais" and data["version"] == 1
    m = db.find("mdf 3 mm")                                   # nome sem diferença de caixa/espaço
    assert m is not None and m.ops["corte"]["speed"] == 20 and m.color == 5
    assert db.find("PAPELÃO").ops["corte"]["power"] == 20
    cfg = db.laser_cfg()["MDF 3mm"]
    assert cfg["speed"] == 20 and cfg["color"] == 5 and cfg["forbidden"] is False


def test_banco_invalido_nao_e_sobrescrito(tmp_path):
    p = tmp_path / material_db.FILE_NAME
    p.write_text("{ quebrado", encoding="utf-8")
    with pytest.raises(MaterialDBError):
        MaterialDB.load(str(p))
    assert p.read_text(encoding="utf-8") == "{ quebrado"
    p.write_text(json.dumps({"format": "sindri-materiais", "version": 99, "materials": []}), encoding="utf-8")
    with pytest.raises(MaterialDBError):
        MaterialDB.load(str(p))


def test_dois_pcs_salvando_nao_perdem_material(tmp_path):
    path = str(tmp_path / material_db.FILE_NAME)
    a = MaterialDB.load(path)
    b = MaterialDB.load(path)
    a.update("Acrílico cast 5mm", lambda m: setattr(m, "thickness", 5))
    b.find("MDF 3mm").kerf = 0.15                             # PC B muda outro material e grava
    b.save()
    c = MaterialDB.load(path)
    assert c.find("Acrílico cast 5mm").thickness == 5 and c.find("MDF 3mm").kerf == 0.15
    c.remove("Acrílico cast 5mm")
    assert MaterialDB.load(path).find("Acrílico cast 5mm") is None


def test_teste_velho_e_overrides():
    m = Material("MDF 6mm", spacing=3, sheet_width=900, sheet_height=600, margin=8, grain=False)
    assert m.is_stale()                                       # sem teste registrado
    m.tested = (dt.date(2026, 10, 10) - dt.timedelta(days=30)).isoformat()
    assert not m.is_stale(dt.date(2026, 10, 10)) and m.test_age_days(dt.date(2026, 10, 10)) == 30
    assert m.is_stale(dt.date(2027, 3, 1))
    assert m.nest_overrides() == {"sheet_width": 900.0, "sheet_height": 600.0, "margin": 8.0, "spacing": 3.0}
    assert Material("MDF 3mm").nest_overrides() == {}         # tudo "do painel"
    with pytest.raises(MaterialDBError):
        Material("X", tested="31/02/2026").validate()


def test_params_por_material_e_veio():
    p = NestParams(sheet_width=600, sheet_height=400, spacing=2,
                   material_sheets={"MDF 6mm": {"sheet_width": 900, "sheet_height": 600, "spacing": 3},
                                    "Compensado": {"grain": True}})
    q = p.for_material("MDF 6mm")
    assert (q.sheet_width, q.sheet_height, q.spacing, q.margin) == (900, 600, 3, 5)
    assert p.for_material("MDF 3mm") is p
    assert p.rotations("Compensado") == [0.0, 180.0] and p.rotations("MDF 3mm") == [0.0, 90.0, 180.0, 270.0]
    p.validate()
    bad = NestParams(material_sheets={"X": {"sheet_width": 20, "sheet_height": 20, "margin": 10}})
    with pytest.raises(ValueError):
        bad.validate()
    back = NestParams.from_json(json.loads(json.dumps(p.to_json())))
    assert back.material_sheets == p.material_sheets


def test_encaixe_usa_a_chapa_e_o_espaco_de_cada_material():
    parts = [_rect_part("A", "MDF 3mm", 300, 200, 4), _rect_part("B", "MDF 6mm", 300, 200, 4),
             _rect_part("C", "Compensado", 300, 40, 2)]
    params = NestParams(sheet_width=600, sheet_height=400, margin=5, spacing=2,
                        material_sheets={"MDF 6mm": {"sheet_width": 900, "sheet_height": 600, "spacing": 6},
                                         "Compensado": {"grain": True}})
    gn = GeneticNester(parts, params, seed=1, workers=0)
    res = gn.evaluate_local(gn.first_individual())
    pmap = {p.id: p for p in parts}
    assert not res.unplaced
    assert validate_layout(pmap, res.placements, params) == []
    sheets_b = {pl.sheet_index for pl in res.placements if pl.part_id == "B"}
    sheets_a = {pl.sheet_index for pl in res.placements if pl.part_id == "A"}
    assert len(sheets_b) == 1                     # 4 peças de 300×200 cabem numa chapa de 900×600…
    assert len(sheets_a) == 2                     # …e só 2 numa de 600×400 (com margem e espaço)
    assert all(pl.rotation in (0.0, 180.0) for pl in res.placements if pl.part_id == "C")
    # o espaço de 6 mm do MDF 6mm é respeitado (e conferido pela validação)
    from app.core.validate import fine_solid, placed_geometry
    gs = [placed_geometry(fine_solid(pmap["B"]), pl) for pl in res.placements if pl.part_id == "B"]
    assert min(a.distance(b) for i, a in enumerate(gs) for b in gs[i + 1:]) >= 6 - 0.02
    # a mesma disposição numa chapa padrão de 600×400 é inválida
    assert validate_layout(pmap, res.placements, NestParams(sheet_width=600, sheet_height=400))


def test_banco_de_materiais_na_janela(tmp_path, monkeypatch):
    from app.ui.dialogs import settings
    from app.ui.main_window import MainWindow
    from app.ui.materials_dialog import MaterialsDialog
    shared = tmp_path / "rede"
    shared.mkdir()
    settings().setValue("laser/materials", json.dumps({"MDF 3mm": {"speed": 18, "power": 55, "color": 7}}))
    w = MainWindow(workers=0)
    w.set_material_db_path(str(shared))
    assert w.material_db().path == str(shared / material_db.FILE_NAME)
    assert w.material_cfg()["MDF 3mm"]["speed"] == 18        # migrado do que estava só neste PC
    w.parts = [_rect_part("A", "mdf 3 mm"), _rect_part("B", "MDF 6mm")]
    w.pmap = {p.id: p for p in w.parts}
    w._refresh_cut_panel()
    w.set_material_value("MDF 6mm", {"color": 5, "speed": 9, "power": 70})
    assert w.material_cfg()["MDF 6mm"]["speed"] == 9           # na memória na hora…
    assert w.flush_material_db()                               # …e no arquivo compartilhado
    assert MaterialDB.load(str(shared / material_db.FILE_NAME)).find("MDF 6mm").ops["corte"]["power"] == 70
    dlg = MaterialsDialog(w, "MDF 6mm")
    assert dlg.name.text() == "MDF 6mm"
    dlg.sheet_w.setValue(900)
    dlg.sheet_h.setValue(600)
    dlg.spacing.setValue(3)
    dlg.tested_on.setChecked(True)
    assert dlg.save_current()
    dlg.close()
    w.__dict__.pop("_mdb", None)
    p = w.nest_params()
    assert p.material_sheets == {"MDF 6mm": {"sheet_width": 900.0, "sheet_height": 600.0, "spacing": 3.0}}
    w._refresh_laser_panel(force=True)
    assert "MDF 6mm" in w.settings_panel.sheet_note.text() and "900×600" in w.settings_panel.sheet_note.text()
    assert w.material_cfg()["MDF 6mm"]["stale"] is False
    # a lista do banco mostra o material novo e o nome normalizado acha o mesmo material
    assert w.material_cfg()["mdf 3 mm"]["speed"] == 18
    w.dirty = False
    w.close()
