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
                    "outline": False, "inner": True, "path": True, "report": True,
                    "open_rdworks": True}

    launched = []
    monkeypatch.setattr(mw, "ExportDialog", FakeDlg)
    monkeypatch.setattr(mw, "find_rdworks", lambda saved=None: "C:/RDWorksV8/RDWorksV8.exe")
    monkeypatch.setattr(mw, "launch", lambda exe, path: launched.append((exe, path)))
    w.export()
    assert len(launched) == 1 and launched[0][1].endswith("t_todas_placas.dxf")
    assert QApplication.clipboard().text() == os.path.abspath(launched[0][1])
    # só dois arquivos: todas as placas + relatório
    assert sorted(f for f in os.listdir(tmp_path) if f.startswith("t_")) == ["t_relatorio.pdf", "t_todas_placas.dxf"]

    # checklist na aba Peças: placas cortadas e peças feitas
    first = min(pl.sheet_index for pl in w.placements)
    w.on_sheet_cut(first, True)
    assert first in w.cut_sheets and w.parts_panel.sheet_checks[first].isChecked()
    assert any(it.done for it in w.canvas.sheet_items if it.index == first)
    only_first = {p.id for p in w.parts
                  if {pl.sheet_index for pl in w.placements if pl.part_id == p.id} == {first}}
    assert only_first and only_first <= w.done_parts          # peças só dessa placa ficam feitas
    row = next(r for r in w.parts_panel.rows if r.part.id in only_first)
    assert row.done_btn.isChecked() and row.property("done")
    w.on_sheet_cut(first, False)
    assert not w.done_parts
    row.done_btn.setChecked(True)                              # marcar uma peça direto na lista
    assert row.part.id in w.done_parts
    w.reset_checklist()
    assert not w.cut_sheets and not w.done_parts and not row.done_btn.isChecked()

    w.set_dark(True)
    w.set_dark(False)

    # limpar tudo volta para a tela inicial
    w.clear_all(ask=False)
    assert w.parts == [] and w.placements == [] and w.files == []
    assert w.canvas.mode == "empty" and w.stack.currentIndex() == 0
    assert not w.btn_nest.isEnabled() and not w.btn_export.isEnabled()
    w.dirty = False
    w.close()


def test_salvamento_automatico_e_recuperacao(app, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, str(tmp_path / "cfg"))
    from app.ui import main_window as mw
    from app.core.models import Placement
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(mw.MainWindow, "autosave_path", staticmethod(lambda: str(tmp_path / "auto.sindri")))
    w = mw.MainWindow()
    w.load_files([fx("simples.dxf")])
    w.placements = [Placement(w.parts[0].id, 0, 0, 80, 80, 0, False)]
    w.n_sheets = 1
    w.cut_sheets = {0}
    w.autosave()
    assert os.path.isfile(tmp_path / "auto.sindri")
    w2 = mw.MainWindow()
    w2.check_autosave_on_start()               # não fechou normalmente -> oferece recuperar
    assert w2.placements and w2.cut_sheets == {0} and w2.project_path is None
    w.dirty = w2.dirty = False
    w.close()
    w2.close()
