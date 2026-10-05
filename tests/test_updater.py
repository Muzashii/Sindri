import io
import os
import zipfile

import pytest

from app.core import updater


def _zip(files: dict[str, bytes], top="Sindri-abc/") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(top, b"")
        for name, data in files.items():
            z.writestr(top + name, data)
    return buf.getvalue()


def test_instala_por_cima_sem_tocar_no_ambiente(tmp_path):
    root = tmp_path
    (root / "app").mkdir()
    (root / "app" / "main.py").write_bytes(b"old")
    (root / "sindri.py").write_bytes(b"same")
    (root / ".venv").mkdir()
    (root / ".venv" / "x.txt").write_bytes(b"venv")
    data = _zip({"sindri.py": b"same", "app/main.py": b"new", "app/novo.py": b"n",
                 ".venv/x.txt": b"HACK", "requirements.txt": b"r"})
    res = updater.apply_zip(data, str(root), "abc123")
    assert (root / "app" / "main.py").read_bytes() == b"new"
    assert (root / "app" / "novo.py").exists()
    assert (root / ".venv" / "x.txt").read_bytes() == b"venv"          # protegido
    assert (root / updater.BACKUP_DIR / "app" / "main.py").read_bytes() == b"old"
    assert sorted(res["changed"]) == ["app/main.py", "app/novo.py", "requirements.txt"]
    assert res["needs_setup"]
    assert updater.local_version(str(root)) == "abc123"


def test_recusa_pacote_estranho(tmp_path):
    with pytest.raises(ValueError):
        updater.apply_zip(_zip({"README.md": b"x"}), str(tmp_path), "x")
    with pytest.raises(ValueError):
        updater.apply_zip(_zip({"sindri.py": b"", "app/main.py": b"", "../fora.txt": b""}), str(tmp_path), "x")
    assert not os.path.exists(tmp_path / "fora.txt")


def test_compara_versoes(tmp_path, monkeypatch):
    remote = updater.RemoteVersion("abc", "2026-10-05T10:00:00Z", "msg")
    monkeypatch.setattr(updater, "latest_version", lambda timeout=6.0: remote)
    (tmp_path / updater.VERSION_FILE).write_text("abc\n")
    assert updater.update_available(root=str(tmp_path)) is None
    (tmp_path / updater.VERSION_FILE).write_text("old\n")
    assert updater.update_available(root=str(tmp_path)) is remote


def test_aviso_na_tela(tmp_path, monkeypatch):
    import time
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, str(tmp_path / "cfg"))
    from app.ui.main_window import MainWindow
    remote = updater.RemoteVersion("abc", "2026-10-05T10:00:00Z", "Melhorias")
    monkeypatch.setattr(updater, "update_available", lambda timeout=6.0, root=None: remote)
    w = MainWindow()
    w.check_updates(silent=True)
    t = time.time()
    while "Nova versão" not in w.banner_text.text() and time.time() - t < 5:
        app.processEvents()
        time.sleep(0.02)
    assert "Nova versão" in w.banner_text.text() and "update:" in w.banner_text.text()
    w.close()
