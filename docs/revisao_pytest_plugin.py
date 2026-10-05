"""Isolamento temporario para diagnosticar os testes originais, sem edita-los."""
import sys
import pytest


@pytest.fixture(autouse=True)
def revisao_preferencias_isoladas(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from app.ui import dialogs
    original = dialogs.settings
    config = QSettings(str(tmp_path / "revisao-preferencias.ini"), QSettings.IniFormat)
    for name, module in list(sys.modules.items()):
        if name.startswith("app.ui") and getattr(module, "settings", None) is original:
            monkeypatch.setattr(module, "settings", lambda: config)
    from app.ui.main_window import MainWindow
    monkeypatch.setattr(MainWindow,"autosave_path",staticmethod(lambda:str(tmp_path / "revisao-auto.sindri")))
    yield
    config.sync()
