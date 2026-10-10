"""Checagens de fabricabilidade e selo por solicitação."""
from shapely.geometry import Point, box

from app.core.manufacturability import (MaterialLimits, Seal, SealStore, check_part, seal_for,
                                        thin_regions)
from app.core.models import Part, Placement, Prim


def _poly(pts, layer="CORTE", color=7):
    return Prim("POLY", {"pts": pts, "closed": True}, layer, color)


def _rect(x0, y0, x1, y1, **kw):
    return _poly([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], **kw)


def _part(prims, outer, holes=(), material="MDF 3mm", pid="P", tag=""):
    return Part(pid, pid, "x.dxf", outer, list(holes), prims, [0], material=material, tag=tag)


LIM = MaterialLimits(thickness=3, kerf=0.15, min_part=10, usable=(590, 390))


def kinds(issues):
    return sorted(i.kind for i in issues)


def test_peca_boa_sem_avisos():
    p = _part([_rect(-30, -20, 30, 20), _rect(-10, -5, 10, 5)], box(-30, -20, 30, 20), [box(-10, -5, 10, 5)])
    assert check_part(p, LIM) == []


def test_parede_fina_entre_furo_e_borda():
    # furo a 1 mm da borda direita (limite: 1,5 × 3 = 4,5 mm)
    p = _part([_rect(-30, -20, 30, 20), _rect(0, -10, 29, 10)], box(-30, -20, 30, 20), [box(0, -10, 29, 10)])
    issues = check_part(p, LIM)
    assert kinds(issues) == ["parede_fina"]
    thin = issues[0].geom
    assert thin.bounds[0] >= 28.9 and thin.bounds[2] <= 30.01        # só a parede de 1 mm, à direita
    assert thin_regions(box(0, 0, 50, 50), 4.5) is None                # cantos vivos não contam


def test_furo_menor_que_kerf_peca_pequena_e_texto():
    circle = Prim("CIRCLE", {"c": (0, 0), "r": 0.1}, "CORTE", 7)
    txt = Prim("TEXT", {"text": "A", "p": (2, 2), "h": 2, "bbox": [(2, 2), (3, 2), (3, 4), (2, 4)]}, "CORTE", 7)
    p = _part([_rect(-4, -4, 4, 4), circle, txt], box(-4, -4, 4, 4))
    assert kinds(check_part(p, LIM)) == ["furo_pequeno", "peca_pequena", "texto"]


def test_rasgo_sem_compensacao():
    slot = _rect(-1.5, 0, 1.5, 20)                                    # 3,00 mm = espessura nominal
    p = _part([_rect(-30, -20, 30, 40), slot], box(-30, -20, 30, 40))
    assert "rasgo_sem_compensacao" in kinds(check_part(p, LIM))
    ok = _part([_rect(-30, -20, 30, 40), _rect(-1.425, 0, 1.425, 20)], box(-30, -20, 30, 40))
    assert "rasgo_sem_compensacao" not in kinds(check_part(ok, LIM))  # 2,85 = 3 − kerf: compensado


def test_maior_que_a_chapa():
    p = _part([_rect(-350, -50, 350, 50)], box(-350, -50, 350, 50))
    assert kinds(check_part(p, LIM)) == ["maior_que_chapa"]


def test_selo_e_store(tmp_path):
    p = _part([_rect(-4, -4, 4, 4)], box(-4, -4, 4, 4))
    small = check_part(p, LIM)
    assert seal_for(["MDF 3mm"]).status == "ok"
    s = seal_for(["MDF 3mm"], small)
    assert s.status == "atencao" and "peça pequena" in s.reasons[0]
    b = seal_for(["PVC 2mm", "MDF 3mm"], small)
    assert b.status == "bloqueado" and any("cloro" in r for r in b.reasons)
    assert seal_for(["MDF"], non_dxf=1).status == "atencao"
    assert seal_for(["MDF"], lookup=lambda m: (True, "proibido no lab")).status == "bloqueado"
    store = SealStore(str(tmp_path / "selos.json"))
    store.put_many({"8787": b, 8790: Seal()})
    assert store.get("8787").status == "bloqueado" and store.get("8790").status == "ok"
    assert store.get("1") is None


def test_avisos_na_janela_e_selo_no_lote(tmp_path):
    from app.ui.main_window import MainWindow
    from app.core.manufacturability import default_seal_path
    w = MainWindow(workers=0)
    thin = _part([_rect(-30, -20, 30, 20), _rect(0, -10, 29, 10)], box(-30, -20, 30, 20),
                 [box(0, -10, 29, 10)], pid="A", tag="8787")
    pvc = _part([_rect(-30, -20, 30, 20)], box(-30, -20, 30, 20), material="PVC", pid="B", tag="8790")
    w.parts = [thin, pvc]
    w.pmap = {p.id: p for p in w.parts}
    w.request_info = {"batch": True, "materials": ["MDF 3mm", "PVC"],
                      "requests": [{"code": "8787", "nome": "A"}, {"code": "8790", "nome": "B"}]}
    w._rebuild_checker()
    w.parts_panel.set_parts(w.parts, w.too_big)
    w._refresh_cut_panel()
    assert [i.kind for i in w.part_issues["A"]] == ["parede_fina"]
    assert "A" in w.canvas.issue_geoms
    row = next(r for r in w.parts_panel.rows if r.part.id == "A")
    assert any("Parede mais fina" in x for x in row.warns)
    assert w.request_seals["8787"].status == "atencao" and w.request_seals["8790"].status == "bloqueado"
    assert w.parts_panel._req_buttons["8790"].text().startswith("⛔")
    assert SealStore(default_seal_path()).get("8790").status == "bloqueado"
    # a tela pinta o trecho fino em laranja
    w.placements = [Placement("A", 0, 0, 60, 60, 0)]
    w.n_sheets = 1
    w.tabs.setCurrentIndex(1)
    w._redraw()
    assert w.canvas.issue_path("A") is not None and not w.canvas.issue_path("A").isEmpty()
    from app.ui.intranet import IntranetDialog
    assert IntranetDialog._known_seals()["8787"].status == "atencao"
    w.dirty = False
    w.close()
