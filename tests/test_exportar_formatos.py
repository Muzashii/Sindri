"""Botão "Exportar": DXF R2000/R12, SVG, PDF e AI na medida real, onde o técnico escolher; o botão
"Abrir no RDWorks" continua fazendo o que o Exportar fazia."""
import os
import re
import time

import ezdxf
import pytest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from app.core import vector_export as ve
from tests.conftest import fx


def _dxf(tmp_path):
    doc = ezdxf.new("R2000")
    m = doc.modelspace()
    m.add_lwpolyline([(0, 0), (100, 0), (100, 50), (0, 50)], close=True, dxfattribs={"color": 1})
    m.add_circle((50, 25), 10, dxfattribs={"color": 5})
    m.add_arc((20, 20), 5, 0, 90, dxfattribs={"color": 3})
    m.add_lwpolyline([(110, 0), (120, 0), (120, 10)], format="xyb", dxfattribs={"color": 7})
    m.add_text("nome")
    path = str(tmp_path / "t.dxf")
    doc.saveas(path)
    return path


def test_svg_na_medida_e_com_cores(tmp_path):
    out, skipped = ve.convert(_dxf(tmp_path), "svg", str(tmp_path / "t.svg"))
    svg = open(out, encoding="utf-8").read()
    assert 'width="120mm"' in svg and 'height="50mm"' in svg and 'viewBox="0 0 120 50"' in svg
    assert {"#ff0000", "#0000ff", "#00ff00", "#000000"} <= set(re.findall(r'stroke="(#[0-9a-f]{6})"', svg))
    assert svg.count(" C") >= 4                          # círculo e arco como curvas, não segmentos
    assert skipped == {"TEXT": 1}


def test_pdf_e_ai_na_medida_e_ai_volta_para_o_sindri(tmp_path):
    from pypdf import PdfReader

    from app.core.ai_import import read_ai
    src = _dxf(tmp_path)
    pdf, _ = ve.convert(src, "pdf", str(tmp_path / "t.pdf"))
    box = PdfReader(pdf).pages[0].mediabox
    assert float(box.width) == pytest.approx(120 * 72 / 25.4, rel=1e-4)
    assert float(box.height) == pytest.approx(50 * 72 / 25.4, rel=1e-4)
    ai, _ = ve.convert(src, "ai", str(tmp_path / "t.ai"))
    raw = read_ai(ai)
    rect = next(p for p in raw.prims if p.kind == "POLY" and p.color == 1)
    xs = [q[0] for q in rect.data["pts"]]
    assert max(xs) - min(xs) == pytest.approx(100, abs=1e-3)
    with pytest.raises(ValueError):
        ve.convert(src, "rd", str(tmp_path / "t.rd"))


@pytest.fixture
def window(monkeypatch, tmp_path):
    from app.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Discard))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.Yes))
    w = MainWindow(workers=0)
    w.show()
    w.load_files([fx("exemplo_lab.dxf")])
    w.settings_panel.stop_ni.setValue(1)
    w.start_nest()
    for _ in range(600):
        QApplication.processEvents()
        if w.worker is None:
            break
        time.sleep(0.05)
    monkeypatch.setattr(w, "confirm_color_ops", lambda force=False: True)
    yield w
    w.dirty = False
    w.close()


def _run_export(w, monkeypatch, tmp_path, **choice):
    from app.ui import export_files_dialog as efd

    def fake_exec(dlg):
        for key, combo in (("format", dlg.fmt), ("scope", dlg.scope)):
            if key in choice:
                combo.setCurrentIndex(combo.findData(choice[key]))
        dlg.report.setChecked(choice.get("report", False))
        dlg.folder.setText(str(tmp_path))
        dlg.base.setText("lote")
        dlg._accept()
        return QDialog.Accepted
    monkeypatch.setattr(efd.ExportFilesDialog, "exec", fake_exec)
    w.export_files()
    return sorted(os.listdir(tmp_path))


@pytest.mark.parametrize("fmt,ext", [("dxf2000", ".dxf"), ("dxf12", ".dxf"), ("svg", ".svg"), ("pdf", ".pdf"),
                                     ("ai", ".ai")])
def test_exportar_um_arquivo_por_placa_em_cada_formato(window, monkeypatch, tmp_path, fmt, ext):
    w = window
    opened = []
    monkeypatch.setattr(w, "_open_in_rdworks", lambda *a, **k: opened.append(a) or "")
    n = len(w.sheet_index().ordered)
    names = _run_export(w, monkeypatch, tmp_path / fmt, format=fmt, scope="por_placa")
    assert len(names) == n and all(x.endswith(ext) and "_placa" in x for x in names)
    assert not opened                                    # "Exportar" nunca abre o RDWorks
    if fmt == "dxf12":
        assert ezdxf.readfile(str(tmp_path / fmt / names[0])).dxfversion == "AC1009"
    assert "Abrir pasta" in w.banner_text.text()


def test_todas_num_arquivo_relatorio_e_placa_da_tela(window, monkeypatch, tmp_path):
    w = window
    names = _run_export(w, monkeypatch, tmp_path / "a", format="svg", scope="todas", report=True)
    assert names == ["lote_relatorio.pdf", "lote_todas_placas.svg"]
    again = _run_export(w, monkeypatch, tmp_path / "a", format="svg", scope="todas", report=True)
    assert "lote_2_todas_placas.svg" in again           # nada é sobrescrito
    w.goto_sheet(w.sheet_index().ordered[-1])
    one = _run_export(w, monkeypatch, tmp_path / "b", format="pdf", scope="tela", report=True)
    assert len(one) == 1 and one[0].endswith(".pdf") and "relatorio" not in one[0]


def test_botao_abrir_no_rdworks_faz_o_que_o_exportar_fazia(window, monkeypatch):
    w = window
    calls = []
    monkeypatch.setattr(w, "export", lambda *a, **k: calls.append(k))
    monkeypatch.setattr(w, "export_files", lambda: calls.append("arquivos"))
    assert "RDWorks" in w.btn_export.text() and w.btn_save_as.text() == "Exportar"
    w.btn_export.click()
    assert calls == [{"open_rd": True}]
    assert w.btn_save_as.isEnabled()
    w.btn_save_as.clicked.disconnect()
    w.btn_save_as.clicked.connect(w.export_files)
    w.btn_save_as.click()
    assert calls[-1] == "arquivos"
