"""Regressões de layout e preservação de dados nos novos controles."""
import os
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QScrollArea


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
