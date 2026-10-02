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
