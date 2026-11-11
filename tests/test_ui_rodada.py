"""Rodada de interface: barra de ícones com o resumo da seleção, aviso de cores uma vez por lote,
Conferência vazia, 1024 px, ordem do painel de parâmetros, mover pelo teclado e rodapé "cheias · última"."""
import threading
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from app.core.models import Placement
from tests.conftest import fx


@pytest.fixture
def window(monkeypatch):
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Discard))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.No))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: 0))
    from app.ui.main_window import MainWindow
    w = MainWindow(workers=0)
    w.resize(1440, 900)
    w.show()
    QApplication.processEvents()
    yield w
    w.dirty = False
    w.close()
    w.deleteLater()


def _layout(w, placements):
    """Encaixe montado à mão (sem rodar o algoritmo): simples.dxf, peças centradas na origem."""
    if not w.parts:
        w.load_files([fx("simples.dxf")])
    w.placements = placements
    w.n_sheets = max(pl.sheet_index for pl in placements) + 1
    w._rebuild_checker()
    w.tabs.setCurrentIndex(1)
    w._redraw()
    w._update_buttons()
    w._update_status()
    QApplication.processEvents()


def _item(w, pid, inst=0):
    return next(it for it in w.canvas.part_items
                if (it.placement.part_id, it.placement.instance) == (pid, inst))


# ---------------------------------------------------------------- 1. menos faixas acima do desenho
def test_resumo_da_selecao_fica_na_barra_de_icones(window):
    w = window
    card = w.tabs.parentWidget()
    rows = [card.layout().itemAt(i).layout() for i in range(card.layout().count())]
    toolbar = next(r for r in rows if r is not None and r.indexOf(w.btn_rot) >= 0)
    assert toolbar.indexOf(w.selection_summary) >= 0              # mesma linha dos ícones
    assert toolbar.indexOf(w.selection_summary) < toolbar.indexOf(w.btn_prev)
    assert all(card.layout().itemAt(i).widget() is not w.selection_summary for i in range(card.layout().count()))
    _layout(w, [Placement("P001", 0, 0, 100, 100, 0), Placement("P002", 0, 0, 300, 100, 0)])
    assert w.selection_summary.property("hint") is True          # dica discreta, sem seleção
    _item(w, "P001").setSelected(True)
    QApplication.processEvents()
    assert "Peça 1" in w.selection_summary.full_text() and w.selection_summary.property("hint") is False
    assert "Peça 1" in w.selection_summary.toolTip()
    # o corte com "…" é testado num rótulo solto: dentro da barra o layout devolve a largura que a fonte
    # do sistema pede (no Windows o texto cabia inteiro e o teste falhava)
    from app.ui.components import ElidedLabel
    lbl = ElidedLabel()
    lbl.setText(w.selection_summary.full_text())
    lbl.resize(60, 20)
    lbl.show()
    QApplication.processEvents()
    assert lbl.text().endswith("…") and lbl.toolTip() == w.selection_summary.full_text()
    lbl.deleteLater()


def test_aviso_de_cores_aparece_uma_vez_por_lote(window, tmp_path):
    import shutil
    w = window
    w.load_files([fx("exemplo_lab.dxf")])
    assert not w.banner.isHidden() and 'href="colorops:"' in w.banner_text.text()
    w.dismiss_banner()
    w.reimport()                                                   # redesenho/reimportação do mesmo lote
    w._announce_color_ops()
    assert w.banner.isHidden()
    # a confirmação obrigatória da 1ª exportação continua pendente (fechar o aviso não confirma nada)
    from app.core import operations
    keys = set(operations.file_colors([p for p in w.parts if p.quantity > 0]))
    assert not keys <= w.color_ops_confirmed
    outro = str(tmp_path / "outro_lote.dxf")
    shutil.copy(fx("exemplo_lab.dxf"), outro)
    w.dirty = False
    w.load_files([outro])                                          # outro lote: avisa de novo
    assert w.files == [outro]
    assert not w.banner.isHidden() and 'href="colorops:"' in w.banner_text.text()


# ---------------------------------------------------------------- 2. Conferência vazia e 1024 px
def test_conferencia_vazia_mostra_estado_vazio_e_desabilita_botoes(window, tmp_path):
    from tests.test_import_review import _gappy
    p = window.review_panel
    p.set_data([], [fx("simples.dxf")])
    assert p.list_stack.currentWidget() is p.empty and "Nada a conferir" in p.empty_title.text()
    assert p.actions_box.isHidden()
    for b in (p.btn_note, p.btn_next, p.btn_close, p.btn_ack, p.btn_undo, p.btn_open, p.btn_folder):
        assert not b.isEnabled(), b.text()
    assert not p.show_fixed.isEnabled()
    window.load_files([_gappy(str(tmp_path / "Parede.dxf"))])
    assert p.list_stack.currentWidget() is p.tree and not p.actions_box.isHidden()
    assert p.btn_note.isEnabled() and p.btn_next.isEnabled()


def test_1024_paineis_viram_icone_e_abas_ficam_inteiras(window):
    w = window
    w.load_files([fx("simples.dxf")])
    w.resize(1024, 700)
    QApplication.processEvents()
    assert w.width() <= 1024                                       # vindo de 1440: encolhe de verdade
    for b in (w.btn_parts_panel, w.btn_params_panel):
        assert b.text() == "" and b.toolButtonStyle() == Qt.ToolButtonIconOnly
        assert b.toolTip() and not b.icon().isNull()
    assert [w.tabs.tabText(i) for i in range(2)] == ["Arquivo original", "Encaixe"]
    assert not w.tabs.usesScrollButtons() and w.tabs.width() >= w.tabs.sizeHint().width()
    w.resize(1440, 900)
    QApplication.processEvents()
    assert w.btn_parts_panel.text() == "Peças" and w.btn_params_panel.text() == "Parâmetros"


def test_conferencia_mantem_numero_pendente_no_modo_estreito(window, tmp_path):
    from tests.test_import_review import _gappy
    w = window
    w.load_files([_gappy(str(tmp_path / "Parede.dxf"))])
    n = len(w.review_panel.pending())
    assert f"({n})" in w.btn_review_panel.text()
    w.apply_width(1024, force=True)
    assert w.btn_review_panel.text() == str(n) and str(n) in w.btn_review_panel.toolTip()


# ---------------------------------------------------------------- 3. parâmetros em ordem fixa
def test_parametros_em_ordem_fixa_e_secoes_lembradas(window):
    from app.ui.settings_panel import SECTION_ORDER, SettingsPanel
    sp = window.settings_panel
    lay = sp.sections["arquivo"].parentWidget().layout()
    pos = [lay.indexOf(sp.sections[k]) for k in SECTION_ORDER]
    assert pos == sorted(pos) and min(pos) >= 0
    window.load_files([fx("exemplo_lab.dxf")])                    # laser aparece, mas depois de Encaixe
    assert sp.laser_box.isVisibleTo(sp)
    assert [lay.indexOf(sp.sections[k]) for k in SECTION_ORDER] == pos
    assert sp.sections["encaixe"].y() < sp.laser_box.y()
    sp.sections["placa"].toggle.setChecked(False)
    sp.adv_btn.setChecked(True)
    novo = SettingsPanel()
    try:
        assert not novo.sections["placa"].toggle.isChecked() and novo.sections["encaixe"].toggle.isChecked()
        assert novo.adv_btn.isChecked()
    finally:
        novo.deleteLater()


# ---------------------------------------------------------------- 4. mover peça pelo teclado
def test_nudge_step_por_modificador():
    from app.ui.canvas import nudge_step
    assert nudge_step(Qt.Key_Right, Qt.NoModifier) == ((1.0, 0.0), False)
    assert nudge_step(Qt.Key_Left, Qt.ShiftModifier) == ((-10.0, 0.0), False)
    (dx, dy), snap = nudge_step(Qt.Key_Up, Qt.AltModifier)
    assert (dx, round(dy, 6), snap) == (0.0, 0.1, False)
    assert nudge_step(Qt.Key_Down, Qt.ControlModifier) == ((0.0, -1.0), True)
    assert nudge_step(Qt.Key_A, Qt.NoModifier) is None


def test_setas_movem_com_um_passo_de_desfazer(window):
    w = window
    _layout(w, [Placement("P001", 0, 0, 100, 100, 0), Placement("P002", 0, 0, 300, 100, 0)])
    it = _item(w, "P001")
    it.setSelected(True)
    w.canvas.setFocus()
    QTest.keyClick(w.canvas, Qt.Key_Right)
    QTest.keyClick(w.canvas, Qt.Key_Right, Qt.ShiftModifier)
    QTest.keyClick(w.canvas, Qt.Key_Up, Qt.AltModifier)
    pl = it.placement
    assert pl.x == pytest.approx(111) and pl.y == pytest.approx(100.1)
    assert it.pos().x() == pytest.approx(111)                       # o desenho acompanha
    assert w.dirty
    w.undo()                                                         # a sequência inteira volta de uma vez
    pl = _item(w, "P001").placement
    assert (pl.x, pl.y) == (100, 100)
    assert not w.undo_stack


def test_setas_respeitam_trava_colisao_e_campos(window):
    w = window
    _layout(w, [Placement("P001", 0, 0, 100, 100, 0), Placement("P002", 0, 0, 300, 100, 0)])
    it = _item(w, "P001")
    it.setSelected(True)
    it.placement.locked = True
    w.canvas.setFocus()
    QTest.keyClick(w.canvas, Qt.Key_Right)
    assert it.placement.x == 100                                    # travada não anda
    it.placement.locked = False
    for _ in range(12):                                             # 120 mm: encosta na vizinha
        QTest.keyClick(w.canvas, Qt.Key_Right, Qt.ShiftModifier)
    assert it.colliding and _item(w, "P002").colliding              # fica vermelha (checagem existente)
    # campo numérico com foco: as setas são dele
    spin = w.settings_panel.spacing
    spin.setFocus()
    before = it.placement.x
    QTest.keyClick(spin, Qt.Key_Up)
    assert it.placement.x == before


def test_ctrl_seta_encosta_na_vizinha_e_na_margem(window):
    w = window
    _layout(w, [Placement("P001", 0, 0, 100, 100, 0), Placement("P002", 0, 0, 300, 100, 0)])
    p = w.nest_params()
    it = _item(w, "P001")
    it.setSelected(True)
    w.canvas.setFocus()
    # o verificador de colisão usa o contorno com folga (meio espaçamento + segurança de cada lado)
    a, b = w.checker.solid("P001", 0, False).bounds, w.checker.solid("P002", 0, False).bounds
    usable = w.checker.usable_for("", "").bounds
    QTest.keyClick(w.canvas, Qt.Key_Right, Qt.ControlModifier)
    assert it.placement.x + a[2] == pytest.approx(300 + b[0], abs=0.02)   # encostou na vizinha
    assert 300 - 30 - (it.placement.x + 50) >= p.spacing                  # e respeita o espaçamento
    assert not it.colliding and w._mark_collisions() == 0
    QTest.keyClick(w.canvas, Qt.Key_Left, Qt.ControlModifier)
    assert it.placement.x + a[0] == pytest.approx(usable[0], abs=0.03)    # encosta na margem
    assert it.placement.x - 50 >= p.margin
    QTest.keyClick(w.canvas, Qt.Key_Down, Qt.ControlModifier)
    assert it.placement.y + a[1] == pytest.approx(usable[1], abs=0.03)
    assert w._mark_collisions() == 0
    n = len(w.undo_stack)
    QTest.keyClick(w.canvas, Qt.Key_Down, Qt.ControlModifier)             # já encostada: nada muda
    assert len(w.undo_stack) == n and it.placement.y + a[1] == pytest.approx(usable[1], abs=0.03)


def test_f1_lista_os_atalhos_das_setas(window, monkeypatch):
    seen = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: seen.append(a[2])))
    window.show_shortcuts()
    assert "Ctrl+setas" in seen[0] and "Shift / Alt + setas" in seen[0]


# ---------------------------------------------------------------- 5. barra superior
@pytest.mark.parametrize("width,open_txt,intranet_txt", [(1700, "Abrir DXF", "Intranet FIAP"),
                                                          (1440, "", "Intranet"), (1280, "", "Intranet"),
                                                          (1100, "", "")])
def test_barra_superior_por_largura(window, width, open_txt, intranet_txt):
    window.apply_width(width, force=True)
    assert window.btn_open.text() == open_txt and window.btn_intranet.text() == intranet_txt
    assert window.btn_intranet.toolTip()


# ---------------------------------------------------------------- 6. rodapé e placa quase vazia
def test_usage_split_separa_cheias_e_ultima():
    from app.ui.canvas import usage_split
    us = usage_split({0: 60.0, 1: 8.0}, {0: 100.0, 1: 100.0}, {0: "MDF", 1: "MDF"})
    assert us["full"] == pytest.approx(0.6) and us["last"] == pytest.approx(0.08) and us["low"] == {1}
    assert us["total"] == pytest.approx(0.34)
    one = usage_split({0: 8.0}, {0: 100.0}, {0: "MDF"})               # placa única: nunca "pouco usada"
    assert one["full"] is None and not one["low"]
    two = usage_split({0: 60.0, 1: 8.0, 2: 50.0}, {0: 100.0, 1: 100.0, 2: 100.0}, {0: "A", 1: "A", 2: "B"})
    assert two["lasts"] == {1, 2} and two["low"] == {1}               # B tem 1 placa só


def _low_layout(w):
    _layout(w, [Placement("P001", 0, 0, 100, 100, 0), Placement("P001", 1, 0, 300, 100, 0),
                Placement("P002", 0, 0, 450, 100, 0), Placement("P003", 0, 1, 100, 100, 0)])


def test_rodape_mostra_cheias_e_ultima_e_selo_pouco_usada(window):
    w = window
    _low_layout(w)
    txt = w.chip_util.text()
    assert "Cheias" in txt and "última" in txt and "5,7%" in txt and "1,2%" in txt
    assert "Aproveitamento total" in w.chip_util.toolTip() and "pouco usada" in w.chip_util.toolTip()
    label = next(lb for lb in w.canvas.sheet_labels if lb.index == 1)
    assert label.low and "pouco usada" in label.sub
    assert not next(lb for lb in w.canvas.sheet_labels if lb.index == 0).low
    w.apply_width(1024, force=True)                                   # compacto
    assert "Cheias" not in w.chip_util.text() and w._metric_caps["aproveitamento"][0].text() == "cheias · última"


def test_aviso_de_placa_pouco_usada_com_acoes(window, monkeypatch):
    w = window
    _low_layout(w)
    calls = []
    monkeypatch.setattr(type(w), "open_remnants_dialog", lambda self: calls.append("retalhos"))
    monkeypatch.setattr(type(w), "start_nest", lambda self, more_time=False: calls.append(("encaixe", more_time)))
    w._warn_low_use()
    assert not w.banner.isHidden() and "pouco usada" in w.banner_text.text()
    assert 'href="renest:"' in w.banner_text.text() and 'href="remnants:"' in w.banner_text.text()
    w._banner_link("remnants:")
    w._banner_link("renest:")
    assert calls == ["retalhos", ("encaixe", True)]
    w._warn_low_use()                                                 # não fechou: pode voltar
    assert not w.banner.isHidden()
    w.dismiss_banner()
    w._warn_low_use()                                                 # fechou: não volta neste lote
    assert w.banner.isHidden()


def test_encaixar_com_mais_tempo_dobra_o_limite_so_desta_vez(window, monkeypatch):
    w = window
    w.load_files([fx("simples.dxf")])
    w.settings_panel.stop_ni.setValue(40)
    seen = []

    class _Sig:
        def connect(self, *_):
            pass

    class FakeWorker:
        def __init__(self, parts, p, locked, workers=None):
            seen.append(p)
            self.bestFound = self.progress = self.failed = self.finished = _Sig()
            self.pause_event = threading.Event()

        def start(self):
            pass

    import app.ui.mainwindow.nesting as nesting
    monkeypatch.setattr(nesting, "NestWorker", FakeWorker)
    w.start_nest(more_time=True)
    w.worker = None
    assert seen[0].stop_after_seconds == 80 and w.settings_panel.stop_ni.value() == 40
    w.start_nest()
    w.worker = None
    assert seen[1].stop_after_seconds == 40


def test_rodape_troca_solucoes_por_melhora_e_tentativas(window):
    w = window
    cap, val = w._metric_caps["soluções testadas"]
    w.worker = SimpleNamespace(pause_event=threading.Event())
    try:
        w.since_improve = 12.4
        w._update_gen_chip()
        assert val.text() == "há 12 s" and cap.text() == "última melhora"
    finally:
        w.worker = None
    w.evaluated = 4050
    w._update_gen_chip()
    assert val.text() == "4.050" and cap.text() == "tentativas"
    assert "soluções" not in cap.text()



# ---------------------------------------------------------------- 7. placas em grade
def test_grade_origem_e_vaga_sao_inversas():
    from app.core.models import NestParams
    from app.ui.canvas import grid_gaps, grid_index, grid_origin, sheet_offset
    p = NestParams(sheet_width=600, sheet_height=400)
    for cols in (0, 1, 2, 3):
        for i in range(7):
            ox, oy = grid_origin(p, i, cols)
            assert grid_index(p, ox + 300, oy + 200, cols) == i            # centro da placa
            assert grid_index(p, ox + 1, oy + 1, cols) == i
    assert grid_origin(p, 2, 0) == (sheet_offset(p, 2), 0)               # fileira única = como antes
    gx, gy = grid_gaps(p)
    assert grid_origin(p, 3, 2) == (600 + gx, -(400 + gy))              # 2ª linha desce


def test_grade_escolhe_colunas_pela_proporcao_da_vista():
    from app.ui.canvas import best_columns
    gaps = (48.0, 100.0)
    assert best_columns(4, 600, 400, gaps, 1600, 300) == 4               # vista larga: uma fileira
    assert best_columns(4, 600, 400, gaps, 700, 700) == 2                # vista quadrada: 2 × 2
    assert best_columns(4, 600, 400, gaps, 300, 1600) == 1               # vista alta: uma coluna
    assert best_columns(1, 600, 400, gaps, 300, 1600) == 1
    assert best_columns(3, 600, 400, gaps, 0, 0) == 3                    # sem vista: como antes


def test_arrastar_para_a_placa_de_baixo_muda_a_placa(window):
    w = window
    _layout(w, [Placement("P001", 0, 0, 100, 100, 0), Placement("P001", 1, 0, 300, 100, 0),
                Placement("P002", 0, 1, 300, 100, 0), Placement("P003", 0, 2, 300, 200, 0)])
    w.canvas._cols = 2
    w.canvas._place_grid()
    assert w.canvas.sheet_origin(2)[1] < 0                             # placa 3 na linha de baixo
    sheet3 = next(s for s in w.canvas.sheet_items if s.index == 2)
    assert sheet3.pos().y() < 0 and sheet3.pos().x() == 0
    it = _item(w, "P001")
    w.canvas.scene().clearSelection()
    it.setSelected(True)
    ox, oy = w.canvas.sheet_origin(2)
    it.setPos(ox + 120, oy + 300)
    w.on_items_released()
    assert (it.placement.sheet_index, it.placement.x, it.placement.y) == (2, pytest.approx(120), pytest.approx(300))
    assert not it.colliding
    # navegação e "placa da tela" entendem a grade
    w.goto_sheet(2)
    assert w.canvas.current_sheet() == 2
    w.goto_sheet(0)
    assert w.canvas.current_sheet() == 0


def test_navegacao_de_placas_compacta_no_modo_estreito(window):
    w = window
    _low_layout(w)
    w.apply_width(1440, force=True)
    assert "Placa 1 de 2" in w.sheet_label.text()
    w.apply_width(1024, force=True)
    assert w.sheet_label.text().strip() == "1/2" and "Placa 1 de 2" in w.sheet_label.toolTip()
    w.goto_sheet(1)
    assert w.sheet_label.text().strip() == "2/2"


def test_linhas_de_peca_cabem_na_lista_com_barra_de_rolagem(monkeypatch):
    """1024 px logo depois de importar: a linha não pode ficar mais larga que a área visível da lista
    (cortava "Rotação fixa" e o ✓)."""
    from PySide6.QtWidgets import QApplication, QMessageBox
    from app.ui.main_window import MainWindow
    from tests.conftest import fx
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    w = MainWindow(workers=0)
    try:
        w.resize(1440, 900)
        w.show()
        w.load_files([fx("exemplo_lab.dxf")])
        w.set_review_open(True)
        for _ in range(5):
            app.processEvents()
        w.resize(1024, 700)                                   # encolher depois de importar
        for _ in range(10):
            app.processEvents()
        view = w.parts_panel.list.viewport()
        assert w.parts_panel.list.verticalScrollBar().isVisible()
        for r in w.parts_panel.rows:
            assert r.lock.geometry().right() <= view.width() and r.done_btn.geometry().right() <= view.width()
    finally:
        w.dirty = False
        w.close()
