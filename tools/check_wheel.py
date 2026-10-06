"""Instala o wheel num diretório temporário e abre a janela fora da árvore fonte."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    wheel = sorted(Path(sys.argv[1]).resolve().glob("*.whl"))[-1]
    with tempfile.TemporaryDirectory(prefix="sindri-wheel-") as tmp:
        target = Path(tmp) / "installed"
        subprocess.run([sys.executable, "-m", "pip", "install", "--no-deps", "--target", str(target), str(wheel)], check=True)
        env = dict(os.environ, PYTHONPATH=str(target), QT_QPA_PLATFORM="offscreen",
                   SINDRI_SETTINGS_FILE=str(Path(tmp) / "settings.ini"), SINDRI_DATA_DIR=str(Path(tmp) / "data"))
        code = """
from pathlib import Path
import PySide6.QtWebEngineWidgets
from PySide6.QtWidgets import QApplication
import app
assert 'installed' in Path(app.__file__).parts, app.__file__
from app.ui.main_window import MainWindow
from app.ui.mainwindow.projects import ProjectMixin
from app.cli import main
from app.core.updater import can_self_update
assert not can_self_update(), 'Wheel não deve atualizar a pasta site-packages por ZIP'
qapp = QApplication([])
window = MainWindow(workers=0)
window.close()
print('Wheel instalado: importação e janela principal OK; ' + app.__file__)
"""
        subprocess.run([sys.executable, "-c", code], cwd=tmp, env=env, check=True, timeout=60)


if __name__ == "__main__":
    main()
