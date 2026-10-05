"""Ponto de entrada da aplicação Sindri."""
from __future__ import annotations

import multiprocessing
import os
import sys


def main() -> int:
    multiprocessing.freeze_support()  # necessário no .exe (PyInstaller) para os processos de cálculo
    if __package__ in (None, ""):
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from PySide6.QtWidgets import QApplication
    try:  # o navegador embutido (intranet) precisa ser carregado antes da QApplication
        import PySide6.QtWebEngineWidgets  # noqa: F401
    except Exception:
        pass
    from app.ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Sindri")
    app.setOrganizationName("LabMaker")
    from app.ui.icons import app_icon
    app.setWindowIcon(app_icon())
    if sys.platform == "win32":
        try:  # ícone próprio na barra de tarefas do Windows
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("LabMaker.Sindri")
        except Exception:
            pass
    win = MainWindow()
    win.showMaximized()
    files = [a for a in sys.argv[1:] if a.lower().endswith((".dxf", ".dxfnest", ".sindri"))]
    if files:
        if files[0].lower().endswith((".dxfnest", ".sindri")):
            win.open_project(files[0])
        else:
            win.load_files(files)
    else:
        from PySide6.QtCore import QTimer
        QTimer.singleShot(400, win.check_autosave_on_start)   # oferece recuperar o último trabalho
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
