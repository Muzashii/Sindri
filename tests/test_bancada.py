"""Fluxo de bancada: RDWorks sem fechar quando os valores não mudam, C com ciclo completo, tempo real ×
estimado, modo bancada e atualização por versão marcada."""
import json

import pytest

from app.core import laser, updater
from app.core import machine_time as mt
from app.core.version import app_version, version_label


def test_config_ja_aplicado_dispensa_fechar_o_rdworks(tmp_path):
    from tests.test_laser import fake_config
    data = fake_config()
    values = {1: (20.0, 40.0, 60.0), 2: (300.0, 10.0, 15.0)}
    assert not laser.already_applied(data, values)
    new = laser.patch(data, values)
    assert laser.already_applied(new, values)
    assert not laser.already_applied(new, {1: (21.0, 40.0, 60.0)})      # outra velocidade: precisa gravar
    assert not laser.already_applied(b"lixo", values) and not laser.already_applied(new, {})
    assert not laser.config_already_applied(str(tmp_path / "nao_existe"), values)
    cfg = tmp_path / "config"
    cfg.write_bytes(new)
    assert laser.config_already_applied(str(cfg), values)


def test_fator_de_tempo_real(tmp_path):
    assert mt.correction_factor([]) == (1.0, 0)
    hist = [{"estimado_s": 600, "real_s": 720}, {"estimado_s": 300, "real_s": 390}]
    assert mt.correction_factor(hist) == (1.0, 0)                       # menos de 3 medidas: não calibra
    hist.append({"estimado_s": 100, "real_s": 110})
    hist.append({"estimado_s": 100, "real_s": 5})                       # marcou sem cortar: ignorada
    hist.append({"estimado_s": 100, "real_s": 5 * 3600})                # esqueceu de marcar: ignorada
    f, n = mt.correction_factor(hist)
    assert n == 3 and f == pytest.approx(1.2)
    assert mt.correction_factor([{"estimado_s": 10, "real_s": 3000}] * 3)[0] == 3.0   # limitado a 3×
    path = str(tmp_path / "gestao" / mt.HISTORY_NAME)
    assert mt.append_time_history(path, {"id": "a", "estimado_s": 600, "real_s": 700})
    assert not mt.append_time_history(path, {"id": "b", "estimado_s": 600, "real_s": 3})
    assert [h["id"] for h in mt.load_time_history(path)] == ["a"]
    assert mt.remove_time_entry(path, "a") and mt.load_time_history(path) == []
    (tmp_path / "ruim.json").write_text("{", encoding="utf-8")
    assert mt.load_time_history(str(tmp_path / "ruim.json")) == []
    assert mt.fmt_duration(840) == "14 min" and mt.fmt_duration(3900) == "1 h 05 min"
    assert mt.fmt_clock(207) == "03:27"


# ------------------------------------------------------------------ atualizador por tag
class _Resp:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def read(self):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _net(monkeypatch, tags, main_sha="m" * 40, compare="behind"):
    def fake(req, timeout=0):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "/tags" in url:
            return _Resp(tags)
        if "/compare/" in url:
            return _Resp({"status": compare})
        return _Resp({"sha": main_sha, "commit": {"message": "msg", "committer": {"date": "2026-11-10T00:00:00Z"}}})
    monkeypatch.setattr(updater.urllib.request, "urlopen", fake)


def _tag(name, sha):
    return {"name": name, "commit": {"sha": sha}}


def test_atualizador_usa_a_maior_tag_e_cai_no_main_sem_tags(tmp_path, monkeypatch):
    root = str(tmp_path)
    monkeypatch.setattr(updater, "is_git_checkout", lambda r: False)
    (tmp_path / updater.VERSION_FILE).write_text("a" * 40 + "\n", encoding="utf-8")

    _net(monkeypatch, [])                                               # sem tags: como sempre foi
    remote = updater.update_available(root=root)
    assert remote.sha == "m" * 40 and remote.tag == ""

    _net(monkeypatch, [_tag("v1.9.0", "9" * 40), _tag("exe-12", "e" * 40), _tag("v1.10.0", "1" * 40),
                       _tag("v2.0.0-rc1", "r" * 40)])
    remote = updater.update_available(root=root)
    assert remote.tag == "v1.10.0" and remote.sha == "1" * 40 and remote.number == "1.10.0"

    (tmp_path / updater.VERSION_FILE).write_text("1" * 40 + " v1.10.0\n", encoding="utf-8")
    assert updater.update_available(root=root) is None                                  # já está nela
    assert updater.local_tag(root) == "v1.10.0" and updater.local_version(root) == "1" * 40
    assert app_version(root) == "1.10.0" and version_label(root) == "Sindri 1.10.0 (1111111)"

    (tmp_path / updater.VERSION_FILE).write_text("f" * 40 + " v1.11.0\n", encoding="utf-8")
    assert updater.update_available(root=root) is None                                  # nunca rebaixa

    (tmp_path / updater.VERSION_FILE).write_text("d" * 40 + "\n", encoding="utf-8")
    _net(monkeypatch, [_tag("v1.10.0", "1" * 40)], compare="ahead")
    assert updater.update_available(root=root) is None                                  # instalada pelo main já passou da tag
    _net(monkeypatch, [_tag("v1.10.0", "1" * 40)], compare="behind")
    assert updater.update_available(root=root).tag == "v1.10.0"
    assert updater.parse_tag("v1.2") is None and updater.parse_tag("v1.2.3") == (1, 2, 3)


# ------------------------------------------------------------------ interface
def test_c_faz_o_ciclo_e_modo_bancada(tmp_path, monkeypatch):
    import time
    from PySide6.QtWidgets import QApplication, QMessageBox
    from app.ui.main_window import MainWindow
    from tests.conftest import fx
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Discard))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.Discard))
    w = MainWindow(workers=0)
    try:
        w.show()
        w.load_files([fx("exemplo_lab.dxf")])
        w.settings_panel.stop_ni.setValue(1)
        w.start_nest()
        for _ in range(600):
            app.processEvents()
            if w.worker is None:
                break
            time.sleep(0.05)
        order = w.sheet_index().ordered
        assert len(order) >= 2
        opened = []
        monkeypatch.setattr(w, "open_sheet_in_rdworks", lambda si: opened.append(si))

        assert not w.open_next_on_cut()                                 # padrão: só vai para a próxima
        w.goto_sheet(order[0])
        w.toggle_current_cut()
        assert order[0] in w.cut_sheets and opened == [] and w.canvas.current_sheet() == order[1]
        w.on_sheet_cut(order[0], False)

        w.set_open_next_on_cut(True)
        assert w.a_open_next.isChecked()
        w.goto_sheet(order[0])
        w.toggle_current_cut()
        assert opened == [order[1]]                                     # um C: marcou, avançou e abriu a próxima
        w.on_sheet_cut(order[0], False)
        w.set_open_next_on_cut(False)

        w.toggle_bench_mode()
        bench = w._bench
        assert bench.isVisible() and "Placa" in bench.title.text() and bench.btn_cut.isEnabled()
        first = bench.si
        bench.mark_cut()
        assert first in w.cut_sheets and bench.si != first
        bench.open_current()
        assert opened[-1] == bench.si
        bench.close()
        assert not bench.isVisible()
    finally:
        w.close()
