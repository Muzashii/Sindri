import os

import ezdxf
import numpy as np

from app.core.photo import LEVEL_ACI, PhotoParams, trace, write_dxf


def test_degrade_vira_niveis_e_branco_nao_grava(tmp_path):
    img = np.tile(np.linspace(0, 1, 400), (100, 1))           # preto à esquerda, branco à direita
    p = PhotoParams(width_mm=100, line_mm=0.5, levels=4, dither=False, white_cut=0.05, mode="linhas")
    r = trace(img, p)
    assert r.levels.shape == (50, 200)
    row = r.levels[10]
    assert row[0] == 0 and row[-1] == -1                       # escuro = nível 0; branco não grava
    assert set(np.unique(row)) == {-1, 0, 1, 2, 3}
    assert all(0 <= x0 < x1 <= 100 + 1e-6 for _, _, x0, x1 in r.segments)
    path = write_dxf(r, p, str(tmp_path / "f.dxf"), outline=(300, 200))
    doc = ezdxf.readfile(path)
    lines = [e for e in doc.modelspace() if e.dxf.layer.startswith("FOTO_")]
    assert len(lines) == r.count
    assert {e.dxf.color for e in lines} == set(LEVEL_ACI[:4])
    assert p.level_powers() == [70.0, 53.3, 36.7, 20.0]


def test_pontilhado_preserva_o_tom_medio():
    img = np.full((80, 80), 0.5)
    p = PhotoParams(width_mm=40, line_mm=0.5, levels=2, dither=True, white_cut=0.0, mode="linhas")
    lv = trace(img, p).levels
    # metade escura: a média dos patamares gravados fica perto de 0.5
    dark = np.where(lv < 0, 0.0, np.where(lv == 0, 1.0, 0.5))
    assert abs(dark.mean() - 0.5) < 0.05


def test_aba_foto_gera_e_exporta(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from app.ui.dialogs import settings
    from app.ui.main_window import MainWindow
    img = QImage(120, 80, QImage.Format_RGB32)
    img.fill(QColor(255, 255, 255))
    for x in range(20, 100):
        for y in range(20, 60):
            img.setPixelColor(x, y, QColor(int(x * 2), int(x * 2), int(x * 2)))
    src = str(tmp_path / "foto.png")
    img.save(src)
    w = MainWindow(workers=0)
    w.set_mode(1)
    assert w.mode_stack.currentWidget() is w.photo_panel and not w.nest_actions.isVisibleTo(w)
    pp = w.photo_panel
    pp.w.setValue(60)
    pp.mode.setCurrentIndex(pp.mode.findData("linhas"))
    assert pp.open_image(src) and pp.wait_idle()
    assert pp.result.count > 0 and pp.h_lbl.text() == "40.0 mm"
    vals = pp.laser_values()
    assert len(vals) == pp.levels.value() and all(v[0] == pp.speed.value() for v in vals.values())
    out = tmp_path / "saida"
    out.mkdir()
    st = settings()
    st.setValue("photo/last_dir", str(out))
    st.setValue("export/open_rdworks", "false")
    pp.mode.setCurrentIndex(pp.mode.findData("imagem"))
    assert pp.wait_idle() and not pp.levels.isEnabled()
    pp.dither.setChecked(False)                       # tons de cinza: potência mín./máx.
    assert pp.wait_idle() and pp.pmin.isEnabled()
    assert list(pp.laser_values().values()) == [(pp.speed.value(), pp.pmin.value(), pp.pmax.value())]
    pp.dither.setChecked(True)                        # pontilhado: só preto e branco, potência única
    assert pp.wait_idle() and not pp.pmin.isEnabled()
    assert list(pp.laser_values().values()) == [(pp.speed.value(), pp.pmax.value(), pp.pmax.value())]
    assert set(np.unique(pp.result.dark)) <= {0.0, 1.0}
    w.export_photo()
    assert os.listdir(out) == ["foto_foto.bmp"]
    q = QImage(str(out / "foto_foto.bmp"))
    assert q.width() == round(60 / pp.line.value()) and abs(q.width() / q.dotsPerMeterX() * 1000 - 60) < 0.5
    assert "Foto exportada" in pp.msg.text() and "pontilhada" in pp.msg.text()
    assert {q.pixelColor(x, y).red() for x in range(0, q.width(), 7) for y in range(0, q.height(), 7)} <= {0, 255}
    pp.mode.setCurrentIndex(pp.mode.findData("linhas"))
    assert pp.wait_idle() and pp.levels.isEnabled()
    w.export_photo()
    assert sorted(os.listdir(out)) == ["foto_foto.bmp", "foto_foto.dxf"]
    w.set_mode(0)
    assert w.nest_actions.isVisibleTo(w)
    w.close()


def test_pontilhado_binario_reproduz_os_tons():
    from app.core.photo import dither_binary
    d = np.tile(np.linspace(0, 1, 500), (300, 1))
    burn = (dither_binary(d) == 0).astype(float)
    for i in range(5):
        assert abs(burn[:, i * 100:(i + 1) * 100].mean() - (0.1 + 0.2 * i)) < 0.03
