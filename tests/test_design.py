"""Regressões de layout e preservação de dados nos novos controles."""
import os
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QScrollArea, QToolButton


@pytest.fixture
def window(monkeypatch):
    # O plugin offscreen do Qt no Windows não enumera fontes instaladas.
    if os.name == "nt":
        font_dir = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for name in ("segoeui.ttf", "segoeuib.ttf", "seguisb.ttf"):
            if (font_dir / name).exists():
                QFontDatabase.addApplicationFont(str(font_dir / name))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: QMessageBox.No)
    from app.ui.main_window import MainWindow
    w = MainWindow(workers=0)
    w.show()
    yield w
    w.dirty = False
    w.close()
    w.deleteLater()


def test_panels_restore_values_and_menu_state(window):
    window.settings_panel.w.setValue(720)
    for button, action, panel in (
        (window.btn_parts_panel, window.a_parts, window.parts_panel),
        (window.btn_params_panel, window.a_params, window.settings_panel),
    ):
        QTest.keyClick(button, Qt.Key_Space)
        assert panel.isHidden() and not action.isChecked()
        action.setChecked(True)
        assert not panel.isHidden() and button.isChecked()
    assert window.settings_panel.w.value() == 720
    assert window.btn_parts_panel.accessibleName()


def test_sections_preserve_values_and_keyboard_access(window):
    step = window.box_panel.steps["arestas"]
    window.box_panel.kerf.setValue(.17)
    assert step.content.isHidden()
    window.set_mode(2)
    step.toggle.setFocus()
    QTest.keyClick(step.toggle, Qt.Key_Space)
    assert not step.content.isHidden()
    QTest.keyClick(step.toggle, Qt.Key_Space)
    assert step.content.isHidden() and window.box_panel.kerf.value() == .17
    window.set_mode(1)
    from app.ui.components import SectionToggle
    toggle = next(t for t in window.photo_panel.findChildren(SectionToggle) if t.text() == "Ajustes da imagem")
    QTest.keyClick(toggle, Qt.Key_Space)
    assert toggle.isChecked() and not toggle.content.isHidden()


@pytest.mark.parametrize("width", [1024, 1100, 1440])
def test_window_and_fields_fit_available_width(window, width):
    window.load_files([str(Path(__file__).parent / "fixtures" / "simples.dxf")])
    window.resize(width, 800)
    QApplication.processEvents()
    assert window.width() <= width
    viewport = window.settings_panel.findChild(QScrollArea).viewport()
    for field in (window.settings_panel.units, window.settings_panel.w, window.settings_panel.margin):
        point = field.mapTo(viewport, field.rect().topRight())
        assert point.x() < viewport.width(), (width, field.objectName(), point.x(), viewport.width())
    window.btn_params_panel.setChecked(False)
    window.btn_parts_panel.setChecked(False)
    QApplication.processEvents()
    assert window.canvas.width() > width * .8
    assert window.btn_params_panel.isVisible() and window.btn_parts_panel.isVisible()


def test_secondary_text_and_control_contrast():
    from app.ui.theme import LIGHT, DARK
    from tests.test_ux import _contrast
    for palette in (LIGHT, DARK):
        for background in ("surface", "surface2", "surface3"):
            assert _contrast(palette["muted"], palette[background]) >= 4.5
        assert _contrast(palette["control_border"], palette["surface2"]) >= 3
        assert _contrast(palette["danger"], palette["danger_soft"]) >= 4.5


def test_numeric_fields_have_margin_and_brazilian_decimal(window):
    """Campos numéricos: texto com respiro, vírgula decimal e ponto digitado aceito como vírgula."""
    from PySide6.QtWidgets import QDoubleSpinBox
    spins = window.findChildren(QDoubleSpinBox)
    assert spins
    for s in spins:
        assert s.lineEdit().textMargins().left() >= 6, s.objectName()
    m = window.settings_panel.margin
    m.setValue(5)
    assert "5,0" in m.text()
    m.lineEdit().selectAll()
    m.setFocus()
    QTest.keyClicks(m.lineEdit(), "7.5")
    QTest.keyClick(m.lineEdit(), Qt.Key_Return)
    assert m.value() == 7.5


def test_dark_theme_links_and_engraving_lines_are_readable():
    """Tema escuro: links do aviso e linhas azuis/pretas do desenho legíveis (só na tela)."""
    from PySide6.QtGui import QColor, QPalette
    from app.ui import theme
    app = QApplication.instance()
    theme.apply_theme(app, True)
    try:
        assert app.palette().color(QPalette.Link).name() == theme.DARK["accent_text"]
        bg = QColor(theme.DARK["sheet"])

        def lum(c):
            return sum(k * (v / 255) ** 2.2 for k, v in zip((0.2126, 0.7152, 0.0722), (c.red(), c.green(), c.blue())))
        for rgb in ((0, 0, 255), (0, 0, 0)):
            c = theme.line_color(QColor(*rgb), True)
            assert (lum(c) + .05) / (lum(bg) + .05) >= 3, rgb
        assert theme.line_color(QColor(255, 0, 0), True) == QColor(255, 0, 0)
        assert theme.line_color(QColor(0, 0, 255), False) == QColor(0, 0, 255)
    finally:
        theme.apply_theme(app, False)


def test_toolbar_and_status_follow_mode(window, tmp_path):
    """Cada aba mostra só o que vale para ela e não herda mensagens das outras."""
    window.statusBar().showMessage("Encaixe concluído. Ctrl+E exporta.", 12000)
    window.set_mode(1)
    QApplication.processEvents()
    assert "Encaixe concluído" not in window.statusBar().currentMessage()
    assert not window.btn_open.isVisible() and not window.btn_save.isVisible()
    assert window.preset_combo.isVisible()
    window.set_mode(2)
    assert not window.preset_combo.isVisible()
    window.set_mode(0)
    assert window.btn_open.isVisible() and window.preset_combo.isVisible()


def test_photo_empty_state_then_preview(window, tmp_path):
    from PySide6.QtGui import QColor, QImage
    pp = window.photo_panel
    window.set_mode(1)
    assert pp.view_stack.currentWidget() is not pp.view
    assert pp.btn_empty_open.objectName() == "primary" and pp.btn_open.objectName() != "primary"
    img = QImage(40, 30, QImage.Format_RGB32)
    img.fill(QColor(120, 120, 120))
    path = str(tmp_path / "foto.png")
    img.save(path)
    assert pp.open_image(path)
    assert pp.view_stack.currentWidget() is pp.view and pp.btn_open.text() == "Trocar imagem…"
    pp.wait_idle()


def test_buttons_follow_one_pattern(window):
    """Ações do painel Laser parecem link de ação; alternâncias usam o padrão do topo, sem borda azul fixa."""
    from PySide6.QtWidgets import QPushButton
    sp = window.settings_panel
    for b in (sp.btn_color_ops, sp.btn_materials):
        assert b.objectName() == "ghost"
    assert window.btn_parts_panel.objectName() == window.btn_params_panel.objectName() == "PanelToggle"
    segs = [b for b in window.box_panel.findChildren(QPushButton) if b.objectName() == "Seg"]
    assert segs and all(b.parentWidget().objectName() == "SegTrack" for b in segs)
    from app.ui.theme import STYLE
    assert "QToolButton#PanelToggle:checked" in STYLE and "color: %(accent_text)s; text-align: left" in STYLE


@pytest.mark.parametrize("width", [1024, 1440])
def test_part_cards_grid_two_columns_and_whole(window, width):
    """Peças em grade de 2 colunas; ✓ de feita, nome, ×quantidade e medida inteiros no cartão."""
    from app.ui.parts_panel import PartRow
    window.load_files([str(Path(__file__).parent / "fixtures" / "exemplo_lab.dxf")])
    window.resize(width, 760)
    QTest.qWait(50)
    lst = window.parts_panel.list
    rows = window.parts_panel.rows
    assert len(rows) == 5
    xs = {lst.visualItemRect(lst.item(i)).x() for i in range(len(rows))}
    assert len(xs) == 2, (width, xs)
    for row in rows:
        assert isinstance(row, PartRow)
        right = row.done_btn.mapTo(row, row.done_btn.rect().topRight()).x()
        assert right <= row.width(), (width, row.full_name)
        assert row.qty.text() == f"×{row.part.quantity}"
        sub = row._sub
        assert sub.width() >= sub.fontMetrics().horizontalAdvance(sub.text()), (width, sub.text(), sub.width())
    # o cartão "Adicionar DXF" fecha a grade e não vira seleção de peça
    assert lst.count() == len(rows) + 1 and not lst.item(len(rows)).flags() & Qt.ItemIsSelectable


def test_part_detail_edits_selected_part(window):
    """Quantidade e rotação fixa da peça selecionada mudam pelo bloco de baixo (e o cartão acompanha)."""
    window.load_files([str(Path(__file__).parent / "fixtures" / "exemplo_lab.dxf")])
    QApplication.processEvents()
    panel = window.parts_panel
    assert panel.detail.part is None and not panel.detail.body.isVisibleTo(panel)
    panel.list.setCurrentRow(1)
    part = panel.rows[1].part
    assert panel.detail.part is part and panel.detail.body.isVisibleTo(panel)
    QApplication.processEvents()
    assert panel.detail.spin.lineEdit().width() > 20 and panel.detail.spin.text() == str(part.quantity)
    q = part.quantity
    QTest.mouseClick(panel.detail.btn_plus, Qt.LeftButton)
    assert part.quantity == q + 1 and panel.rows[1].qty.text() == f"×{q + 1}"
    window.undo()
    QApplication.processEvents()
    assert part.quantity == q and panel.rows[1].qty.text() == f"×{q}"
    panel.detail.lock.setChecked(True)
    assert part.rotation_locked and not panel.rows[1].lock_mark.isHidden()


def test_canvas_refits_on_resize_until_user_zooms(window):
    window.load_files([str(Path(__file__).parent / "fixtures" / "simples.dxf")])
    window.resize(1440, 900)
    QApplication.processEvents()
    window.canvas.fit_all()
    before = window.canvas.transform().m11()
    window.resize(1024, 700)
    QTest.qWait(50)
    assert window.canvas.transform().m11() < before          # enquadrou de novo na janela menor
    window.canvas._auto_fit = False                          # usuário deu zoom: a vista fica onde ele deixou
    kept = window.canvas.transform().m11()
    window.resize(1440, 900)
    QTest.qWait(50)
    assert window.canvas.transform().m11() == kept


def test_metrics_footer_and_button_roles(window):
    """Rodapé com 4 células; antes do encaixe "Encaixar" é a ação principal, depois é "Exportar"."""
    window.load_files([str(Path(__file__).parent / "fixtures" / "simples.dxf")])
    QApplication.processEvents()
    caps = [cap.text() for cap, _ in window._metric_caps.values()]
    assert caps == ["APROVEITAMENTO", "PLACAS", "PEÇAS", "TEMPO ESTIMADO"]
    assert window.btn_nest.objectName() == "primary" and window.btn_export.objectName() == "primary"
    assert not window.btn_export.isEnabled()
    window.start_nest()
    for _ in range(200):
        QTest.qWait(50)
        if window.worker is None:
            break
        if window.placements:
            window.stop_nest()
    QTest.qWait(100)
    assert window.placements and window.btn_export.isEnabled()
    assert window.btn_nest.objectName() == "" and window.btn_nest.text() == "Encaixar de novo"
    assert window.chip_time.text().startswith("~") and "Placa 1" in window.chip_time.toolTip()
    assert window._metric_caps["tempo"][0].text() == "TEMPO ESTIMADO"
    assert window.chip_util.text().endswith("%")


def test_switches_keep_their_options(window):
    """As chaves liga/desliga continuam sendo as mesmas opções (valores e sinais preservados)."""
    sp = window.settings_panel
    for sw in (sp.mirror, sp.pip, sp.multi, sp.material_mode, sp.two_tubes):
        assert sw.objectName() == "Switch"
    before = sp.params().allow_mirror
    sp.mirror.setChecked(not sp.mirror.isChecked())
    assert sp.params().allow_mirror != before


def test_clean_layout_keeps_every_action(window, monkeypatch):
    """Tela enxuta: cada ação aparece uma vez na tela e continua acessível (menu, atalho ou etiqueta)."""
    window.load_files([str(Path(__file__).parent / "fixtures" / "simples.dxf")])
    QApplication.processEvents()
    # barra da seleção (girar, travar, remover, posição) só com peça selecionada
    assert not window.selection_bar.isVisibleTo(window)
    for b in (window.btn_rot, window.btn_lock, window.btn_del, window.btn_positions):
        assert b.parentWidget() is window.selection_bar
    # cada ação do desenho continua no menu, com atalho, mesmo sem a barra
    from PySide6.QtGui import QAction
    menu = {a.text(): a.shortcut().toString() for a in window.findChildren(QAction) if a.text()}
    for text, key in (("Girar peça selecionada", "R"), ("Travar/destravar posição", "L"),
                      ("Remover peça selecionada", "Del"), ("Editar posições por instância…", "Ctrl+Shift+M"),
                      ("Enquadrar tudo", "F"), ("Marcar placa da tela como cortada (e ir para a próxima)", "C"),
                      ("Abrir no RDWorks só a placa da tela", "Ctrl+Alt+R"), ("Gerenciar placas salvas…", ""),
                      ("Adicionar DXF…", "Ctrl+Shift+O"), ("Limpar tudo", "Ctrl+Shift+Del")):
        assert menu.get(text) == key, (text, menu.get(text))
    # no cabeçalho do desenho, um botão por ação (sem menu ⋯ repetindo o que está ao lado)
    head = [b for b in window.findChildren(QToolButton) if b.isVisibleTo(window) and b.menu() is not None
            and b.window() is window and b.parentWidget() is window.btn_fit.parentWidget()]
    assert not head
    # painel de peças: Kits, quantidades do arquivo e Limpar no menu ⋯ do painel
    pm = [a.text() for a in window.parts_panel.btn_menu.menu().actions() if a.text()]
    assert any("Kits" in t for t in pm) and any("arquivo" in t for t in pm) and any("Limpar" in t for t in pm)
    # sem peça selecionada, o bloco de quantidade não aparece
    assert not window.parts_panel.detail.isVisibleTo(window.parts_panel)
    # engrenagem virou "Gerenciar placas…" no seletor
    called = []
    monkeypatch.setattr(window, "edit_presets", lambda: called.append(1))
    window._preset_chosen(window.preset_combo.count() - 1)
    assert called and isinstance(window.preset_combo.currentData(), (dict, type(None)))
    # atalhos sem conflito
    keys = [a.shortcut().toString() for a in window.findChildren(QAction) if not a.shortcut().isEmpty()]
    assert len(keys) == len(set(keys)), sorted(k for k in keys if keys.count(k) > 1)


def test_sheet_label_has_cut_and_rdworks_chips(window):
    """A etiqueta de cada placa marca "cortada" e abre só aquela placa no RDWorks."""
    from PySide6.QtCore import QPointF
    window.load_files([str(Path(__file__).parent / "fixtures" / "simples.dxf")])
    window.start_nest()
    for _ in range(200):
        QTest.qWait(50)
        if window.worker is None:
            break
        if window.placements:
            window.stop_nest()
    QTest.qWait(100)
    lb = window.canvas.sheet_labels[0]
    width, compact = lb._layout()
    open_r, cut_r = lb._chips(width, compact)
    opened, cut = [], []
    window.canvas.sheetOpenClicked.disconnect()           # não exporta nem abre o RDWorks de verdade aqui
    window.canvas.sheetOpenClicked.connect(lambda si: opened.append(si))
    window.canvas.sheetCutClicked.connect(lambda si: cut.append(si))

    class _Ev:
        def __init__(self, p):
            self._p = p

        def pos(self):
            return self._p

        def accept(self):
            pass
    lb.mousePressEvent(_Ev(open_r.center()))
    lb.mousePressEvent(_Ev(cut_r.center()))
    assert opened == [lb.index] and cut == [lb.index]


def test_selection_bar_actions_and_advanced_single_toggle(window):
    """Com peça selecionada a barra mostra o resumo e as 4 ações; "Avançado" abre com um toggle só."""
    window.load_files([str(Path(__file__).parent / "fixtures" / "simples.dxf")])
    window.start_nest()
    for _ in range(200):
        QTest.qWait(50)
        if window.worker is None:
            break
        if window.placements:
            window.stop_nest()
    QTest.qWait(100)
    window.canvas.part_items[0].setSelected(True)
    QApplication.processEvents()
    assert window.selection_bar.isVisibleTo(window) and window.btn_positions.isEnabled()
    window.canvas.scene().clearSelection()
    QApplication.processEvents()
    assert not window.selection_bar.isVisibleTo(window)
    sp = window.settings_panel
    assert sp.adv_btn is sp.adv_box.toggle and not sp.adv_box.body.isVisibleTo(sp)
    sp.adv_btn.setChecked(True)
    assert sp.adv_box.body.isVisibleTo(sp) and sp.stop_ni.isVisibleTo(sp)


def test_current_sheet_rdworks_shortcut(window, monkeypatch):
    """Ctrl+Alt+R abre no RDWorks a placa do centro da tela (o mesmo "▶ RDWorks" da etiqueta)."""
    window.load_files([str(Path(__file__).parent / "fixtures" / "simples.dxf")])
    window.start_nest()
    for _ in range(200):
        QTest.qWait(50)
        if window.worker is None:
            break
        if window.placements:
            window.stop_nest()
    QTest.qWait(100)
    got = []
    monkeypatch.setattr(window, "open_sheet_in_rdworks", lambda si: got.append(si))
    window.open_current_sheet_in_rdworks()
    assert got == [window.canvas.current_sheet()]
