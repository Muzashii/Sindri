"""Gerador de caixas: as peças montadas precisam preencher as paredes sem sobrepor nada."""
import ezdxf
import numpy as np
import pytest
import shapely

from app.core.boxgen import (LID_CLOSED, LID_LIFT, LID_OPEN, BoxParams, finger_segments, generate, resolve_dims,
                             validate, write_dxf)


def occupancy(result, res: float):
    """Conta quantas peças ocupam cada voxel (centro do voxel dentro da peça)."""
    d = result.dims
    t = result.params.thickness
    nx, ny, nz = (int(round(v / res)) for v in (d.W, d.D, d.total_h))
    xs = (np.arange(nx) + 0.5) * res
    ys = (np.arange(ny) + 0.5) * res
    zs = (np.arange(nz) + 0.5) * res
    X, Y, Z = np.meshgrid(xs, ys, zs, indexing="ij")
    P = np.stack([X.ravel(), Y.ravel(), Z.ravel()], axis=1)
    count = np.zeros(len(P), dtype=np.int16)
    for pn in result.panels:
        o, U, V, N = (np.array(a, float) for a in (pn.origin, pn.U, pn.V, pn.N))
        rel = P - o
        u, v, s = rel @ U, rel @ V, rel @ N
        inside = (s > 0) & (s < t)
        idx = np.nonzero(inside)[0]
        hit = shapely.contains_xy(pn.poly, u[idx], v[idx])
        for cu, cv, r in pn.circles:
            hit &= (u[idx] - cu) ** 2 + (v[idx] - cv) ** 2 > r * r
        count[idx[hit]] += 1
    return count.reshape(nx, ny, nz), (X, Y, Z)


@pytest.mark.parametrize("lid", [LID_OPEN, LID_CLOSED, LID_LIFT])
@pytest.mark.parametrize("cols,rows", [(1, 1), (3, 2)])
def test_montagem_sem_sobreposicao_e_sem_buracos(lid, cols, rows):
    p = BoxParams(width=90, depth=72, height=45, thickness=3, finger=9, lid=lid, cols=cols, rows=rows,
                  divider_clearance=0, lid_clearance=0, finger_hole=0)
    r = generate(p)
    cnt, (X, Y, Z) = occupancy(r, 0.75)
    assert cnt.max() == 1, "duas peças ocupam o mesmo lugar"
    d, t = r.dims, p.thickness
    shell = (X < t) | (X > d.W - t) | (Y < t) | (Y > d.D - t) | (Z < t)
    if lid == LID_CLOSED:
        shell |= Z > d.H - t
    shell &= Z < d.H
    assert cnt[shell].min() == 1, "faltou material numa parede (dente e vão não se completam)"
    if lid == LID_LIFT:
        assert cnt[Z > d.H].min() == 1         # tampa de cima inteira


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
