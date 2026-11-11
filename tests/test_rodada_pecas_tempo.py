"""Botões "Excluir" e "Adicionar peças" no painel de peças, e tempo estimado de corte no rodapé."""
import pytest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from tests.conftest import fx


@pytest.fixture
def window(monkeypatch):
    from app.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Discard))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.Discard))
    w = MainWindow(workers=0)
    w.show()
    yield w
    import time
    for _ in range(40):                       # "Adicionar peças" começa o encaixe sozinho um instante depois
        QApplication.processEvents()
        time.sleep(0.02)
    if w.worker is not None:
        w.stop_nest(wait=True)
    w.dirty = False
    w.close()


def _nest(w):
    import time
    w.settings_panel.stop_ni.setValue(1)
    w.start_nest()
    for _ in range(600):
        QApplication.processEvents()
        if w.worker is None:
            break
        time.sleep(0.05)


def test_excluir_peca_tira_da_lista_e_do_encaixe_e_desfaz(window):
    w = window
    w.load_files([fx("exemplo_lab.dxf")])
    _nest(w)
    n = len(w.parts)
    victim = w.parts[1]
    row = next(r for r in w.parts_panel.rows if r.part.id == victim.id)
    assert row.del_btn.text() == "Excluir"
    row.del_btn.click()
    assert victim.id not in w.pmap and len(w.parts) == n - 1
    assert all(pl.part_id != victim.id for pl in w.placements)
    assert all(r.part.id != victim.id for r in w.parts_panel.rows) and w.dirty
    # reler o arquivo (ex.: mudar a tolerância) não traz a peça de volta
    w._import(w.files, keep_quantities=True)
    assert all(p.identity != victim.identity for p in w.parts) and len(w.parts) == n - 1
    # projeto salvo guarda a exclusão
    import os
    import tempfile
    path = os.path.join(tempfile.mkdtemp(), "p.sindri")
    w.project_path = path
    w.save_project()
    w.open_project(path)
    assert len(w.parts) == n - 1
    w._import(w.files, keep_quantities=True)
    assert len(w.parts) == n - 1
    # abrir o arquivo de novo (sem juntar) volta a lista inteira
    w.dirty = False
    w.load_files([fx("exemplo_lab.dxf")])
    assert len(w.parts) == n


def test_excluir_volta_com_desfazer(window):
    w = window
    w.load_files([fx("exemplo_lab.dxf")])
    _nest(w)
    before = ({p.id for p in w.parts}, len(w.placements))
    w.remove_part(w.parts[0].id)
    assert len(w.parts) == len(before[0]) - 1
    w.undo()
    assert {p.id for p in w.parts} == before[0] and len(w.placements) == before[1]
    assert len(w.parts_panel.rows) == len(before[0])


def test_adicionar_pecas_aceita_dxf_ai_rd(window, monkeypatch):
    w = window
    w.load_files([fx("exemplo_lab.dxf")])
    seen = {}

    def fake(parent, title, start, filt):
        seen["filter"] = filt
        return [fx("furos.dxf")], ""
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", staticmethod(fake))
    total = sum(p.quantity for p in w.parts)
    w.parts_panel.btn_add.click()
    assert all(ext in seen["filter"] for ext in ("*.dxf", "*.ai", "*.rd"))
    assert "Adicionar" in w.parts_panel.btn_add.text()
    assert len(w.files) == 2 and sum(p.quantity for p in w.parts) > total   # juntou, não substituiu


def test_rodape_mostra_tempo_de_corte(window):
    w = window
    cap, val = w._metric_caps["tempo de corte"]
    w._refresh_time_chip()
    assert val.text() == "—"
    w.load_files([fx("exemplo_lab.dxf")])
    _nest(w)
    w._refresh_time_chip()
    assert val.text().startswith("~") and cap.text() == "tempo de corte"
    assert "Placa 1" in val.toolTip() and "Total do lote" in val.toolTip()
    assert "tentativas" not in cap.text()
    first = w.sheet_index().ordered[0]
    w.on_sheet_cut(first, True)
    import time
    for _ in range(50):                                   # atualiza sozinho (sem chamar o refresh)
        QApplication.processEvents()
        time.sleep(0.02)
    if len(w.sheet_index().ordered) > 1:
        assert cap.text() == "falta cortar"
    for si in w.sheet_index().ordered:
        w.on_sheet_cut(si, True)
    w._refresh_time_chip()
    assert cap.text() == "tudo cortado"
    # janela estreita: o tempo continua à mostra
    w.resize(1024, 700)
    QApplication.processEvents()
    assert val.isVisible()
