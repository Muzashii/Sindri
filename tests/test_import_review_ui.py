"""Painel de Conferência: avisos sem janela modal, desenho por arquivo e correções em lote."""
import os

import pytest
from PySide6.QtWidgets import QMessageBox

from app.core import import_issues as ii
from tests.conftest import fx
from tests.test_import_review import _gappy


@pytest.fixture
def window(monkeypatch):
    from app.ui.main_window import MainWindow
    shown = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: shown.append(self.windowTitle()) or 0)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append("warning") or QMessageBox.No)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: shown.append("critical"))
    w = MainWindow(workers=0)
    w.resize(1400, 860)
    w.show()
    w._shown_dialogs = shown
    yield w
    w.dirty = False
    w.close()
    w.deleteLater()


@pytest.fixture
def lote(tmp_path):
    a = _gappy(str(tmp_path / "Parede.dxf"))
    b = _gappy(str(tmp_path / "Chao.dxf"), gap=0.2)
    tags = {os.path.abspath(a): "48213", os.path.abspath(b): "48230", os.path.abspath(fx("duplicadas.dxf")): "48230"}
    info = {"batch": True, "requests": [{"code": "48213", "nome": "Ana"}, {"code": "48230", "nome": "Bruno"}]}
    return [a, b, fx("duplicadas.dxf")], tags, info


def _open_parts(w):
    return sum(1 for p in w.parts if p.is_open)


def test_avisos_vao_para_o_painel_sem_janela(window, lote):
    files, tags, info = lote
    window.load_files(files, tags=tags, request_info=info)
    assert "Avisos da importação" not in window._shown_dialogs
    assert window.review_panel.isVisible()
    assert len(window.review_panel.pending()) == 2
    assert window.btn_review_panel.isVisible() and "(2)" in window.btn_review_panel.text()
    # um quadro com nome para cada arquivo, agrupado pela solicitação na árvore
    assert set(window.canvas.preview_frames) == {os.path.abspath(f) for f in files}
    tree = window.review_panel.tree
    assert [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())] == ["48213 · Ana", "48230 · Bruno"]
    # aba Encaixe esconde o painel; voltar ao original mostra de novo
    window.tabs.setCurrentIndex(1)
    assert not window.review_panel.isVisible()
    window.tabs.setCurrentIndex(0)
    assert window.review_panel.isVisible()


def test_fechar_aberturas_em_lote_e_desfazer(window, lote):
    files, tags, info = lote
    window.load_files(files, tags=tags, request_info=info)
    before = _open_parts(window)
    panel = window.review_panel
    panel.limit.setValue(0.5)
    assert panel.btn_close_all.text() == "Fechar 4 aberturas"    # 0,3 + 0,4 e 0,2 + 0,4
    panel._close_all()
    assert _open_parts(window) < before
    assert set(window.file_repairs) == {os.path.abspath(f) for f in files[:2]}
    assert any(pr.data.get("repair") for pr, _ in window.report.preview)
    # sobram as aberturas de 1,2 mm (uma por arquivo) e as linhas soltas
    left = [i for i in panel.pending() if i.kind == "open"]
    assert sorted(round(g.dist, 1) for i in left for g in i.gaps) == [1.2, 1.2]
    assert window.dirty
    window.undo_repairs([files[0]])
    assert os.path.abspath(files[0]) not in window.file_repairs
    assert any(round(g.dist, 1) == 0.3 for i in panel.pending() for g in i.gaps)


def test_esta_certo_proximo_e_recado(window, lote):
    files, tags, info = lote
    window.load_files(files, tags=tags, request_info=info)
    panel = window.review_panel
    first = window.next_review_issue() or panel.pending()[0]
    assert window.canvas.transform().m11() > 0 and not window.canvas._auto_fit   # aproximou do problema
    assert window.statusBar().currentMessage().startswith(os.path.basename(first.file))
    window.set_issue_acks([first.key], True)
    assert first.key not in {i.key for i in panel.pending()} and len(panel.pending()) == 1
    window.copy_student_note(panel.pending())
    from PySide6.QtWidgets import QApplication
    note = QApplication.clipboard().text()
    other = {os.path.basename(f) for f in files[:2]} - {os.path.basename(first.file)}
    assert other.pop() in note and os.path.basename(first.file) not in note
    # clique no nome do arquivo no desenho seleciona o arquivo no painel
    window.on_preview_file_clicked(files[2])
    from app.ui.review_panel import ROLE
    sel = panel.tree.selectedItems()
    assert len(sel) == 1 and sel[0].data(0, ROLE) == ("file", os.path.abspath(files[2]))


def test_projeto_guarda_correcoes_e_conferidos(window, lote, tmp_path):
    files, tags, info = lote
    window.load_files(files, tags=tags, request_info=info)
    panel = window.review_panel
    panel.limit.setValue(0.5)
    panel._close_all()
    key = panel.pending()[0].key
    window.set_issue_acks([key], True)
    pending = len(panel.pending())
    path = str(tmp_path / "lote.sindri")
    window._write_project(path)
    window.dirty = False
    window.clear_all(ask=False)
    assert window.file_repairs == {} and not window.review_panel.isVisible()
    window.open_project(path)
    assert set(window.file_repairs) == {os.path.abspath(f) for f in files[:2]}
    assert key in window.issue_acks and len(window.review_panel.pending()) == pending


def test_sem_avisos_nao_mostra_painel(window):
    window.load_files([fx("simples.dxf")])
    assert not window.review_panel.isVisible() and not window.btn_review_panel.isVisible()
    assert ii.summarize(window.report.issues)[ii.CHECK] == 0
