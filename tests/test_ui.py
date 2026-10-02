"""Teste de fumaça da interface (roda sem tela, plataforma offscreen)."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from tests.conftest import fx  # noqa: E402


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    yield a


def _pump(app, secs):
    import time
    t = time.time()
    while time.time() - t < secs:
        app.processEvents()
        time.sleep(0.01)


def test_fluxo_completo(app, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    # não mexer nas preferências reais do usuário
    QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, str(tmp_path / "cfg"))
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path / "cfg"))
    from app.ui.main_window import MainWindow
    from app.ui import main_window as mw
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.Yes))
    w = MainWindow(workers=0)
    w.show()
    w.load_files([fx("exemplo_lab.dxf")])
    assert len(w.parts) == 5 and w.canvas.mode == "preview"
    w.settings_panel.stop_ni.setValue(1)
    w.start_nest()
    for _ in range(600):
        _pump(app, 0.05)
        if w.worker is None:
            break
    assert w.worker is None
    assert len(w.placements) == 35
    assert w.canvas.mode == "layout"
    assert w._mark_collisions() == 0

    # edição manual + desfazer
    it = w.canvas.part_items[0]
    it.setSelected(True)
    rot0 = it.placement.rotation
    w.rotate_selected()
    assert w.placements[0].rotation != rot0 or it.placement.rotation != rot0
    w.toggle_lock_selected()
    assert it.placement.locked
    w.undo()
    w.undo()
    assert not any(p.locked for p in w.placements)
    w.redo()

    # arrastar para fora da placa => colisão
    before = w._mark_collisions()
    it = w.canvas.part_items[1]
    w.canvas.scene().clearSelection()
    it.setSelected(True)
    it.setPos(-300, -300)
    w.on_items_released()
    w._mark_collisions()
    assert any(i.colliding and i.placement.x < 0 for i in w.canvas.part_items)
    w.undo()
    assert w._mark_collisions() == before

    # remover e mover entre placas
    n_before = len(w.placements)
    w.canvas.part_items[0].setSelected(True)
    w.delete_selected()
    assert len(w.placements) < n_before
    w.canvas.part_items[0].setSelected(True)
    w.move_selected_to_sheet(w.n_sheets)
    assert w.n_sheets >= 2

    # salvar/abrir projeto
    proj = str(tmp_path / "t.dxfnest")
    w.project_path = proj
    w.save_project()
    pls = [p.to_json() for p in w.placements]
    w.dirty = False
    w.open_project(proj)
    assert [p.to_json() for p in w.placements] == pls

    # exportar (diálogo substituído)
    class FakeDlg:
        def __init__(self, *a, **k):
            pass

        def exec(self):
            return True

        def options(self):
            return {"folder": str(tmp_path), "base": "t", "version": "R2000", "combined": True,
                    "outline": False, "inner": True, "path": True, "report": True}

    monkeypatch.setattr(mw, "ExportDialog", FakeDlg)
    w.export()
    assert os.path.isfile(tmp_path / "t_placa01.dxf")
    assert os.path.isfile(tmp_path / "t_relatorio.pdf")
    assert os.path.isfile(tmp_path / "t_placa01.png")

    w.set_dark(True)
    w.set_dark(False)

    # limpar tudo volta para a tela inicial
    w.clear_all(ask=False)
    assert w.parts == [] and w.placements == [] and w.files == []
    assert w.canvas.mode == "empty" and w.stack.currentIndex() == 0
    assert not w.btn_nest.isEnabled() and not w.btn_export.isEnabled()
    w.dirty = False
    w.close()
