import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
# os processos filhos (spawn) também precisam achar o pacote "app"
os.environ["PYTHONPATH"] = ROOT + os.pathsep + os.environ.get("PYTHONPATH", "")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
try:  # precisa vir antes de qualquer QApplication
    import PySide6.QtWebEngineWidgets  # noqa: F401
except Exception:
    pass


def fx(name: str) -> str:
    return os.path.join(FIX, name)


@pytest.fixture(autouse=True)
def isolated_preferences(tmp_path, monkeypatch):
    monkeypatch.setenv("SINDRI_SETTINGS_FILE", str(tmp_path / "settings.ini"))
    monkeypatch.setenv("SINDRI_DATA_DIR", str(tmp_path / "data"))
    from PySide6.QtWidgets import QApplication
    from types import SimpleNamespace
    clipboard = SimpleNamespace(value="")
    clipboard.setText = lambda value: setattr(clipboard, "value", value)
    clipboard.text = lambda: clipboard.value
    monkeypatch.setattr(QApplication, "clipboard", staticmethod(lambda: clipboard))
    # o diálogo "cor do arquivo -> operação" é modal: nos testes aceita o padrão
    from app.ui.color_ops_dialog import ColorOpsDialog
    monkeypatch.setattr(ColorOpsDialog, "exec", lambda self: 1)


@pytest.fixture(scope="session", autouse=True)
def qt_lifetime():
    """Destrói páginas antes do perfil e mantém QApplication viva durante toda a suíte."""
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app
    for widget in app.topLevelWidgets():
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    module = sys.modules.get("app.ui.intranet")
    if module is not None and module._PROFILE is not None:
        module._PROFILE.deleteLater()
        module._PROFILE = None
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


@pytest.fixture(scope="session", autouse=True)
def _fixtures():
    if not os.path.isfile(fx("exemplo_lab.dxf")):
        sys.path.insert(0, FIX)
        import make_fixtures
        make_fixtures.main()
    yield


@pytest.fixture(scope="session")
def lab_parts():
    from app.core.part_builder import import_files
    return import_files([fx("exemplo_lab.dxf")]).parts
