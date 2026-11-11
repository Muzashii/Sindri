"""Micro-pontes (tabs) e compensação de kerf por peça: exporta, reimporta o DXF e confere."""
import math

import ezdxf
import pytest
from shapely.geometry import Polygon

from app.core import kerf as kerf_mod
from app.core import tabs as tabs_mod
from app.core.dxf_export import export_all_sheets, export_cut_files, export_sheets
from app.core.models import NestParams, Part, Placement, Prim
from app.core.overlap import remove_overlaps


def _poly_pts(prims):
    from app.core.geometry import flatten_prim
    pts = []
    for p in prims:
        pts.extend(map(tuple, flatten_prim(p, 0.01)[:-1]))
    return pts


def make_part(pid, outer_prims, inner_prims=(), outline=None, material="MDF 3mm", **kw):
    prims = list(outer_prims) + list(inner_prims)
    poly = Polygon(outline if outline is not None else _poly_pts(outer_prims))
    return Part(pid, pid, "mem.dxf", poly, [], prims, list(range(len(outer_prims))), material=material, **kw)


def square_lines(w=50.0, h=50.0):
    c = [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]
    return [Prim("LINE", {"s": list(c[i]), "e": list(c[(i + 1) % 4])}) for i in range(4)]


def square_poly(w=50.0, h=50.0):
    c = [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]
    return [Prim("POLY", {"pts": [[x, y, 0.0] for x, y in c], "closed": True})]


def rounded_poly(w=40.0, h=30.0, r=6.0):
    b = math.tan(math.pi / 8)
    x, y = -w / 2, -h / 2
    pts = [[x + r, y, 0], [x + w - r, y, b], [x + w, y + r, 0], [x + w, y + h - r, b],
           [x + w - r, y + h, 0], [x + r, y + h, b], [x, y + h - r, 0], [x, y + r, b]]
    return [Prim("POLY", {"pts": pts, "closed": True})]


def export_one(tmp_path, part, name="t", params=None, **kw):
    params = params or NestParams(sheet_width=200, sheet_height=150)
    out = export_all_sheets([part], [Placement(part.id, 0, 0, 80, 60, 0)], params, str(tmp_path), name, **kw)
    return ezdxf.readfile(out).modelspace()


def ends_of(e):
    """Pontas de uma entidade aberta do DXF reimportado."""
    t = e.dxftype()
    if t == "LINE":
        return [(e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)]
    if t == "ARC":
        c, r = e.dxf.center, e.dxf.radius
        return [(c.x + r * math.cos(math.radians(a)), c.y + r * math.sin(math.radians(a)))
                for a in (e.dxf.start_angle, e.dxf.end_angle)]
    if t == "LWPOLYLINE":
        assert not e.closed
        pts = list(e.get_points("xy"))
        return [pts[0], pts[-1]]
    raise AssertionError(t)


def gaps_of(msp):
    """Aberturas do contorno: pares de pontas soltas mais próximas (distância entre elas)."""
    pts = [p for e in msp for p in ends_of(e)]
    loose = [p for p in pts if sum(1 for q in pts if math.dist(p, q) < 1e-6) == 1]
    out, rest = [], list(loose)
    while rest:
        a = rest.pop()
        b = min(rest, key=lambda q: math.dist(a, q))
        rest.remove(b)
        out.append((a, b, math.dist(a, b)))
    return out


# ------------------------------------------------------------------------------------------- micro-pontes
@pytest.mark.parametrize("n", [2, 3, 4])
def test_pontes_em_quadrado_de_linhas(tmp_path, n):
    part = make_part("Q", square_lines(), tabs=n)
    stats = {}
    msp = export_one(tmp_path, part, stats=stats)
    gaps = gaps_of(msp)
    assert len(gaps) == n and stats["tabs"] == n
    assert all(abs(g[2] - 0.4) < 1e-6 for g in gaps)
    assert all(e.dxftype() == "LINE" for e in msp)
    assert len(list(msp)) == 4 + n                      # cada ponte divide uma LINE em duas
    total = sum(math.dist(*ends_of(e)) for e in msp)
    assert abs(total - (200.0 - n * 0.4)) < 1e-6        # só some o trechinho da ponte


def test_duas_pontes_ficam_em_lados_opostos_e_longe_dos_cantos(tmp_path):
    part = make_part("Q", square_lines(60, 20), tabs=2)
    gaps = gaps_of(export_one(tmp_path, part))
    mids = [((a[0] + b[0]) / 2 - 80, (a[1] + b[1]) / 2 - 60) for a, b, _ in gaps]
    assert abs(mids[0][0] + mids[1][0]) < 1e-6 and abs(mids[0][1] + mids[1][1]) < 1e-6   # opostas
    for mx, my in mids:                                  # no meio de um lado, não no canto
        assert min(abs(abs(mx) - 30), abs(abs(my) - 10)) < 1e-6
        assert abs(mx) < 1e-6 or abs(my) < 1e-6


def test_largura_da_ponte_vem_dos_parametros(tmp_path):
    part = make_part("Q", square_lines(), tabs=2)
    p = NestParams(sheet_width=200, sheet_height=150, tab_width=0.8)
    gaps = gaps_of(export_one(tmp_path, part, params=p))
    assert [round(g[2], 6) for g in gaps] == [0.8, 0.8]


def test_circulo_vira_arcos(tmp_path):
    part = make_part("C", [Prim("CIRCLE", {"c": [0.0, 0.0], "r": 15.0})], tabs=2,
                     outline=Polygon([(15 * math.cos(a / 50 * math.pi), 15 * math.sin(a / 50 * math.pi))
                                      for a in range(100)]).exterior.coords)
    msp = export_one(tmp_path, part)
    ents = list(msp)
    assert [e.dxftype() for e in ents] == ["ARC", "ARC"]          # arcos continuam arcos
    assert all(abs(e.dxf.radius - 15.0) < 1e-9 for e in ents)
    gaps = gaps_of(msp)
    assert len(gaps) == 2 and all(abs(g[2] - 0.4) < 1e-6 for g in gaps)
    sweep = sum((e.dxf.end_angle - e.dxf.start_angle) % 360 for e in ents)
    assert abs(sweep - (360 - 2 * math.degrees(2 * math.asin(0.2 / 15)))) < 1e-6


def test_arco_vira_dois_arcos_e_reta_fica_inteira(tmp_path):
    # meia-lua: uma reta curta demais para a 2ª ponte "oposta" → a outra ponte cai no arco
    prims = [Prim("LINE", {"s": [-20.0, 0.0], "e": [20.0, 0.0]}),
             Prim("ARC", {"c": [0.0, 0.0], "r": 20.0, "a0": 0.0, "a1": 180.0})]
    part = make_part("D", prims, tabs=2)
    msp = export_one(tmp_path, part)
    kinds = sorted(e.dxftype() for e in msp)
    assert kinds == ["ARC", "ARC", "LINE", "LINE"]
    gaps = gaps_of(msp)
    assert len(gaps) == 2 and all(abs(g[2] - 0.4) < 1e-6 for g in gaps)


def test_polilinha_com_bulge_mantem_os_arcos(tmp_path):
    part = make_part("R", rounded_poly(), tabs=2)
    msp = export_one(tmp_path, part)
    ents = list(msp)
    assert [e.dxftype() for e in ents] == ["LWPOLYLINE", "LWPOLYLINE"] and not any(e.closed for e in ents)
    bulges = [round(abs(b), 6) for e in ents for *_, b in e.get_points("xyb") if abs(b) > 1e-9]
    assert bulges == [round(math.tan(math.pi / 8), 6)] * 4      # os 4 cantos continuam arcos de 90°
    gaps = gaps_of(msp)
    assert len(gaps) == 2 and all(abs(g[2] - 0.4) < 1e-6 for g in gaps)


def test_geometria_original_fica_intacta_e_sem_pontes_nada_muda(tmp_path):
    part = make_part("Q", square_lines(), tabs=0)
    before = [p.to_json() for p in part.prims]
    assert len(list(export_one(tmp_path, part, "a"))) == 4
    part.tabs = 2
    export_one(tmp_path, part, "b")
    assert [p.to_json() for p in part.prims] == before


def test_ordem_gravacao_furos_contorno_continua_com_pontes(tmp_path):
    hole = Prim("CIRCLE", {"c": [0.0, 0.0], "r": 5.0})
    mark = Prim("LINE", {"s": [-10.0, 15.0], "e": [10.0, 15.0]}, color=5)
    part = make_part("Q", square_lines(), [hole, mark], tabs=2)
    msp = export_one(tmp_path, part)
    ents = list(msp)
    assert ents[0].dxftype() == "LINE" and ents[0].dxf.color == 5       # gravação primeiro
    assert ents[1].dxftype() == "CIRCLE"                                 # depois o furo (inteiro: sem ponte)
    assert all(e.dxftype() == "LINE" and e.dxf.color == 7 for e in ents[2:]) and len(ents[2:]) == 6


def test_ponte_nao_e_cortada_pela_peca_encostada(tmp_path):
    """Espaço 0: a borda da vizinha passa em cima da ponte — a remoção de trechos repetidos preserva a ponte."""
    a = make_part("A", square_lines(20, 20), tabs=4)
    b = make_part("B", square_lines(20, 20))
    params = NestParams(sheet_width=200, sheet_height=150, spacing=0)
    for order in ([("A", 30), ("B", 50)], [("B", 50), ("A", 30)]):
        pls = [Placement(pid, 0, 0, x, 30, 0) for pid, x in order]
        out = export_all_sheets([a, b], pls, params, str(tmp_path), f"viz{order[0][0]}", sort_path=False,
                                inner_first=False)
        cut_on_edge = []
        for e in ezdxf.readfile(out).modelspace():
            pts = ([(e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)] if e.dxftype() == "LINE"
                   else list(e.get_points("xy")))
            for p, q in zip(pts, pts[1:]):
                if abs(p[0] - 40) < 1e-6 and abs(q[0] - 40) < 1e-6:
                    cut_on_edge.append(tuple(sorted((p[1], q[1]))))
        cut_on_edge.sort()
        assert len(cut_on_edge) == 2, cut_on_edge                         # aresta comum: uma vez só, com a ponte
        assert abs(cut_on_edge[0][0] - 20) < 1e-6 and abs(cut_on_edge[1][1] - 40) < 1e-6
        assert abs(cut_on_edge[1][0] - cut_on_edge[0][1] - 0.4) < 1e-6


def test_pontes_valem_para_arquivo_por_placa_conferencia_e_cli(tmp_path):
    part = make_part("Q", square_lines(), tabs=2)
    pls = [Placement("Q", 0, 0, 80, 60, 90)]
    params = NestParams(sheet_width=200, sheet_height=150)
    cut = export_cut_files([part], pls, params, str(tmp_path), "c")
    assert len(gaps_of(ezdxf.readfile(cut[0]).modelspace())) == 2
    conf = export_all_sheets([part], pls, params, str(tmp_path), "c", sheet_outline=True)
    ents = [e for e in ezdxf.readfile(conf).modelspace() if e.dxf.layer != "PLACA"]
    assert len(ents) == 6
    files = export_sheets([part], pls, params, str(tmp_path / "cli"), "c")
    assert len(gaps_of(ezdxf.readfile(files[0]).modelspace())) == 2


def test_peca_sem_trecho_para_ponte_avisa(tmp_path):
    part = make_part("mini", square_lines(0.5, 0.5), tabs=2)
    stats = {}
    msp = export_one(tmp_path, part, stats=stats)
    assert len(list(msp)) == 4 and not stats.get("tabs")
    assert any("micro-ponte" in n for n in stats["notes"])


def test_part_tabs_para_a_interface():
    part = make_part("Q", square_lines(), tabs=3)
    tabs = tabs_mod.part_tabs(part, 0.4)
    assert len(tabs) == 3 and all(abs(math.dist(t.p0, t.p1) - 0.4) < 1e-9 for t in tabs)
    part.tabs = 0
    assert tabs_mod.part_tabs(part) == []


# ------------------------------------------------------------------------------------------------- kerf
def bbox_of(msp, kinds=("LINE", "LWPOLYLINE", "ARC")):
    xs, ys = [], []
    for e in msp:
        if e.dxftype() == "LINE":
            pts = [(e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)]
        elif e.dxftype() == "LWPOLYLINE":
            pts = list(e.get_points("xy"))
        else:
            continue
        xs += [p[0] for p in pts]
        ys += [p[1] for p in pts]
    return max(xs) - min(xs), max(ys) - min(ys)


@pytest.mark.parametrize("outer", [square_lines, square_poly])
def test_kerf_quadrado_50_com_furo_10(tmp_path, outer):
    hole = Prim("CIRCLE", {"c": [0.0, 0.0], "r": 5.0})
    part = make_part("Q", outer(), [hole], kerf_comp=True)
    stats = {}
    msp = export_one(tmp_path, part, material_kerf={"MDF 3mm": 0.2}, stats=stats)
    w, h = bbox_of(msp)
    assert abs(w - 50.2) < 1e-6 and abs(h - 50.2) < 1e-6           # externo para fora (kerf/2 de cada lado)
    circles = [e for e in msp if e.dxftype() == "CIRCLE"]
    assert len(circles) == 1 and abs(circles[0].dxf.radius * 2 - 9.8) < 1e-9   # furo para dentro, ainda círculo
    assert abs(circles[0].dxf.center.x - 80) < 1e-9 and abs(circles[0].dxf.center.y - 60) < 1e-9
    others = [e.dxftype() for e in msp if e.dxftype() != "CIRCLE"]
    assert others == (["LINE"] * 4 if outer is square_lines else ["LWPOLYLINE"])   # linha continua linha
    assert stats["kerf_parts"] == 1 and not stats.get("notes")
    assert part.prims[0].kind in ("LINE", "POLY") and part.size == (50.0, 50.0)   # o desenho não mudou


def test_kerf_desligado_ou_sem_valor_nao_compensa_e_avisa(tmp_path):
    part = make_part("Q", square_lines(), kerf_comp=False)
    assert bbox_of(export_one(tmp_path, part, "a", material_kerf={"MDF 3mm": 0.2})) == (50.0, 50.0)
    part.kerf_comp = True
    stats = {}
    assert bbox_of(export_one(tmp_path, part, "b", stats=stats)) == (50.0, 50.0)
    assert any("SEM compensação" in n and "kerf medido" in n for n in stats["notes"])
    # kerf pelo material_sheets dos parâmetros (caminho alternativo ao material_kerf)
    p = NestParams(sheet_width=200, sheet_height=150, material_sheets={"MDF 3mm": {"kerf": 0.3}})
    w, _ = bbox_of(export_one(tmp_path, part, "c", params=p))
    assert abs(w - 50.3) < 1e-6


def test_kerf_arcos_continuam_arcos(tmp_path):
    part = make_part("R", rounded_poly(40, 30, 6), [Prim("POLY", {"pts": [[-5, -3, 0], [5, -3, 0], [5, 3, 0],
                                                                         [-5, 3, 0]], "closed": True})],
                     kerf_comp=True)
    msp = export_one(tmp_path, part, material_kerf={"MDF 3mm": 0.2})
    polys = sorted([e for e in msp if e.dxftype() == "LWPOLYLINE"], key=lambda e: -len(e))
    assert len(polys) == 2 and all(e.closed for e in polys)
    outer, slot = polys
    pts = list(outer.get_points("xyb"))
    assert len(pts) == 8 and sum(1 for p in pts if abs(abs(p[2]) - math.tan(math.pi / 8)) < 1e-9) == 4
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    assert abs(max(xs) - min(xs) - 40.2) < 1e-6 and abs(max(ys) - min(ys) - 30.2) < 1e-6
    # raio do canto: 6 → 6,1 (a reta entre dois cantos encurta 2 × 6,1 no total)
    bottom = sorted(p[0] for p in pts if abs(p[1] - min(ys)) < 1e-6)
    assert abs((bottom[-1] - bottom[0]) - (40.2 - 2 * 6.1)) < 1e-6
    sp = list(slot.get_points("xy"))
    assert abs(max(p[0] for p in sp) - min(p[0] for p in sp) - 9.8) < 1e-6      # rasgo 10 × 6 → 9,8 × 5,8
    assert abs(max(p[1] for p in sp) - min(p[1] for p in sp) - 5.8) < 1e-6


def test_kerf_linha_e_arco_soltos_com_canto_vivo(tmp_path):
    # meia-lua (LINE + ARC): os dois cantos vivos entre reta e arco ganham arco de raio kerf/2
    prims = [Prim("LINE", {"s": [-20.0, 0.0], "e": [20.0, 0.0]}),
             Prim("ARC", {"c": [0.0, 0.0], "r": 20.0, "a0": 0.0, "a1": 180.0})]
    part = make_part("D", prims, kerf_comp=True)
    msp = export_one(tmp_path, part, material_kerf={"MDF 3mm": 0.2})
    arcs = sorted((e.dxf.radius for e in msp if e.dxftype() == "ARC"))
    lines = [e for e in msp if e.dxftype() == "LINE"]
    assert len(lines) == 1 and [round(r, 9) for r in arcs] == [0.1, 0.1, 20.1]
    assert abs(lines[0].dxf.start.y - (60 - 0.1)) < 1e-9
    assert not gaps_of(msp)                                             # contorno fechado


def test_kerf_canto_concavo_L(tmp_path):
    c = [(0, 0), (40, 0), (40, 15), (15, 15), (15, 40), (0, 40)]
    c = [(x - 20, y - 20) for x, y in c]
    part = make_part("L", [Prim("POLY", {"pts": [[x, y, 0.0] for x, y in c], "closed": True})], kerf_comp=True)
    msp = export_one(tmp_path, part, material_kerf={"MDF 3mm": 0.2})
    (e,) = list(msp)
    got = Polygon([(x - 80, y - 60) for x, y in e.get_points("xy")])
    want = Polygon(c).buffer(0.1, join_style=2)
    assert got.symmetric_difference(want).area < 1e-9 and len(list(e.get_points("xy"))) == 6


def test_kerf_recusa_spline_e_detalhe_menor_que_o_kerf(tmp_path):
    sp = Prim("SPLINE", {"degree": 3, "ctrl": [[0, 0], [1, 2], [3, 2], [4, 0]], "knots": [], "weights": [],
                         "fit": [], "closed": False})
    part = make_part("S", square_lines(), [sp], kerf_comp=True)
    part.prims[-1].color = 7
    stats = {}
    msp = export_one(tmp_path, part, "s", material_kerf={"MDF 3mm": 0.2}, stats=stats, version="R2000")
    assert bbox_of(msp) == (50.0, 50.0) and any("SPLINE" in n for n in stats["notes"])
    assert kerf_mod.can_compensate(part)
    # furo Ø0,1 com kerf 0,2: não há como compensar → peça inteira sem compensação, com aviso
    part2 = make_part("F", square_lines(), [Prim("CIRCLE", {"c": [0.0, 0.0], "r": 0.05})], kerf_comp=True)
    stats = {}
    msp = export_one(tmp_path, part2, "f", material_kerf={"MDF 3mm": 0.2}, stats=stats)
    assert bbox_of(msp) == (50.0, 50.0) and any("menor que o kerf" in n for n in stats["notes"])
    # rasgo de 0,15 mm de largura, kerf 0,2: o deslocamento inverteria o rasgo
    slot = Prim("POLY", {"pts": [[-5, 0, 0], [5, 0, 0], [5, 0.15, 0], [-5, 0.15, 0]], "closed": True})
    part3 = make_part("G", square_lines(), [slot], kerf_comp=True)
    stats = {}
    msp = export_one(tmp_path, part3, "g", material_kerf={"MDF 3mm": 0.2}, stats=stats)
    assert any("menor que o kerf" in n for n in stats["notes"])
    assert max(e.get_points("xy")[2][1] - e.get_points("xy")[0][1] for e in msp if e.dxftype() == "LWPOLYLINE") \
        == pytest.approx(0.15)


def test_kerf_gravacao_e_vinco_nao_se_deslocam(tmp_path):
    mark = Prim("CIRCLE", {"c": [0.0, 0.0], "r": 5.0}, color=5)        # outra cor = gravação
    part = make_part("Q", square_lines(), [mark], kerf_comp=True)
    msp = export_one(tmp_path, part, material_kerf={"MDF 3mm": 0.2})
    (c,) = [e for e in msp if e.dxftype() == "CIRCLE"]
    assert c.dxf.radius == 5.0 and abs(bbox_of(msp)[0] - 50.2) < 1e-6


def test_kerf_ilha_dentro_de_furo_cresce(tmp_path):
    hole = Prim("CIRCLE", {"c": [0.0, 0.0], "r": 10.0})
    island = Prim("CIRCLE", {"c": [0.0, 0.0], "r": 4.0})
    part = make_part("Q", square_lines(), [hole, island], kerf_comp=True)
    msp = export_one(tmp_path, part, material_kerf={"MDF 3mm": 0.2})
    assert sorted(round(e.dxf.radius, 9) for e in msp if e.dxftype() == "CIRCLE") == [4.1, 9.9]


def test_kerf_com_pontes_juntos(tmp_path):
    part = make_part("Q", square_lines(), [Prim("CIRCLE", {"c": [0.0, 0.0], "r": 5.0})], kerf_comp=True, tabs=2)
    msp = export_one(tmp_path, part, material_kerf={"MDF 3mm": 0.2})
    lines = [e for e in msp if e.dxftype() == "LINE"]
    assert len(lines) == 6 and abs(bbox_of(msp)[0] - 50.2) < 1e-6
    gaps = gaps_of([e for e in msp if e.dxftype() == "LINE"])
    assert len(gaps) == 2 and all(abs(g[2] - 0.4) < 1e-6 for g in gaps)


def test_kerf_peca_girada_e_espelhada(tmp_path):
    part = make_part("Q", square_lines(50, 30), [Prim("CIRCLE", {"c": [10.0, 5.0], "r": 5.0})], kerf_comp=True)
    out = export_all_sheets([part], [Placement("Q", 0, 0, 80, 60, 90, mirrored=True)],
                            NestParams(sheet_width=200, sheet_height=150), str(tmp_path), "g",
                            material_kerf={"MDF 3mm": 0.2})
    msp = ezdxf.readfile(out).modelspace()
    w, h = bbox_of(msp)
    assert abs(w - 30.2) < 1e-6 and abs(h - 50.2) < 1e-6


def test_aviso_espacamento_menor_que_kerf(tmp_path):
    part = make_part("Q", square_lines(), kerf_comp=True)
    stats = {}
    export_one(tmp_path, part, params=NestParams(sheet_width=200, sheet_height=150, spacing=0.1),
               material_kerf={"MDF 3mm": 0.2}, stats=stats)
    assert any("Espaçamento" in n and "menor que o kerf" in n for n in stats["notes"])
    assert kerf_mod.spacing_note(2.0, 0.2) is None


def test_offset_contour_direto_cresce_e_encolhe():
    segs = tabs_mod.prim_segs(rounded_poly(40, 30, 6)[0])
    grown = kerf_mod.offset_contour(segs, 0.5)
    shrunk = kerf_mod.offset_contour(segs, -0.5)
    assert sorted({round(s.r, 9) for s in grown if s.is_arc}) == [6.5]
    assert sorted({round(s.r, 9) for s in shrunk if s.is_arc}) == [5.5]
    with pytest.raises(kerf_mod.KerfError):
        kerf_mod.offset_contour(segs, -16)                             # maior que metade da peça


# ---------------------------------------------------------------------------- projeto e fabricabilidade
def test_projeto_guarda_pontes_e_kerf_e_abre_projeto_antigo(tmp_path):
    import json
    from app.core.project import load_project, save_project
    part = make_part("Q", square_lines(), tabs=3, kerf_comp=True)
    other = make_part("P", square_lines(20, 20))
    path = str(tmp_path / "p.sindri")
    save_project(path, [], NestParams(tab_width=0.6), [part, other], None)
    pj = load_project(path)
    got = {p.id: p for p in pj.parts}
    assert (got["Q"].tabs, got["Q"].kerf_comp) == (3, True) and (got["P"].tabs, got["P"].kerf_comp) == (0, False)
    assert pj.params.tab_width == 0.6
    # projeto "antigo": sem os campos novos em lugar nenhum
    data = json.loads(open(path, encoding="utf-8").read())
    data["params"].pop("tab_width")
    for g in data["geometry"]:
        g.pop("tabs"), g.pop("kerf_comp")
    for s in data["parts"].values():
        s.pop("tabs"), s.pop("kerf_comp")
    old = str(tmp_path / "antigo.sindri")
    open(old, "w", encoding="utf-8").write(json.dumps(data))
    pj = load_project(old)
    assert all(p.tabs == 0 and p.kerf_comp is False for p in pj.parts) and pj.params.tab_width == 0.4
    # valor estragado não derruba o projeto
    data["parts"]["Q"]["tabs"] = "muitas"
    open(old, "w", encoding="utf-8").write(json.dumps(data))
    assert {p.id: p.tabs for p in load_project(old).parts}["Q"] == 0


def test_projeto_de_fixture_antiga_continua_abrindo(tmp_path):
    """Projeto salvo a partir de um DXF real, sem os campos novos (como os .sindri de antes)."""
    import json
    from app.core.part_builder import import_files
    from app.core.project import load_project, save_project
    from tests.conftest import fx
    rep = import_files([fx("exemplo_lab.dxf")])
    path = str(tmp_path / "lab.sindri")
    save_project(path, [fx("exemplo_lab.dxf")], NestParams(), rep.parts, None, report=rep)
    data = json.loads(open(path, encoding="utf-8").read())
    for g in data["geometry"]:
        g.pop("tabs"), g.pop("kerf_comp")
    for s in data["parts"].values():
        s.pop("tabs"), s.pop("kerf_comp")
    data["params"].pop("tab_width")
    open(path, "w", encoding="utf-8").write(json.dumps(data))
    pj = load_project(path)
    assert len(pj.parts) == len(rep.parts) and all(p.tabs == 0 and not p.kerf_comp for p in pj.parts)


def test_aviso_de_peca_pequena_aponta_o_botao_pontes():
    from app.core.manufacturability import MaterialLimits, check_part
    part = make_part("mini", square_lines(6, 6))
    msg = [i.message for i in check_part(part, MaterialLimits()) if i.kind == "peca_pequena"][0]
    assert "Pontes" in msg and "lista de peças" in msg
    part.tabs = 2
    msg = [i.message for i in check_part(part, MaterialLimits()) if i.kind == "peca_pequena"][0]
    assert "2 micro-pontes" in msg


def test_aviso_de_kerf_sem_valor_no_material():
    from app.core.manufacturability import MaterialLimits, check_part
    part = make_part("Q", square_lines(), kerf_comp=True)
    kinds = [i.kind for i in check_part(part, MaterialLimits(thickness=3))]
    assert "kerf_sem_compensacao" in kinds
    assert "kerf_sem_compensacao" not in [i.kind for i in check_part(part, MaterialLimits(thickness=3, kerf=0.2))]
    kinds = [i.kind for i in check_part(part, MaterialLimits(thickness=3, kerf=0.2, spacing=0.1))]
    assert "kerf_espacamento" in kinds


def test_remove_overlaps_sem_pontes_continua_igual():
    a = Prim("LINE", {"s": [0, 0], "e": [10, 0]})
    b = Prim("LINE", {"s": [0, 0], "e": [10, 0]})
    out, n = remove_overlaps([a, b])
    assert out == [a] and n == 1


# ------------------------------------------------------------------------------------------- interface
def test_painel_de_pecas_tem_pontes_e_kerf():
    from PySide6.QtWidgets import QApplication
    from app.ui.parts_panel import PartsPanel
    _app = QApplication.instance() or QApplication([])
    part = make_part("Q", square_lines(), tabs=0)
    other = make_part("P", square_lines(20, 20), tabs=3, kerf_comp=True)
    panel = PartsPanel()
    panel.set_parts([part, other])
    got = []
    panel.tabsChanged.connect(lambda pid, n: got.append(("tabs", pid, n)))
    panel.kerfCompChanged.connect(lambda pid, on: got.append(("kerf", pid, on)))
    row, row2 = panel.rows
    assert sorted(row.tab_actions) == [0, 2, 3, 4] and row.tab_actions[0].isChecked()
    assert row.cut_btn.text() == "Pontes" and row2.cut_btn.text() == "Pontes 3 · kerf"
    assert row2.tab_actions[3].isChecked() and row2.kerf_action.isChecked()
    row.tab_actions[2].trigger()
    row.kerf_action.trigger()
    assert got == [("tabs", "Q", 2), ("kerf", "Q", True)]
    assert row.cut_btn.text() == "Pontes 2 · kerf"
    assert "de propósito" in row.kerf_action.toolTip()
    assert part.tabs == 0 and part.kerf_comp is False        # quem grava na peça é a janela (com desfazer)
    panel.set_cut_options("Q", 0, False)                      # ex.: depois de desfazer — sem emitir sinal
    assert row.cut_btn.text() == "Pontes" and len(got) == 2
    panel.deleteLater()


def test_janela_grava_pontes_e_kerf_com_desfazer_e_kerf_do_banco(tmp_path, monkeypatch):
    """Os botões do painel chegam à peça (com desfazer), o kerf vem do banco de materiais e a impressão
    digital da placa muda — o arquivo exportado antes deixa de valer."""
    from PySide6.QtWidgets import QApplication, QMessageBox
    from app.core import material_db
    from app.ui.main_window import MainWindow
    from tests.conftest import fx
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Discard))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.Discard))
    w = MainWindow(workers=0)
    try:
        w.load_files([fx("exemplo_lab.dxf")])
        part = w.parts[0]
        part.material = "MDF 3mm"
        db = w.material_db()
        m = db.find("MDF 3mm") or db.add(material_db.Material(name="MDF 3mm"))
        m.kerf = 0.15
        db.save()
        w.__dict__.pop("_mdb", None)
        assert w._material_kerf() == {"MDF 3mm": 0.15}
        assert w._export_kwargs({"version": "R2000", "inner": True, "path": True},
                                {"numbers": {"color": 1}, "cmap": {}, "labels": {}, "op_colors": {},
                                 "color_ops": {}}, {})["material_kerf"] == {"MDF 3mm": 0.15}

        row = next(r for r in w.parts_panel.rows if r.part.id == part.id)
        row.tab_actions[3].trigger()
        row.kerf_action.trigger()
        assert part.tabs == 3 and part.kerf_comp is True and w.dirty
        w.undo()
        assert part.kerf_comp is False and part.tabs == 3
        w.undo()
        row = next(r for r in w.parts_panel.rows if r.part.id == part.id)      # o painel pode ter sido refeito
        assert part.tabs == 0 and row.cut_btn.text() == "Pontes"
        w.redo()
        assert part.tabs == 3

        part.material = "Papelão"                              # sem kerf no banco: avisa na hora
        row = next(r for r in w.parts_panel.rows if r.part.id == part.id)
        row.kerf_action.trigger()
        assert "kerf cadastrado" in w.banner_text.text()
    finally:
        w.close()
