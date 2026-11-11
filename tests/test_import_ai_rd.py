"""Importação de .ai (Illustrator) e .rd (arquivo de máquina do RDWorks) pelo mesmo fluxo do DXF.

Os arquivos são sintéticos, gerados aqui. O .rd segue o formato documentado publicamente; falta validar
com um arquivo salvo pelo RDWorks do laboratório.
"""
import os

import pytest

from app.core.ai_import import PT_MM, read_ai
from app.core.dxf_import import DXFImportError, is_drawing, read_drawing
from app.core.models import NestParams
from app.core.part_builder import import_files
from app.core.rd_import import decode, read_rd


# ------------------------------------------------------------------ .ai (PDF)
def _pdf(content: bytes) -> bytes:
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 600] /Contents 4 0 R /Resources << >> >>",
            b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream"]
    out = bytearray(b"%PDF-1.4\n")
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 5\n0000000000 65535 f \n" + b"".join(b"%010d 00000 n \n" % o for o in offs)
    out += b"trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n" + str(xref).encode() + b"\n%%EOF\n"
    return bytes(out)


K = 0.5522847498


def _circle(cx, cy, r) -> str:
    k = K * r
    return (f"{cx + r} {cy} m {cx + r} {cy + k} {cx + k} {cy + r} {cx} {cy + r} c "
            f"{cx - k} {cy + r} {cx - r} {cy + k} {cx - r} {cy} c "
            f"{cx - r} {cy - k} {cx - k} {cy - r} {cx} {cy - r} c "
            f"{cx + k} {cy - r} {cx + r} {cy - k} {cx + r} {cy} c h S\n")


@pytest.fixture
def ai_file(tmp_path):
    content = ("0 0 600 600 re W n\n"                       # recorte da prancheta: não é desenho
               "q 2 0 0 2 10 20 cm\n"                       # escala 2× e deslocamento
               "1 0 0 RG 0 0 100 50 re S\n" +               # retângulo vermelho 100×50 pt → 200×100 pt
               _circle(50, 25, 10) +                        # furo na mesma cor (4 Béziers)
               "Q\n"
               "0 0 0 RG 300 300 m 400 300 l S\n"
               "BT ET\n").encode()
    p = tmp_path / "desenho.ai"
    p.write_bytes(_pdf(content))
    return str(p)


def test_ai_pdf_vira_primitivas_em_mm(ai_file):
    raw = read_drawing(ai_file)
    kinds = sorted(p.kind for p in raw.prims)
    assert kinds == ["LINE", "POLY", "SPLINE", "SPLINE", "SPLINE", "SPLINE"]      # o recorte ficou de fora
    rect = next(p for p in raw.prims if p.kind == "POLY")
    xs = [q[0] for q in rect.data["pts"]]
    ys = [q[1] for q in rect.data["pts"]]
    assert rect.data["closed"] and len(rect.data["pts"]) == 4
    assert max(xs) - min(xs) == pytest.approx(200 * PT_MM) and max(ys) - min(ys) == pytest.approx(100 * PT_MM)
    assert min(xs) == pytest.approx(10 * PT_MM) and min(ys) == pytest.approx(20 * PT_MM)
    assert rect.color == 1                                                         # vermelho exato do AutoCAD
    assert {p.color for p in raw.prims if p.kind == "SPLINE"} == {1}
    assert next(p for p in raw.prims if p.kind == "LINE").color == 7               # preto
    assert any("texto" in w for w in raw.warnings)
    assert raw.unit_factor == 1.0 and raw.size_mm > 70


def test_ai_entra_no_fluxo_de_pecas(ai_file):
    p = NestParams()
    rep = import_files([ai_file], p.join_tolerance, p.curve_tolerance, **p.import_kwargs())
    big = max(rep.parts, key=lambda part: part.outer.area)
    assert big.outer.area == pytest.approx(200 * PT_MM * 100 * PT_MM, rel=1e-3)
    assert len(big.holes) == 1                                                     # o círculo virou furo
    r = 20 * PT_MM
    assert big.holes[0].area == pytest.approx(3.14159265 * r * r, rel=0.02)


def test_ai_postscript_antigo(tmp_path):
    src = ("%!PS-Adobe-3.0\n%%Creator: Adobe Illustrator(TM) 8.0\n%%EndSetup\n"
           "0 1 1 0 K\n"                                    # traço vermelho (CMYK)
           "0 0 m\n100 0 L\n100 100 L\n0 100 L\n0 0 L\ns\n"
           "0 0 0 1 K\n20 50 m\n20 60 30 70 40 70 c\nS\n"
           "%%Trailer\n%%EOF\n")
    f = tmp_path / "antigo.ai"
    f.write_text(src, encoding="latin-1")
    raw = read_ai(str(f))
    poly = next(p for p in raw.prims if p.kind == "POLY")
    assert poly.data["closed"] and len(poly.data["pts"]) == 4 and poly.color == 1
    assert any(p.kind == "SPLINE" for p in raw.prims)
    assert raw.size_mm == pytest.approx(100 * PT_MM * 2 ** 0.5, rel=0.01) or raw.size_mm > 35


def test_ai_invalido_e_rld_explicam_o_problema(tmp_path):
    bad = tmp_path / "ruim.ai"
    bad.write_bytes(b"isto nao e um desenho")
    with pytest.raises(DXFImportError, match="Illustrator"):
        read_drawing(str(bad))
    rld = tmp_path / "projeto.rld"
    rld.write_bytes(b"\x00\x01")
    assert is_drawing(str(rld))
    with pytest.raises(DXFImportError, match="projeto do RDWorks"):
        read_drawing(str(rld))
    p = NestParams()
    rep = import_files([str(bad)], p.join_tolerance, p.curve_tolerance, **p.import_kwargs())
    assert not rep.parts and rep.warnings                                          # não derruba o lote


# ------------------------------------------------------------------ .rd
def _scramble(data: bytes, magic: int) -> bytes:
    out = bytearray()
    for b in data:
        b ^= (b >> 7) & 0xFF
        b ^= (b << 7) & 0xFF
        b ^= (b >> 7) & 0xFF
        b ^= magic
        out.append((b + 1) & 0xFF)
    return bytes(out)


def _a(mm: float) -> bytes:                                 # 35 bits, micrômetros
    v = int(round(mm * 1000)) & ((1 << 35) - 1)
    return bytes((v >> s) & 0x7F for s in (28, 21, 14, 7, 0))


def _r(mm: float) -> bytes:                                 # 14 bits com sinal
    v = int(round(mm * 1000)) & 0x3FFF
    return bytes(((v >> 7) & 0x7F, v & 0x7F))


def _pw(pct: float) -> bytes:
    v = int(round(pct / 100 * 16383))
    return bytes(((v >> 7) & 0x7F, v & 0x7F))


def _rd_job(magic=0x88) -> bytes:
    j = bytearray(b"\xd8\x12\xf0\xf1\x02\x00\xd8\x00")
    j += b"\xc9\x04\x00" + _a(20.0) + b"\xc6\x31\x00" + _pw(40) + b"\xc6\x32\x00" + _pw(60)
    j += b"\xca\x06\x00" + _a(0x0000FF / 1000)              # camada 0: vermelho (BGR)
    j += b"\xc9\x04\x01" + _a(300.0) + b"\xc6\x32\x01" + _pw(15)
    j += b"\xca\x06\x01" + _a(0xFF0000 / 1000)              # camada 1: azul
    j += b"\xca\x02\x00"                                    # camada 0: contorno fechado (absoluto + relativo)
    j += b"\x88" + _a(10) + _a(10)
    j += b"\xa8" + _a(60) + _a(10)
    j += b"\xab" + _r(5)                                   # relativo: só até ±8,191 mm
    j += b"\xa8" + _a(14) + _a(15)
    j += b"\xa9" + _r(-4) + _r(-5)
    j += b"\xca\x02\x01"                                    # camada 1: risco
    j += b"\x88" + _a(20) + _a(20)
    j += b"\x89" + _r(5) + _r(5)
    j += b"\xaa" + _r(8)
    j += b"\xeb\xe7\x00\xd7"
    return _scramble(bytes(j), magic)


@pytest.mark.parametrize("magic", [0x88, 0x11])
def test_rd_decodifica_camadas_e_caminhos(tmp_path, magic):
    job = decode(_rd_job(magic))
    assert job["magic"] == magic and job["complete"]
    assert job["layers"][0] == {"speed": 20.0, "power_min": 40.0, "power_max": 60.0, "color": (255, 0, 0)}
    assert job["layers"][1]["speed"] == 300.0 and job["layers"][1]["color"] == (0, 0, 255)
    (l0, sq), (l1, ln) = job["paths"]
    assert (l0, l1) == (0, 1)
    assert sq == [(10, -10), (60, -10), (60, -15), (14, -15), (10, -10)]   # Y virado para cima
    assert ln == [(25, -25), (33, -25)]


def test_rd_entra_no_fluxo_de_pecas(tmp_path):
    f = tmp_path / "trabalho.rd"
    f.write_bytes(_rd_job())
    raw = read_rd(str(f))
    poly = next(p for p in raw.prims if p.kind == "POLY")
    assert poly.data["closed"] and len(poly.data["pts"]) == 4 and poly.color == 1 and poly.layer == "Camada 1"
    assert next(p for p in raw.prims if p.kind == "LINE").color == 5
    assert raw.meta["laser"]["0"]["speed"] == 20.0
    assert any("20 mm/s" in w and "40–60%" in w for w in raw.warnings)
    p = NestParams()
    rep = import_files([str(f)], p.join_tolerance, p.curve_tolerance, **p.import_kwargs())
    closed = [pt for pt in rep.parts if not pt.is_open]            # o risco solto vira peça aberta, com aviso
    assert len(closed) == 1 and closed[0].outer.area == pytest.approx(50 * 5 - 4 * 5 / 2, rel=1e-3)
    assert rep.extra["file_meta"][os.path.abspath(str(f))]["format"] == "rd"


def test_rd_invalido_explica(tmp_path):
    f = tmp_path / "lixo.rd"
    f.write_bytes(bytes(range(256)) * 4)
    with pytest.raises(DXFImportError, match="RDWorks"):
        read_drawing(str(f))


# ------------------------------------------------------------------ interface
def test_soltar_ai_e_rd_na_janela(tmp_path, ai_file, monkeypatch):
    from PySide6.QtWidgets import QApplication, QMessageBox
    from app.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Discard))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.Discard))
    rd = tmp_path / "trabalho.rd"
    rd.write_bytes(_rd_job())
    w = MainWindow(workers=0)
    try:
        w.show()
        w.load_files([ai_file, str(rd)])
        assert len(w.parts) >= 2 and len(w.files) == 2
        proj = str(tmp_path / "lote.sindri")
        w.project_path = proj
        w.save_project()
        n = len(w.parts)
        w.open_project(proj)
        assert len(w.parts) == n

        # abrir pelo diálogo (ou soltar na janela) já começa o encaixe, se nada estiver pendente
        from PySide6.QtWidgets import QFileDialog
        limpo = tmp_path / "limpo.ai"
        limpo.write_bytes(_pdf(b"1 0 0 RG 0 0 200 100 re S\n0 0 150 150 re S\n"))
        monkeypatch.setattr(QFileDialog, "getOpenFileNames", staticmethod(lambda *a, **k: ([str(limpo)], "")))
        w.dirty = False
        w.open_dxf_dialog()
        import time
        for _ in range(100):
            QApplication.processEvents()
            if w.worker is not None or w.placements:
                break
            time.sleep(0.02)
        assert w.worker is not None or w.placements
        w.stop_nest()
        for _ in range(600):
            QApplication.processEvents()
            if w.worker is None:
                break
            time.sleep(0.05)
    finally:
        w.close()
