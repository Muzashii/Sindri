"""Behavior regressions for the UX report, using isolated Qt preferences."""
import time
import threading
from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtCore import Qt, QTimer, QThread
from PySide6.QtGui import QAccessible
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox, QFileDialog, QDoubleSpinBox, QWidget, QApplication
from tests.conftest import fx

@pytest.fixture
def window(monkeypatch):
    from app.ui.main_window import MainWindow
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.No)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: None)
    w = MainWindow(workers=0)
    yield w
    w.dirty = False
    w.close()
    w.deleteLater()


def test_photo_pending_error_and_stale_completion_cannot_export(window, tmp_path):
    from app.core.photo import trace
    from app.ui.dialogs import settings
    p = window.photo_panel
    p.gray = np.full((16, 16), .2, dtype=np.float32)
    p._gen = 1
    params = p.params()
    result = trace(p.gray, params)
    p._traced(1, (result, params), None)
    assert p.export_params() is not None and p.btn_export.isEnabled()
    settings().setValue("photo/last_dir", str(tmp_path))
    settings().setValue("export/open_rdworks", "false")
    p.w.setValue(p.w.value() + 5)
    generation = p._gen
    assert not p.btn_export.isEnabled() and p.export_params() is None
    p._traced(generation - 1, (result, params), None)
    assert p.export_params() is None
    window.export_photo()
    assert not list(tmp_path.glob("*.bmp"))
    p._timer.stop()
    p._traced(generation, None, ValueError("falha simulada"))
    p._laser_changed()
    assert p.result is None and not p.btn_export.isEnabled()
    assert "falha simulada" in p.info.text()


def test_positions_select_single_copy_move_and_undo(window):
    from app.core.models import Placement
    from app.ui.position_dialog import PositionDialog
    window.load_files([fx("simples.dxf")])
    pid = window.parts[0].id
    window.parts[0].quantity = max(2, window.parts[0].quantity)
    window.placements = [Placement(pid, 0, 0, 10, 10, 0), Placement(pid, 1, 0, 10, 10, 0)]
    window.n_sheets = 1
    window.tabs.setCurrentIndex(1)
    window._redraw()
    dialog = PositionDialog(window)
    dialog.selected.connect(window.select_instance)
    dialog.applied.connect(window.apply_instance_position)
    dialog.list.setCurrentRow(1)
    assert [it.placement.instance for it in window.canvas.selected_items()] == [1]
    before = window.placements[1].x
    dialog.x.setValue(-10)
    dialog.apply()
    assert window.placements[0].x == before and window.placements[1].x == -10
    assert "colisão" in window.status_label.text()
    assert "Pronto para exportar" not in window.status_label.text()
    window.undo()
    assert window.placements[1].x == before
    dialog.close()


def test_dividers_operable_with_space_and_synced_with_canvas(window):
    p = window.box_panel
    p.cols.setValue(3)
    p.rows.setValue(2)
    p._sync_div_editor()
    cb = p._divider_checkboxes[("col", 1)]
    assert cb.isChecked()
    QTest.keyClick(cb, Qt.Key_Space)
    assert 1 in p.cols_off and not cb.isChecked()
    p._toggle_divider("col", 1)
    assert 1 not in p.cols_off and cb.isChecked()
    p.cols.setValue(1)
    assert ("col", 1) not in p._divider_checkboxes


def test_recovery_notice_survives_update(window):
    window.show_banner('<a href="recover:">Recuperar</a> · <a href="discard:">Descartar</a>', "warn")
    window.show_banner('<a href="update:">Atualizar agora</a>', "warn")
    assert "Recuperar" in window.banner_text.text()
    window._banner_link("discard:")
    assert "Atualizar agora" in window.banner_text.text()


def test_custom_plate_unhides_params(window):
    window.a_params.setChecked(False)
    assert window.settings_panel.isHidden()
    window._preset_chosen(window.preset_combo.count() - 1)
    assert not window.settings_panel.isHidden() and window.a_params.isChecked()


def test_save_cancel_and_failure_prevent_discard(window, monkeypatch):
    window.load_files([fx("simples.dxf")])
    window.dirty = True
    monkeypatch.setattr(QMessageBox, "exec", lambda box: 0)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda box: next(b for b in box.buttons() if b.text() == "Salvar"))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: ("", ""))
    assert not window.confirm_discard() and window.dirty
    monkeypatch.setattr(window, "save_project", lambda: None)
    assert not window.confirm_discard() and window.dirty
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda box: next(b for b in box.buttons() if b.text() == "Descartar alterações"))
    assert window.confirm_discard()


def test_task_keeps_event_loop_alive_and_restores_after_error(window):
    from app.ui.tasks import run_task
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(True))
    timer.start(5)
    main_thread = threading.get_ident()
    def operation():
        time.sleep(.06)
        return threading.get_ident()
    assert run_task(window, "Teste", operation) != main_thread
    assert ticks and window._ui_task_depth == 0
    def fail():
        raise ValueError("falhou")
    with pytest.raises(ValueError, match="falhou"):
        run_task(window, "Teste de erro", fail)
    assert window._ui_task_depth == 0
    timer.stop()


def test_native_value_accessible_names_survive_compact(window):
    window.apply_width(1100, force=True)
    assert window.btn_open.accessibleName() and window.btn_save.accessibleName()
    control = window.box_panel.finger
    assert isinstance(control, QDoubleSpinBox)
    interface = QAccessible.queryAccessibleInterface(control)
    assert interface.text(QAccessible.Text.Name) == "Largura do dente"
    assert interface.valueInterface() is not None
    window.set_dark(True)
    control.setFocus()


def test_intranet_unrecognized_response_is_not_login(monkeypatch):
    from app.ui.intranet import IntranetDialog
    state = []
    fake = SimpleNamespace(_set_state=lambda text, kind: state.append(text),
        btn_page=SimpleNamespace(setChecked=lambda checked: None))
    IntranetDialog._got_list(fake, "broken json")
    assert "ler a página" in state[-1] and "login" not in state[-1].lower()


def _contrast(a, b):
    def lum(s):
        vals = [int(s[i:i+2], 16) / 255 for i in (1, 3, 5)]
        vals = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in vals]
        return sum(v * weight for v, weight in zip(vals, (.2126, .7152, .0722)))
    x, y = sorted((lum(a), lum(b)))
    return (y + .05) / (x + .05)


def test_text_contrast_of_corrected_themes_and_materials():
    from app.ui.theme import LIGHT, DARK, MATERIAL_COLORS, material_color
    for theme in (LIGHT, DARK):
        for key in ("accent", "accent_hover", "success", "success_hover"):
            assert _contrast("#ffffff", theme[key]) >= 4.5
        assert _contrast(theme["done_fg"], theme["done_bg"]) >= 4.5
        assert _contrast(theme["accent_text"], theme["accent_soft"]) >= 4.5
        assert _contrast(theme["success_text"], theme["surface3"]) >= 4.5
    for color in MATERIAL_COLORS:
        assert _contrast("#ffffff", color) >= 4.5
    for material in ("Acrílico 2 mm", "Acrílico 6 mm", "Compensado"):
        assert _contrast("#ffffff", material_color(material).name()) >= 4.5
