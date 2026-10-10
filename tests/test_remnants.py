"""Retalhos: cadastro, encaixe usando retalho antes de chapa nova e sobra da placa cortada."""
import ezdxf
import pytest
from shapely.geometry import Polygon, box

from app.core.models import NestParams, Part, Placement, Prim
from app.core.optimizer import GeneticNester
from app.core.remnants import (RemnantError, RemnantStore, from_dxf, leftover, make_remnant, rectangle)
from app.core.sheetspec import remnant_spec
from app.core.validate import validate_layout

L_SHAPE = Polygon([(0, 0), (300, 0), (300, 100), (100, 100), (100, 250), (0, 250)])


def _rect_part(pid, w, h, qty, material="MDF 3mm"):
    prims = [Prim("POLY", {"pts": [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)],
                           "closed": True}, "CORTE", 7)]
    p = Part(pid, pid, "x.dxf", box(-w / 2, -h / 2, w / 2, h / 2), [], prims, [0], material=material)
    p.quantity = p.file_quantity = qty
    return p


def test_store_ida_e_volta(tmp_path):
    store = RemnantStore.load(str(tmp_path / "retalhos.json"))
    r = store.add(rectangle("MDF 3mm", 300, 200, "canto"))
    store.add(make_remnant("Acrílico", L_SHAPE))
    again = RemnantStore.load(store.path)
    assert [x.id for x in again.available(["mdf 3 mm"])] == [r.id]          # material sem caixa/espaço
    again.mark_used(r.id)
    assert RemnantStore.load(store.path).available(["MDF 3mm"]) == []
    with pytest.raises(RemnantError):
        rectangle("MDF", 0, 10)


def test_retalho_de_dxf_com_buraco(tmp_path):
    doc = ezdxf.new("R2000")
    msp = doc.modelspace()
    msp.add_lwpolyline([(10, 10), (210, 10), (210, 160), (10, 160)], close=True)
    msp.add_circle((110, 85), 30)
    path = str(tmp_path / "sobra.dxf")
    doc.saveas(path)
    r = from_dxf(path, "MDF 3mm")
    assert r.size == pytest.approx((200, 150), abs=0.01) and len(r.holes) == 1
    assert r.area == pytest.approx(200 * 150 - 3.14159 * 900, rel=0.01)
    assert min(x for x, _ in r.outline) == 0 and min(y for _, y in r.outline) == 0


def test_encaixe_usa_o_retalho_antes_da_chapa_nova():
    rem = make_remnant("MDF 3mm", L_SHAPE)
    ring = make_remnant("MDF 3mm", Polygon([(0, 0), (200, 0), (200, 200), (0, 200)],
                                           [[(50, 50), (150, 50), (150, 150), (50, 150)]]))
    parts = [_rect_part("A", 80, 60, 6), _rect_part("B", 30, 30, 4)]
    params = NestParams(sheet_width=600, sheet_height=400, remnants=[rem.to_params(), ring.to_params()])
    gn = GeneticNester(parts, params, seed=2, workers=0)
    res = gn.evaluate_local(gn.first_individual())
    assert not res.unplaced
    assert res.sheet_remnants[0] == rem.id                        # a 1ª placa é o retalho em L
    sheet_rem = {i: x for i, x in enumerate(res.sheet_remnants) if x}
    pmap = {p.id: p for p in parts}
    assert validate_layout(pmap, res.placements, params, sheet_remnants=sheet_rem) == []
    # nenhuma peça do retalho fica fora do L (com margem)
    from app.core.validate import fine_solid, placed_geometry
    usable = remnant_spec(params, rem.to_params()).usable(0.02)
    on_rem = [pl for pl in res.placements if pl.sheet_index == 0]
    assert on_rem and all(usable.covers(placed_geometry(fine_solid(pmap[pl.part_id]), pl)) for pl in on_rem)
    # validar como se fosse chapa inteira não acharia o problema; como retalho, uma peça fora do L acusa
    bad = [Placement("A", 0, 0, 200, 200, 0)]
    assert validate_layout(pmap, bad, params, sheet_remnants={0: rem.id})
    assert validate_layout(pmap, bad, params) == []


def test_sobra_da_placa():
    used = [box(5, 5, 300, 395)]
    pieces = leftover(box(0, 0, 600, 400), used, spacing=2)
    assert len(pieces) == 1 and pieces[0].bounds[0] == pytest.approx(302, abs=0.01)
    assert leftover(box(0, 0, 600, 400), [box(5, 5, 590, 395)], spacing=2) == []   # só tiras estreitas


def test_retalhos_na_janela(tmp_path, monkeypatch):
    from app.ui.main_window import MainWindow
    from app.ui.remnants_dialog import RemnantsDialog
    w = MainWindow(workers=0)
    w.parts = [_rect_part("A", 80, 60, 3)]
    w.pmap = {p.id: p for p in w.parts}
    dlg = RemnantsDialog(w)
    dlg.material.setCurrentText("mdf 3mm")
    dlg.w.setValue(200)
    dlg.h.setValue(150)
    dlg.add_rectangle()
    assert dlg.table.rowCount() == 1
    dlg.close()
    rems = w.available_remnants()
    assert len(rems) == 1 and rems[0]["material"] == "MDF 3mm"   # escrito como nas peças
    p = w.nest_params()
    assert p.remnants == rems
    # encaixe pela janela: a placa 1 é o retalho e o canvas desenha o retalho
    from app.core.optimizer import nest
    res = nest(w.parts, p, time_limit=1, max_generations=1, workers=0, seed=1)
    w.placements, w.n_sheets = list(res.placements), res.sheets_used
    w.sheet_remnants = {i: x for i, x in enumerate(res.sheet_remnants) if x}
    assert w.sheet_remnants == {0: rems[0]["id"]}
    w.tabs.setCurrentIndex(1)
    w._redraw()
    assert w.canvas.sheet_items[0].spec.is_remnant and w.canvas.sheet_items[0].spec.width == 200
    # cortou: o retalho sai da lista e a sobra vira retalho novo
    w.on_sheet_cut(0, True)
    assert w.remnant_store().available(["MDF 3mm"]) == []
    added = w.save_leftover(0)
    assert added and all(r.material == "MDF 3mm" for r in added)
    assert w.available_remnants()                                 # a sobra entra no próximo encaixe
    # o projeto lembra qual placa era retalho
    proj = tmp_path / "p.sindri"
    w.files = [str(tmp_path / "x.dxf")]
    w._write_project(str(proj))
    w.sheet_remnants = {}
    import json
    extra = json.load(open(proj, encoding="utf-8"))["extra"]
    w._load_project_extra(extra)
    assert w.sheet_remnants == {0: rems[0]["id"]}
    w.dirty = False
    w.close()
