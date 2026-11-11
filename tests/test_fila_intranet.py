"""Fila do dia (lotes propostos a partir da lista) e recado de retirada (fechar o ciclo com o aluno)."""
import json
import os
import time

import pytest
from PySide6.QtWidgets import QMessageBox

from app.core.intranet import find_request, pickup_note, ready_codes
from app.core.queue_plan import plan_lines, plan_queue, summary_from_detail
from tests.conftest import fx


def _it(code, data, mats, seal="ok", **k):
    return dict({"code": code, "data": data, "materials": mats, "seal": seal}, **k)


# ------------------------------------------------------------------ núcleo: plano da fila
def test_fila_ordem_de_chegada_e_por_material():
    items = [
        _it("30", "03/10/2026 10:00:00", {"MDF 3mm": 1}),
        _it("10", "01/10/2026 08:00:00", {"MDF 3mm": 2, "MDF 6mm": 1}),
        _it("20", "02/10/2026 09:00:00", {"Acrílico cast 3mm": 1}),
        _it("40", "04/10/2026 11:00:00", {"MDF 6mm": 3, "MDF 3mm": 1}),
    ]
    plan = plan_queue(items, max_per_lot=8)
    assert [(lot.number, lot.material, lot.codes) for lot in plan.lots] == [
        (1, "MDF 3mm", ["10", "30"]), (2, "Acrílico cast 3mm", ["20"]), (3, "MDF 6mm", ["40"])]
    lot1 = plan.lots[0]
    assert lot1.per_material == {"MDF 3mm": (2, 3), "MDF 6mm": (1, 1)}
    assert lot1.files == 4 and not lot1.full
    assert "ordem de chegada" in lot1.reason and "MDF 6mm, em placas próprias" in lot1.reason
    assert plan.planned == 4 and not plan.skipped and not plan.pending
    assert plan_lines(plan)[0] == "Lote 1 · 10, 30 · MDF 3mm: 2 solicitações, 3 arquivos; MDF 6mm: 1 solicitação, 1 arquivo"


def test_fila_tamanho_maximo_e_quem_fica_de_fora():
    items = [_it(str(c), f"0{c}/10/2026", {"MDF 3mm": 1}) for c in range(1, 6)]
    items += [_it("7", "01/09/2026", {"PVC 2mm": 1}, seal="bloqueado", seal_reasons=["PVC 2mm: libera cloro"]),
              _it("8", "02/09/2026", {"MDF 3mm": 1}, open=True),
              _it("9", "03/09/2026", {}),
              _it("11", "04/09/2026", None),
              _it("12", "05/09/2026", {"MDF 3mm": 1}, seal="atencao")]     # atenção entra (o técnico confere)
    plan = plan_queue(items, max_per_lot=2)
    assert [lot.codes for lot in plan.lots] == [["12", "1"], ["2", "3"], ["4", "5"]]
    assert plan.lots[0].full and "lote cheio (máx. 2)" in plan.lots[0].reason
    skipped = {s.code: s.reason for s in plan.skipped}
    assert skipped["7"].startswith("⛔ bloqueada") and "cloro" in skipped["7"]
    assert skipped["8"] == "já está aberta no Sindri"
    assert "sem arquivo DXF" in skipped["9"]
    assert plan.pending == ["11"]
    assert plan_queue([], 0).lots == [] and plan_queue(items, 0).max_per_lot == 8


def test_resumo_a_partir_do_detalhe():
    from app.core.intranet import parse_detail
    from app.core.manufacturability import Seal
    d = parse_detail({"codigo": 5, "arquivos": [
        {"nome": "a.dxf", "href": "a.dxf", "material": "MDF 3mm"},
        {"nome": "b.dxf", "href": "b.dxf", "material": "MDF 3mm"},
        {"nome": "c.png", "href": "c.png", "material": "MDF 3mm"}]})
    s = summary_from_detail(5, {"data": "01/10/2026", "nome": "Aluno"}, d, Seal("atencao", ["x"]))
    assert s == {"code": "5", "data": "01/10/2026", "nome": "Aluno", "materials": {"MDF 3mm": 2},
                 "seal": "atencao", "seal_reasons": ["x"], "open": False}
    assert summary_from_detail(6, None, None)["materials"] is None


# ------------------------------------------------------------------ núcleo: recado de retirada
LOTE = {"batch": True, "code": "8759 + 8760", "nome": "2 solicitações",
        "requests": [{"code": 8759, "nome": "Ana Exemplo", "rm": "1", "projeto": "Ponte de palito"},
                     {"code": 8760, "nome": "", "rm": "2", "projeto": ""}]}


def test_recado_de_retirada_e_prontas():
    note = pickup_note(LOTE, "8759")
    assert note == ("Olá, Ana! Sua solicitação nº 8759 (Ponte de palito) está pronta para retirada no "
                    "Laboratório Maker.")
    assert "RM" not in note and "Exemplo" not in note            # só primeiro nome, sem RM
    assert pickup_note(LOTE, 8760) == "Olá! Sua solicitação nº 8760 está pronta para retirada no Laboratório Maker."
    single = {"code": 42, "nome": "Bia", "projeto": "Caixa"}
    assert pickup_note(single, 42).startswith("Olá, Bia! Sua solicitação nº 42 (Caixa)")
    assert find_request(LOTE, 8760)["rm"] == "2" and find_request(LOTE, 1) is None
    assert ready_codes(LOTE, {"8760", "8759"}, False) == ["8759", "8760"]       # ordem do lote
    assert ready_codes(LOTE, {"8760"}, False) == ["8760"]
    assert ready_codes(single, set(), True) == ["42"] and ready_codes(single, set(), False) == []
    assert ready_codes(None, {"1"}, True) == []


# ------------------------------------------------------------------ interface: botão "Fila do dia"
def _pump(app, cond, limit=30):
    t = time.time()
    while time.time() - t < limit:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def _dialog(tmp_path):
    try:
        import PySide6.QtWebEngineWidgets  # noqa: F401
    except Exception:
        pytest.skip("QtWebEngine indisponível")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, str(tmp_path / "cfg"))
    from app.ui.intranet import IntranetDialog
    from tests.test_intranet import _mock_server
    srv = _mock_server()
    d = IntranetDialog(None, start_url=f"http://127.0.0.1:{srv.server_address[1]}/intranet_mock.html",
                       base_folder=str(tmp_path / "solic"))
    d.show()
    if not _pump(app, lambda: len(d._rows) >= 3, 40):
        d.deleteLater()
        srv.shutdown()
        pytest.skip("QtWebEngine não renderiza neste ambiente")
    assert _pump(app, lambda: len(d._rows) == 5 and not d._crawl.isActive(), 40)
    return app, d, srv


def test_fila_do_dia_na_pagina_simulada(tmp_path):
    from app.core.manufacturability import Seal, SealStore, default_seal_path
    # 8702 já passou pelo Sindri e ficou bloqueada: fica de fora sem precisar abrir
    SealStore(default_seal_path()).put_many({"8702": Seal("bloqueado", ["PVC: material proibido"])})
    app, d, srv = _dialog(tmp_path)
    try:
        # lista atual: Aguardando + Corte Laser = 8759, 8702, 8701
        assert sorted(r["codigo"] for r in d._visible_rows()) == ["8701", "8702", "8759"]
        d.queue_max.setValue(1)
        d.plan_day_queue()
        assert d._fetch_mode == "queue" and d._fetch_total == 2          # 8702 não é aberta
        assert not d.btn_queue.isEnabled()
        assert _pump(app, lambda: not d._fetch_queue and d.queue_plan is not None
                     and len(d.queue_plan.lots) == 2, 40)
        plan = d.queue_plan
        # ordem de chegada: 8701 (15/09) antes de 8759 (01/10); MDF 6mm tem mais arquivos na solicitação
        assert [lot.codes for lot in plan.lots] == [["8701"], ["8759"]]
        assert plan.lots[0].material == "MDF 6mm"
        assert plan.lots[0].per_material == {"MDF 3mm": (1, 1), "MDF 6mm": (1, 2)}
        assert [s.code for s in plan.skipped] == ["8702"] and plan.skipped[0].reason.startswith("⛔")
        assert set(d.queue_buttons) == {1, 2} and d.btn_queue.isEnabled()
        assert not os.path.exists(str(tmp_path / "solic"))              # planejar não baixa nada
        # tamanho maior junta as duas num lote só (sem ler de novo)
        d.queue_max.setValue(8)
        assert [lot.codes for lot in d.queue_plan.lots] == [["8701", "8759"]]
        # "Abrir lote 1": marca na ordem do lote e envia para o Sindri
        assert d.open_queue_lot(1)
        assert d._checked == ["8701", "8759"]
        assert _pump(app, lambda: d.result() == 1, 60)
        assert [det.code for det, _ in d.batch_result] == [8701, 8759]
        assert all(len(fs) == 3 for _, fs in d.batch_result)
        # reaberta com o lote já no Sindri: o plano continua à vista, sem as que já foram abertas
        d.setResult(0)
        d.set_open_codes(["8701", "8759"])
        assert d._queue_shown and d.queue_plan.lots == []
        assert {s.code for s in d.queue_plan.skipped} == {"8701", "8702", "8759"}
    finally:
        d.deleteLater()
        srv.shutdown()


def test_open_request_abre_no_site(tmp_path):
    app, d, srv = _dialog(tmp_path)
    try:
        d.open_request(8700)
        assert d.search.text() == "8700" and d.btn_page.isChecked()
        assert [d.list.item(i, 0).text() for i in range(d.list.rowCount())] == ["8700"]   # busca em todos os status
        assert _pump(app, lambda: d.detail is not None and d.detail.code == 8700, 40)
        assert "à mão" in d.state.text()
        assert not os.path.exists(str(tmp_path / "solic"))
    finally:
        d.deleteLater()
        srv.shutdown()


def test_open_request_antes_do_login_fica_pendente(tmp_path):
    try:
        import PySide6.QtWebEngineWidgets  # noqa: F401
    except Exception:
        pytest.skip("QtWebEngine indisponível")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from app.ui.intranet import IntranetDialog
    d = IntranetDialog(None, start_url="about:blank", base_folder=str(tmp_path / "solic"))
    d.open_request("8759")
    assert d._pending_open == "8759" and "login" in d.state.text()
    d.open_request("abc")                                  # ignora o que não é nº
    assert d._pending_open == "8759"
    d.plan_day_queue()                                     # lista vazia: só avisa
    assert "nenhuma solicitação" in d.state.text()
    d.deleteLater()


# ------------------------------------------------------------------ interface: solicitação pronta
@pytest.fixture
def window(monkeypatch):
    from app.ui.main_window import MainWindow
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.Yes))
    w = MainWindow(workers=0)
    w.show()
    yield w
    w.dirty = False
    w.close()
    w.deleteLater()


def test_solicitacao_pronta_no_checklist(window, monkeypatch):
    from PySide6.QtWidgets import QApplication
    a, b = fx("furos.dxf"), fx("simples.dxf")
    tags = {os.path.abspath(a): "8759", os.path.abspath(b): "8760"}
    assert window.load_files([a, b], tags=tags, request_info=json.loads(json.dumps(LOTE)))
    assert window.ready_requests() == [] and window.request_ready_actions("8759") == []
    assert window.show_ready_menu() is None and "Nenhuma solicitação pronta" in window.statusBar().currentMessage()
    for p in [p for p in window.parts if p.tag == "8759"]:
        window.on_part_done(p.id, True)
    assert window.ready_requests() == ["8759"]
    assert "8759 pronta ✓" in window.statusBar().currentMessage()
    acts = window.request_ready_actions("8759")
    assert [t for t, _ in acts] == ["Copiar recado de retirada", "Abrir na intranet"]
    acts[0][1]()
    assert QApplication.clipboard().text() == pickup_note(LOTE, "8759")
    opened = []
    monkeypatch.setattr(type(window), "open_request_in_intranet", lambda self, c: opened.append(c))
    window.request_ready_actions("8759")[1][1]()
    assert opened == ["8759"]
    menu = window.show_ready_menu()
    assert [x.text() for x in menu.actions()] == ["Pronta ✓  8759"]
    menu.close()
    window.on_request_filter("8759")
    assert "pronta ✓" in window.statusBar().currentMessage()
    window.on_part_done(next(p.id for p in window.parts if p.tag == "8759"), False)
    assert window.ready_requests() == []


def test_cartao_da_solicitacao_pronta_tem_menu_de_retirada():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QPushButton, QWidget
    from app.ui.parts_panel import PartsPanel
    QApplication.instance() or QApplication([])
    calls = []

    class Win(QWidget):
        def request_ready_actions(self, code):
            return [("Copiar recado de retirada", lambda: calls.append(code))] if code == "8759" else []

    win = Win()
    panel = PartsPanel(win)
    ready, other = QPushButton(panel), QPushButton(panel)
    ready.setToolTip("Solicitação 8759")
    panel._ready_menu(ready, "8759")
    panel._ready_menu(other, "8760")
    assert ready.contextMenuPolicy() == Qt.CustomContextMenu and "Pronta ✓" in ready.toolTip()
    assert other.contextMenuPolicy() != Qt.CustomContextMenu
    menu = ready.findChild(__import__("PySide6.QtWidgets", fromlist=["QMenu"]).QMenu)
    menu.actions()[0].trigger()
    assert calls == ["8759"]
    win.deleteLater()
