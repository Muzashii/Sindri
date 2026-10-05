import os

import pytest

from app.core.cleanup import downloaded_files, exported_files, human, remove_empty_dirs, total_size


def test_coleta_e_limpeza(tmp_path):
    base = tmp_path / "Solicitações"
    (base / "8759 - Aluno" / "MDF 3mm").mkdir(parents=True)
    f1 = base / "8759 - Aluno" / "MDF 3mm" / "a.dxf"
    f1.write_text("x" * 100)
    out = tmp_path / "saida"
    out.mkdir()
    (out / "lote_relatorio.pdf").write_text("pdf")
    (out / "lote_todas_placas.dxf").write_text("dxf")
    (out / "outro.pdf").write_text("nao mexe")
    assert downloaded_files(str(base)) == [str(f1)]
    rep = exported_files([], [str(out)], ("*_relatorio.pdf",))
    assert [os.path.basename(f) for f in rep] == ["lote_relatorio.pdf"]
    cut = exported_files([str(out / "lote_todas_placas.dxf")], [], ("*_todas_placas.dxf",))
    assert len(cut) == 1
    assert total_size([str(f1)]) == 100 and human(2048) == "2,0 KB"
    os.remove(f1)
    remove_empty_dirs(str(base))
    assert os.path.isdir(base) and os.listdir(base) == []


def test_menu_limpar(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, str(tmp_path / "cfg"))
    from app.ui import main_window as mw
    from app.ui.dialogs import settings
    base = tmp_path / "Solic"
    (base / "1 - A").mkdir(parents=True)
    (base / "1 - A" / "p.dxf").write_text("x")
    out = tmp_path / "out"
    out.mkdir()
    (out / "t_relatorio.pdf").write_text("x")
    (out / "t_todas_placas.dxf").write_text("x")
    settings().setValue("intranet/folder", str(base))
    settings().setValue("export/history", [str(out / "t_relatorio.pdf")])   # 1 item: vira str no Windows
    seen = {}

    class FakeDlg:
        def __init__(self, groups, parent=None):
            seen["groups"] = {k: len(f) for k, _, f, _ in groups}
            self.groups = groups

        def exec(self):
            return True

        def chosen(self):
            return [f for k, _, fs, d in self.groups if d for f in fs]

    monkeypatch.setattr(mw, "CleanupDialog", FakeDlg)
    w = mw.MainWindow()
    w.cleanup_files()
    assert seen["groups"] == {"down": 1, "rep": 1, "cut": 1}
    assert not (base / "1 - A" / "p.dxf").exists() and not (out / "t_relatorio.pdf").exists()
    assert (out / "t_todas_placas.dxf").exists()          # arquivo de corte: desmarcado por padrão
    w.close()
