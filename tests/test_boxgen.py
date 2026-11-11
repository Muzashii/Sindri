"""Gerador de caixas: as peças montadas precisam preencher as paredes sem sobrepor nada."""
import ezdxf
import numpy as np
import pytest
import shapely

from app.core.boxgen import (JOINT_FINGER, JOINT_FLAT, LID_CHEST, LID_CLOSED, LID_DOORS, LID_LIFT,
                             LID_SLIDE, LID_TYPES, BoxParams, finger_segments, generate, hinge_geom, resolve_dims,
                             validate, write_dxf)


def occupancy(result, res: float, holes: bool = True):
    """Conta quantas peças ocupam cada voxel (centro do voxel dentro da peça).

    ``holes=False`` ignora os furos redondos (pinos, dedo) para conferir só os encaixes."""
    t = result.params.thickness
    pts = [pn.to3d(x, y, s) for pn in result.panels for x, y in (pn.poly.bounds[:2], pn.poly.bounds[2:])
           for s in (0, t)]
    lo = np.floor(np.min(pts, axis=0) / res) * res
    hi = np.max(pts, axis=0)
    axes = [np.arange(lo[k] + res / 2, hi[k], res) for k in range(3)]
    X, Y, Z = np.meshgrid(*axes, indexing="ij")
    P = np.stack([X.ravel(), Y.ravel(), Z.ravel()], axis=1)
    count = np.zeros(len(P), dtype=np.int16)
    for pn in result.panels:
        o, U, V, N = (np.array(a, float) for a in (pn.origin, pn.U, pn.V, pn.N))
        rel = P - o
        u, v, s = rel @ U, rel @ V, rel @ N
        inside = (s > 0) & (s < t)
        idx = np.nonzero(inside)[0]
        hit = shapely.contains_xy(pn.poly, u[idx], v[idx])
        if holes:
            for cu, cv, r in pn.circles:
                hit &= (u[idx] - cu) ** 2 + (v[idx] - cv) ** 2 > r * r
        count[idx[hit]] += 1
    return count.reshape(X.shape), (X, Y, Z)


@pytest.mark.parametrize("lid", LID_TYPES)
@pytest.mark.parametrize("cols,rows", [(1, 1), (3, 2)])
@pytest.mark.parametrize("joint", [JOINT_FINGER, JOINT_FLAT])
def test_montagem_sem_sobreposicao_e_sem_buracos(lid, cols, rows, joint):
    if joint == JOINT_FLAT and (cols, rows) != (1, 1):
        pytest.skip("divisórias não dependem do tipo de junta")
    p = BoxParams(width=90, depth=72, height=56 if lid in (LID_CHEST, LID_DOORS) else 48, thickness=3, finger=9,
                  lid=lid, cols=cols, rows=rows,
                  joint=joint, divider_clearance=0, lid_clearance=0 if lid == LID_LIFT else 0.75,
                  finger_hole=0, lid_height=20)
    r = generate(p)
    cnt, (X, Y, Z) = occupancy(r, 0.75, holes=False)
    assert cnt.max() == 1, "duas peças ocupam o mesmo lugar"
    d, t, c = r.dims, p.thickness, p.lid_clearance
    body = (X > 0) & (X < d.W) & (Y > 0) & (Y < d.D) & (Z > 0) & (Z < d.H)
    shell = body & ((X < t) | (X > d.W - t) | (Y < t) | (Y > d.D - t) | (Z < t))
    if lid == LID_CLOSED:
        shell |= body & (Z > d.H - t)
    if lid in (LID_CHEST, LID_DOORS):
        # perto da dobradiça (parede mais baixa, recorte da diagonal e do nó) fica de fora da conferência
        hg = hinge_geom(t, p.kerf, p.pivot)
        zp = d.H - hg["drop"]
        zc = zp - hg["Rk"] - hg["g"] - 1.5
        far = t / 2 + hg["reach"] + hg["Rk"] + 1
        if lid == LID_CHEST:
            near = (Y > d.D - far) & (Z > zc)
            hw = (Y > d.D - t) & (Z > zc)
        else:
            near = ((X < far) | (X > d.W - far)) & (Z > zc)
            hw = ((X < t) | (X > d.W - t)) & (Z > zc)
        shell &= ~near & ~hw
    if lid == LID_SLIDE:
        # a frente para abaixo do rasgo; o rasgo das laterais fica de fora da conferência
        shell &= ~((Y < t) & (Z > d.front_h))
        shell &= ~((Z > d.front_h) & (Z < d.front_h + t + c) & (Y < d.D - 2 * t))
    assert cnt[shell].min() == 1, "faltou material numa parede (dente e vão não se completam)"
    if lid == LID_LIFT:
        lid_top = (Z > d.H) & (Z < d.H + t) & (X > 0) & (X < d.W) & (Y > 0) & (Y < d.D)
        assert cnt[lid_top].min() == 1         # placa de cima da tampa inteira
    if lid in (LID_CHEST, LID_DOORS):
        hg = hinge_geom(t, p.kerf, p.pivot)
        top = (Z > d.total_h - t) & (Z < d.total_h) & (X > 0) & (X < d.W) & (Y > 0) & (Y < d.D)
        if lid == LID_DOORS:
            la = d.W / 2 - hg["g"] / 2
            top &= (X < la) | (X > d.W - la)
        assert cnt[top].min() == 1             # tampo da tampa inteiro (dentes completos com as paredes)
        # paredes da tampa (no plano das do corpo) completas
        walls = (Z > d.H + hg["g"]) & (Z < d.total_h)
        if lid == LID_CHEST:
            lid_walls = walls & ((X < t) | (X > d.W - t) | (Y < t) | (Y > d.D - t)) & \
                (X > 0) & (X < d.W) & (Y > 0) & (Y < d.D)
        else:
            la = d.W / 2 - hg["g"] / 2
            lid_walls = walls & ((Y < t) | (Y > d.D - t) | (X < t) | (X > d.W - t)) & (X > 0) & (X < d.W) & \
                (Y > 0) & (Y < d.D) & ((X < la) | (X > d.W - la))
        assert cnt[lid_walls].min() == 1


@pytest.mark.parametrize("lid", [LID_CHEST, LID_DOORS])
@pytest.mark.parametrize("pivot", [0.0, 16.0])
def test_dobradica_tipo_makercase(lid, pivot):
    """Como no MakerCase: o nó com furo é da lateral da TAMPA (desce em diagonal até ele); o disco fica no
    centro do furo, preso numa lingueta da parede da CAIXA, e não gira — a tampa gira em volta dele."""
    r = generate(BoxParams(width=150, depth=100, height=70, lid=lid, pivot=pivot))
    t = r.params.thickness
    hg = hinge_geom(t, 0.0, pivot)
    discs = [pn for pn in r.panels if pn.kind == "disco"]
    assert len(discs) == (2 if lid == LID_CHEST else 4)
    lid_sides = [pn for pn in r.panels if pn.kind.endswith("_lado")]
    holes = [ls.to3d(h.centroid.x, h.centroid.y, t / 2) for ls in lid_sides
             for h in map(shapely.Polygon, ls.poly.interiors)]
    assert len(holes) == len(discs)
    ends = [pn for pn in r.panels if pn.kind in (("fundo",) if lid == LID_CHEST else ("esquerda", "direita"))]
    for disc in discs:
        assert disc.motion is None                       # o disco é da caixa: não se mexe
        c = np.array(disc.to3d(disc.poly.centroid.x, disc.poly.centroid.y, t / 2))
        # centro do disco = centro do furo do nó da tampa
        assert min(np.linalg.norm(c - np.array(h)) for h in holes) < 1e-6, "disco fora do nó"
        # o furo retangular do disco é ocupado pela lingueta da parede da caixa
        slot = shapely.Polygon(disc.poly.interiors[0])
        sc = slot.centroid
        q = np.array(disc.to3d(sc.x, sc.y, t / 2))
        inside = 0
        for e in ends:
            o, U, V, N = (np.array(a, float) for a in (e.origin, e.U, e.V, e.N))
            rel = q - o
            if 0 < rel @ N < t and e.poly.contains(shapely.Point(rel @ U, rel @ V)):
                inside += 1
        assert inside == 1, "a lingueta da parede da caixa não entra no disco"
        assert slot.area == pytest.approx(t * hg["hs"], rel=1e-3)
    # a tampa gira em volta do centro dos discos
    for ls in lid_sides:
        assert ls.motion and ls.motion[0] == "gira"
        pivot3 = np.array(ls.motion[1])
        axis = np.array(ls.motion[2])
        dists = []
        for disc in discs:
            c = np.array(disc.to3d(disc.poly.centroid.x, disc.poly.centroid.y, t / 2))
            off = c - pivot3
            dists.append(np.linalg.norm(off - axis * (off @ axis)))
        assert min(dists) < 1e-6
    # o nó passa um pouco da face, e a lateral da caixa não tem ponta fina perto do nó
    for pn in r.panels:
        if pn.kind in ("esquerda", "direita", "frente", "fundo"):
            assert pn.poly.buffer(-0.4).geom_type == "Polygon", f"{pn.name} tem ponta fina"


def test_deslizante_frente_mais_baixa_e_rasgo():
    r = generate(BoxParams(width=120, depth=90, height=60, lid=LID_SLIDE))
    d = r.dims
    front = next(pn for pn in r.panels if pn.kind == "frente")
    assert front.poly.bounds[3] == pytest.approx(d.front_h)
    lid = next(pn for pn in r.panels if pn.kind == "deslizante")
    assert lid.origin[2] > d.front_h and lid.motion[0] == "move"
    assert d.Hi == pytest.approx(d.front_h - 3)
    side = next(pn for pn in r.panels if pn.kind == "esquerda")
    assert side.poly.geom_type == "Polygon"
    # a faixa acima do rasgo continua na peça (não virou um pedaço solto)
    assert side.poly.bounds[3] == pytest.approx(d.H)


def test_arestas_tem_numero_impar_de_dentes():
    for L in (40, 87.3, 150, 600):
        segs = finger_segments(L, 3, 10)
        assert len(segs) % 2 == 1
        assert segs[0][0] == pytest.approx(3) and segs[-1][1] == pytest.approx(L - 3)


def test_medidas_internas():
    p = BoxParams(width=100, depth=80, height=50, inner=True, thickness=3, lid=LID_CLOSED)
    d = resolve_dims(p)
    assert (d.W, d.D, d.H) == (106, 86, 56)
    assert (d.Wi, d.Di, d.Hi) == (100, 80, 50)
    p.lid = LID_LIFT
    d = resolve_dims(p)
    assert d.Hi == 50 and d.total_h == d.H + 3


def test_validacao():
    assert validate(BoxParams()) == []
    assert validate(BoxParams(finger=2, thickness=3))
    assert validate(BoxParams(width=10, depth=10, height=10))
    assert validate(BoxParams(cols=20, width=100))
    with pytest.raises(ValueError):
        generate(BoxParams(thickness=0))


def test_kerf_aumenta_dentes():
    a = generate(BoxParams(kerf=0))
    b = generate(BoxParams(kerf=0.2))
    pa = a.panels[1].cut_poly(0)
    pb = b.panels[1].cut_poly(0.2)
    assert pb.area > pa.area


@pytest.mark.parametrize("lid", LID_TYPES)
def test_dxf_de_cada_tampa_reimporta(tmp_path, lid):
    from app.core.part_builder import import_files
    r = generate(BoxParams(width=140, depth=100, height=60, lid=lid, cols=2, rows=2))
    path = write_dxf(r, str(tmp_path / f"caixa_{lid}.dxf"))
    rep = import_files([path])
    assert sum(pt.quantity for pt in rep.parts) == r.count()
    assert not any(pt.is_open for pt in rep.parts)
    circles = sum(len(pn.circles) for pn in r.panels)
    assert len(ezdxf.readfile(path).modelspace().query("CIRCLE")) == circles


def test_dxf_vira_pecas_no_encaixe(tmp_path):
    from app.core.part_builder import import_files
    p = BoxParams(width=120, depth=90, height=50, lid=LID_LIFT, cols=2, rows=2, engrave_names=True)
    r = generate(p)
    path = write_dxf(r, str(tmp_path / "caixa.dxf"))
    doc = ezdxf.readfile(path)
    assert doc.header["$INSUNITS"] == 4
    assert len(doc.modelspace().query("CIRCLE")) == 2          # furo do dedo na tampa e na guia
    rep = import_files([path])
    total = sum(pt.quantity for pt in rep.parts)
    assert total == r.count()
    assert not any(pt.is_open for pt in rep.parts)
    assert len(doc.modelspace().query("TEXT")) == r.count()
    # frente/fundo e as laterais (mesmo com o nome gravado) viram grupos com quantidade 2
    assert sorted(pt.quantity for pt in rep.parts).count(2) >= 2


def _pump(app, secs):
    import time
    end = time.time() + secs
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def test_aba_caixa_envia_para_o_encaixe(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QMessageBox

    from app.ui.main_window import MainWindow
    from tests.conftest import fx
    app = QApplication.instance()
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: QMessageBox.Ok))
    w = MainWindow(workers=0)
    w.show()
    w.set_mode(2)
    assert w.mode_stack.currentWidget() is w.box_panel and not w.nest_actions.isVisibleTo(w)
    bp = w.box_panel
    # modelos: cada um mostra só os seus passos (numerados em sequência)
    bp.model_tiles[MODEL_ELEC].click()
    bp.regenerate()
    assert bp.steps["placa"].isVisibleTo(bp) and not bp.steps["tampa"].isVisibleTo(bp)
    assert not bp.steps["divisorias"].isVisibleTo(bp)
    nums = [st.num.text() for k, st in bp.steps.items() if st.isVisibleTo(bp)]
    assert nums == [str(i) for i in range(1, len(nums) + 1)]
    bp.board_btns["uno"].click()
    bp.regenerate()
    assert bp.params().board == "uno" and any(pn.ghost for pn in bp.result.panels)
    bp.model_tiles[MODEL_KERF].click()
    bp.regenerate()
    assert bp.steps["kerf"].isVisibleTo(bp) and not bp.steps["medidas"].isVisibleTo(bp)
    bp._view_mode(1)
    assert any(isinstance(it, __import__("PySide6.QtWidgets", fromlist=["x"]).QGraphicsSimpleTextItem)
               and it.text() == "0,15" for it in bp.flat.scene().items())
    bp._view_mode(0)
    # receitas
    from app.ui.box_panel import RECIPES
    for name, values in RECIPES:
        bp.apply_recipe(values)
        assert bp.result is not None, name
        assert bp.params().model == values["model"], name
    bp.model_tiles[MODEL_DRAWER].click()
    bp.regenerate()
    assert bp.steps["gaveta"].isVisibleTo(bp) and bp.open_lbl.text() == "Abrir gaveta"
    bp.set_params(BoxParams(width=120, depth=90, height=50, cols=2, rows=1, quantity=2, lid=LID_CLOSED))
    bp.regenerate()
    assert bp.result is not None and bp.btn_send.isEnabled()
    assert "Externa" in bp.info.text()
    bp.view3d.set_explode(0.5)
    bp.view3d.grab()                                   # desenha a vista 3D sem erro
    bp._view_mode(1)
    assert len(bp.flat.scene().items()) >= bp.result.count()
    bp._view_mode(0)
    # escolher a tampa baú pelo botão ilustrado mostra as opções da dobradiça e abre a tampa na prévia
    bp.h.setValue(70)
    bp.lid_tiles[LID_CHEST].click()
    bp.regenerate()
    assert bp.params().lid == LID_CHEST and bp.result is not None
    assert bp.lid_h.isVisibleTo(bp) and not bp.hole.isVisibleTo(bp) and bp.open_slider.value() > 0
    assert bp.pivot.isVisibleTo(bp) and bp.pivot.text() == "Automático"
    bp.pivot.setValue(16)
    bp.regenerate()
    assert bp.params().pivot == 16 and bp.result is not None
    bp.view3d.grab()
    # largura do dente: chave de arrastar de 2× a 4× a espessura
    assert (bp.finger.minimum(), bp.finger.maximum()) == (2 * bp.t.value(), 4 * bp.t.value())
    bp.finger.setValue(100)
    assert bp.finger.value() == 4 * bp.t.value()       # não passa do máximo
    from PySide6.QtCore import QPointF, Qt as _Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QEvent

    def click(widget, x, y):
        for kind in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
            ev = QMouseEvent(kind, QPointF(x, y), QPointF(x, y), _Qt.LeftButton, _Qt.LeftButton, _Qt.NoModifier)
            QApplication.sendEvent(widget, ev)

    bp.finger.resize(240, 36)
    # Native numeric input replaces the custom drag-only value interface.
    from PySide6.QtTest import QTest
    bp.finger.setValue(bp.finger.minimum())
    QTest.keyClick(bp.finger, _Qt.Key_Up)
    assert bp.finger.value() == bp.finger.minimum() + bp.finger.singleStep()
    # divisórias dinâmicas: 3 × 2 e um clique tira uma divisória (e outro põe de volta)
    bp.cols.setValue(3)
    bp.rows.setValue(2)
    bp.regenerate()
    n_div = sum(pn.kind == "divisoria" for pn in bp.result.panels)
    assert n_div == 3
    ed = bp.div_editor
    ed.resize(300, 180)
    kind, i, a, b = ed._lines()[0]                     # a primeira divisória "coluna"
    click(ed, a.x(), (a.y() + b.y()) / 2)
    bp.regenerate()
    assert bp.params().cols_off == [i] and sum(pn.kind == "divisoria" for pn in bp.result.panels) == 2
    click(ed, a.x(), (a.y() + b.y()) / 2)
    bp.regenerate()
    assert bp.params().cols_off == [] and sum(pn.kind == "divisoria" for pn in bp.result.panels) == 3
    bp.joint_tiles[JOINT_FLAT].click()
    bp.regenerate()
    assert (bp.params().cols, bp.params().rows, bp.params().joint) == (3, 2, JOINT_FLAT)
    assert not bp.finger.isVisibleTo(bp)
    # material pelo chip
    bp.mat_btns[1].click()
    assert bp.t.value() == 6 and bp.params().material == "MDF 6mm"
    # modelos: cada um mostra só os seus passos (numerados em sequência)
    bp.model_tiles[MODEL_ELEC].click()
    bp.regenerate()
    assert bp.steps["placa"].isVisibleTo(bp) and not bp.steps["tampa"].isVisibleTo(bp)
    assert not bp.steps["divisorias"].isVisibleTo(bp)
    nums = [st.num.text() for k, st in bp.steps.items() if st.isVisibleTo(bp)]
    assert nums == [str(i) for i in range(1, len(nums) + 1)]
    bp.board_btns["uno"].click()
    bp.regenerate()
    assert bp.params().board == "uno" and any(pn.ghost for pn in bp.result.panels)
    bp.model_tiles[MODEL_KERF].click()
    bp.regenerate()
    assert bp.steps["kerf"].isVisibleTo(bp) and not bp.steps["medidas"].isVisibleTo(bp)
    bp._view_mode(1)
    assert any(isinstance(it, __import__("PySide6.QtWidgets", fromlist=["x"]).QGraphicsSimpleTextItem)
               and it.text() == "0,15" for it in bp.flat.scene().items())
    bp._view_mode(0)
    # receitas
    from app.ui.box_panel import RECIPES
    for name, values in RECIPES:
        bp.apply_recipe(values)
        assert bp.result is not None, name
        assert bp.params().model == values["model"], name
    bp.model_tiles[MODEL_DRAWER].click()
    bp.regenerate()
    assert bp.steps["gaveta"].isVisibleTo(bp) and bp.open_lbl.text() == "Abrir gaveta"
    bp.set_params(BoxParams(width=120, depth=90, height=50, cols=2, rows=1, quantity=2, lid=LID_CLOSED))
    bp.regenerate()
    # medida inválida: avisa e desliga os botões
    bp.w.setValue(12)                                  # caixa pequena demais para a espessura
    bp.regenerate()
    assert bp.result is None and not bp.btn_send.isEnabled() and bp.msg_box.isVisibleTo(bp)
    bp.w.setValue(120)
    bp.regenerate()
    monkeypatch.setattr(w, "start_nest", lambda: None)
    assert w.send_box_to_nest()
    assert w.mode_stack.currentIndex() == 0
    assert sum(pt.quantity for pt in w.parts) == 2 * bp.result.count()
    assert {pt.material for pt in w.parts} == {"MDF 3mm"}
    # This setup replaces the generated box; discard confirmation is covered in test_ux.
    w.dirty = False
    # com peças abertas: "Juntar" mantém o que já estava
    assert w.load_files([fx("simples.dxf")])
    assert len(w.files) == 1
    monkeypatch.setattr(w, "_ask_join_box", lambda: True)
    w.set_mode(2)
    assert w.send_box_to_nest()
    assert len(w.files) == 2 and any("simples" in f for f in w.files)
    _pump(app, 0.05)
    w.dirty = False
    w.close()


def _samples(pn, t, step=0.8):
    minx, miny, maxx, maxy = pn.poly.bounds
    us, vs = np.meshgrid(np.arange(minx + step / 2, maxx, step), np.arange(miny + step / 2, maxy, step))
    u, v = us.ravel(), vs.ravel()
    m = shapely.contains_xy(pn.poly, u, v)
    u, v = u[m], v[m]
    return np.vstack([np.array([pn.to3d(a, b, s) for a, b in zip(u, v)]) for s in (0.25 * t, 0.75 * t)])


def _inside(pn, P, t):
    o, U, V, N = (np.array(a, float) for a in (pn.origin, pn.U, pn.V, pn.N))
    rel = P - o
    u, v, s = rel @ U, rel @ V, rel @ N
    idx = np.nonzero((s > 0.02) & (s < t - 0.02))[0]
    return shapely.contains_xy(pn.poly.buffer(-0.02), u[idx], v[idx]).sum()


def _rotate(P, pivot, axis, ang):
    k, th, p0 = np.array(axis, float), np.radians(ang), np.array(pivot, float)
    V = P - p0
    return p0 + V * np.cos(th) + np.cross(k, V) * np.sin(th) + np.outer(V @ k, k) * (1 - np.cos(th))


@pytest.mark.parametrize("lid", [LID_CHEST, LID_DOORS])
@pytest.mark.parametrize("H,hl,D", [(60, 22, 90), (60, 30, 40), (100, 30, 200)])
def test_dobradica_abre_sem_bater(lid, H, hl, D):
    """Gira a tampa de 0 a 110° em volta do pino: nenhuma peça dela pode entrar no corpo da caixa."""
    r = generate(BoxParams(width=160, depth=D, height=H, lid=lid, lid_height=hl, cols=2, rows=2))
    t = r.params.thickness
    movers = [(_samples(pn, t), pn.motion) for pn in r.panels if pn.motion]
    body = [pn for pn in r.panels if not pn.motion]
    for frac in np.linspace(0.03, 1, 25):
        for P, (_, pivot, axis, ang) in movers:
            Q = _rotate(P, pivot, axis, ang * frac)
            assert sum(_inside(b, Q, t) for b in body) == 0, f"bate no corpo a {ang * frac:.0f}°"


# ------------------------------------------------------------------ modelos: gaveta, eletrônica, bandeja, kerf
from app.core.boxgen import (BOARDS, KERF_STEPS, MODEL_DRAWER, MODEL_ELEC, MODEL_KERF, MODEL_TRAY,  # noqa: E402
                             MODELS)

MODEL_CASES = [
    BoxParams(model=MODEL_DRAWER, width=150, depth=120, height=70, cols=2, rows=2, divider_clearance=0),
    BoxParams(model=MODEL_DRAWER, width=150, depth=120, height=70, joint=JOINT_FLAT, pull="furo"),
    BoxParams(model=MODEL_ELEC, width=120, depth=90, height=50, board="uno", cable_hole=8, vents=True),
    BoxParams(model=MODEL_ELEC, width=200, depth=160, height=60, board="rpi"),
    BoxParams(model=MODEL_TRAY, width=150, depth=120, height=40, cols=3, rows=2, ramp=True, handles=True,
              divider_clearance=0),
    BoxParams(model=MODEL_KERF),
]


@pytest.mark.parametrize("p", MODEL_CASES, ids=lambda p: f"{p.model}-{p.joint}")
def test_modelos_montam_sem_sobreposicao(p):
    r = generate(p)
    real = replace_ghost(r)
    cnt, (X, Y, Z) = occupancy(real, 0.6, holes=False)
    assert cnt.max() == 1, "duas peças ocupam o mesmo lugar"


def replace_ghost(r):
    """A placa fantasma (só para ver) não entra na conferência de sobreposição."""
    import copy
    rr = copy.copy(r)
    rr.panels = r.cut_panels
    return rr


def test_gaveta_cabe_no_movel_e_desliza():
    p = BoxParams(model=MODEL_DRAWER, width=150, depth=120, height=70, cols=2, rows=1)
    r = generate(p)
    d, t, c = r.dims, p.thickness, p.lid_clearance
    drawer = [pn for pn in r.cut_panels if pn.motion]
    shell = [pn for pn in r.cut_panels if not pn.motion]
    assert len(shell) == 5 and all(pn.motion[0] == "move" and pn.motion[1][1] < 0 for pn in drawer)
    # a caixa da gaveta (sem a frente de acabamento) fica dentro do móvel com folga dos lados e em cima
    box = [pn for pn in drawer if pn.kind != "frente_falsa"]
    pts = np.array([pn.to3d(x, y, s) for pn in box for x, y in pn.poly.exterior.coords for s in (0, t)])
    assert pts[:, 0].min() >= t + c - 1e-6 and pts[:, 0].max() <= d.W - t - c + 1e-6
    assert pts[:, 2].min() >= t - 1e-6 and pts[:, 2].max() <= d.H - t - c + 1e-6
    assert pts[:, 1].min() >= -1e-6 and pts[:, 1].max() <= d.D - t - c + 1e-6
    # o puxador vazado atravessa a frente de acabamento e a frente da gaveta no mesmo lugar
    ff = next(pn for pn in drawer if pn.kind == "frente_falsa")
    gf = next(pn for pn in drawer if pn.name == "Gaveta: frente")
    c1 = ff.poly.interiors[0].centroid
    c2 = gf.poly.interiors[0].centroid
    assert np.allclose(ff.to3d(c1.x, c1.y)[::2], gf.to3d(c2.x, c2.y)[::2])
    # medidas internas = espaço útil da gaveta
    q = BoxParams(model=MODEL_DRAWER, width=100, depth=80, height=40, inner=True)
    assert (resolve_dims(q).Wi, resolve_dims(q).Di, resolve_dims(q).Hi) == (100, 80, 40)


def test_eletronica_parafusos_alinhados_e_placa():
    p = BoxParams(model=MODEL_ELEC, width=160, depth=100, height=50, board="uno", cable_hole=8)
    r = generate(p)
    t = p.thickness
    lid = next(pn for pn in r.panels if pn.kind == "tampa_parafusada")
    walls = [pn for pn in r.panels if pn.kind in ("frente", "fundo", "esquerda", "direita")]
    assert len(lid.circles) == 6                        # 2 nas paredes longas (160 mm), 1 nas curtas
    for u, v, rr in lid.circles:
        x, y, z = lid.to3d(u, v, 0)
        # embaixo de cada furo da tampa há um rasgo do parafuso em alguma parede (centro da espessura)
        hits = 0
        for w in walls:
            o, U, V, N = (np.array(a, float) for a in (w.origin, w.U, w.V, w.N))
            rel = np.array([x, y, z - 2]) - o
            uu, vv, ss = rel @ U, rel @ V, rel @ N
            if 0 < ss < t and not w.poly.contains(shapely.Point(uu, vv)) and w.poly.bounds[0] < uu < w.poly.bounds[2]:
                hits += 1
        assert hits == 1, "furo da tampa sem rasgo de parafuso embaixo"
    base = next(pn for pn in r.panels if pn.kind == "base")
    ghost = next(pn for pn in r.panels if pn.ghost)
    assert ghost not in r.cut_panels and len(base.circles) == len(BOARDS["uno"][3])
    for u, v, rr in base.circles:                       # furos da placa dentro do espaço útil
        assert t + rr < u < r.dims.W - t - rr and t + rr < v < r.dims.D - t - rr


def test_teste_de_kerf():
    r = generate(BoxParams(model=MODEL_KERF, thickness=3, kerf=0.2))
    assert r.params.kerf == 0                            # o teste é cortado sem compensação
    comb = next(pn for pn in r.panels if pn.kind == "pente")
    assert [tx[2] for tx in comb.texts] == [f"{k:.2f}".replace(".", ",") for k in KERF_STEPS]
    # largura de cada rasgo = espessura − k
    top = comb.poly.bounds[3]
    line = shapely.LineString([(0.5, top - 1), (comb.poly.bounds[2] - 0.5, top - 1)])
    gaps = line.difference(comb.poly)
    widths = sorted(round(g.length, 3) for g in getattr(gaps, "geoms", [gaps]) if g.length < 10)
    assert widths == sorted(round(3 - k, 3) for k in KERF_STEPS)


@pytest.mark.parametrize("p", MODEL_CASES, ids=lambda p: f"{p.model}-{p.joint}")
def test_dxf_de_cada_modelo_reimporta(tmp_path, p):
    from app.core.part_builder import import_files
    r = generate(p)
    path = write_dxf(r, str(tmp_path / f"{p.model}.dxf"))
    rep = import_files([path])
    assert sum(pt.quantity for pt in rep.parts) == r.count()
    assert not any(pt.is_open for pt in rep.parts)
    msp = ezdxf.readfile(path).modelspace()
    assert len(msp.query("CIRCLE")) == sum(len(pn.circles) for pn in r.cut_panels)
    assert len(msp.query("TEXT")) == sum(len(pn.texts) for pn in r.cut_panels)


def test_todos_os_modelos_tem_nome_e_validam_padrao():
    for m in MODELS:
        assert validate(BoxParams(model=m)) == [], m


@pytest.mark.parametrize("cols_off,rows_off", [([2], []), ([], [1]), ([1, 3], [2]), ([1, 2, 3], [1, 2])])
def test_divisorias_tiradas(cols_off, rows_off):
    """Divisórias tiradas no editor: as outras continuam montando sem sobrepor, e os dentes da base
    só existem embaixo das que ficaram."""
    p = BoxParams(model=MODEL_TRAY, width=160, depth=120, height=40, cols=4, rows=3, cols_off=cols_off,
                  rows_off=rows_off, ramp=True, divider_clearance=0)
    r = generate(p)
    divs = [pn for pn in r.panels if pn.kind == "divisoria"]
    assert len(divs) == (3 - len(cols_off)) + (2 - len(rows_off))
    cnt, _ = occupancy(r, 0.6, holes=False)
    assert cnt.max() == 1
    ramps = [pn for pn in r.panels if pn.kind == "rampa"]
    assert len(ramps) == (4 - len(cols_off)) * (3 - len(rows_off))   # compartimentos juntados
    base = next(pn for pn in r.panels if pn.kind == "base")
    assert len(base.poly.interiors) >= len(divs)       # pelo menos um furo de dente por divisória
    assert validate(BoxParams.from_json(p.to_json())) == []   # salva e volta igual
