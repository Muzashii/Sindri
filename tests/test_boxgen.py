"""Gerador de caixas: as peças montadas precisam preencher as paredes sem sobrepor nada."""
import ezdxf
import numpy as np
import pytest
import shapely

from app.core.boxgen import (JOINT_FINGER, JOINT_FLAT, LID_CHEST, LID_CLOSED, LID_DOORS, LID_LIFT,
                             LID_SLIDE, LID_TYPES, BoxParams, finger_segments, generate, resolve_dims, validate,
                             write_dxf)


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
    p = BoxParams(width=90, depth=72, height=48, thickness=3, finger=9, lid=lid, cols=cols, rows=rows,
                  joint=joint, divider_clearance=0, lid_clearance=0 if lid == LID_LIFT else 0.75,
                  finger_hole=0, lid_height=18)
    r = generate(p)
    cnt, (X, Y, Z) = occupancy(r, 0.75, holes=False)
    assert cnt.max() == 1, "duas peças ocupam o mesmo lugar"
    d, t, c = r.dims, p.thickness, p.lid_clearance
    body = (X > 0) & (X < d.W) & (Y > 0) & (Y < d.D) & (Z > 0) & (Z < d.H)
    shell = body & ((X < t) | (X > d.W - t) | (Y < t) | (Y > d.D - t) | (Z < t))
    if lid == LID_CLOSED:
        shell |= body & (Z > d.H - t)
    if lid == LID_SLIDE:
        # a frente para abaixo do rasgo; o rasgo das laterais fica de fora da conferência
        shell &= ~((Y < t) & (Z > d.front_h))
        shell &= ~((Z > d.front_h) & (Z < d.front_h + t + c) & (Y < d.D - 2 * t))
    assert cnt[shell].min() == 1, "faltou material numa parede (dente e vão não se completam)"
    if lid in (LID_LIFT, LID_CHEST):
        lid_top = (Z > d.H) & (Z < d.H + t)
        if lid == LID_LIFT:
            lid_top &= (X > 0) & (X < d.W) & (Y > 0) & (Y < d.D)
        else:
            lid_top &= (X > -(t + c)) & (X < d.W + t + c) & (Y > -(t + c)) & (Y < d.D)
        assert cnt[lid_top].min() == 1         # placa de cima da tampa inteira (dentes completos)
    if lid == LID_DOORS:
        lw = d.W / 2 - c / 2
        doors = (Z > d.H) & (Z < d.H + t) & (Y > -(t + c)) & (Y < d.D + t + c) & \
            ((X < lw) | (X > d.W / 2 + c / 2)) & (X > 0) & (X < d.W)
        assert cnt[doors].min() == 1


@pytest.mark.parametrize("lid", [LID_CHEST, LID_DOORS])
def test_pinos_da_dobradica_alinhados(lid):
    r = generate(BoxParams(width=120, depth=90, height=55, lid=lid))
    axis = 0 if lid == LID_CHEST else 1                 # baú gira em torno de x; portas, de y
    lid_holes = [pn.to3d(u, v, 0) for pn in r.panels if pn.kind in ("bau_lado", "porta_aba")
                 for u, v, _ in pn.circles]
    body_holes = [pn.to3d(u, v, 0) for pn in r.panels if pn.kind in ("esquerda", "direita", "frente", "fundo")
                  for u, v, _ in pn.circles]
    assert len(lid_holes) == len(body_holes) == (2 if lid == LID_CHEST else 4)
    key = lambda q: tuple(round(q[k], 6) for k in range(3) if k != axis)  # noqa: E731
    assert sorted(map(key, lid_holes)) == sorted(map(key, body_holes))
    for pn in r.panels:
        if pn.kind in ("bau_lado", "porta_aba", "bau_topo", "bau_frente", "porta_topo"):
            assert pn.motion and pn.motion[0] == "gira"


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
    bp.lid_tiles[LID_CHEST].click()
    bp.regenerate()
    assert bp.params().lid == LID_CHEST and bp.result is not None
    assert bp.lid_h.isVisibleTo(bp) and not bp.hole.isVisibleTo(bp) and bp.open_slider.value() > 0
    bp.view3d.grab()
    # divisórias pelo botão "3 × 2" e junta lisa
    bp.grid_tiles[(3, 2)].click()
    bp.joint_tiles[JOINT_FLAT].click()
    bp.regenerate()
    assert (bp.params().cols, bp.params().rows, bp.params().joint) == (3, 2, JOINT_FLAT)
    assert not bp.finger.isVisibleTo(bp)
    # material pelo chip
    bp.mat_btns[1].click()
    assert bp.t.value() == 6 and bp.params().material == "MDF 6mm"
    bp.set_params(BoxParams(width=120, depth=90, height=50, cols=2, rows=1, quantity=2, lid=LID_CLOSED))
    bp.regenerate()
    # medida inválida: avisa e desliga os botões
    bp.t.setValue(15)                                  # dente (10 mm) menor que a espessura
    bp.regenerate()
    assert bp.result is None and not bp.btn_send.isEnabled() and bp.msg_box.isVisibleTo(bp)
    bp.t.setValue(3)
    bp.regenerate()
    monkeypatch.setattr(w, "start_nest", lambda: None)
    assert w.send_box_to_nest()
    assert w.mode_stack.currentIndex() == 0
    assert sum(pt.quantity for pt in w.parts) == 2 * bp.result.count()
    assert {pt.material for pt in w.parts} == {"MDF 3mm"}
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
@pytest.mark.parametrize("H,hl,D", [(60, 20, 90), (45, 30, 40), (100, 30, 200)])
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
