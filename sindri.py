"""Lançador do Sindri (usado pelo executar.bat e pelo PyInstaller).

Se algo falhar ao abrir, o erro é mostrado numa janela e gravado em sindri_erro.log.
"""
import multiprocessing
import os
import sys
import traceback


def _log_path() -> str:
    base = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
    return os.path.join(base, "sindri_erro.log")


def _report(msg: str) -> None:
    try:
        with open(_log_path(), "w", encoding="utf-8") as f:
            f.write(msg)
    except OSError:
        pass
    print(msg, file=sys.stderr)
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                None, "O Sindri não conseguiu abrir.\n\n" + msg[-1500:] +
                f"\n\nDetalhes salvos em:\n{_log_path()}", "Sindri — erro", 0x10)
        except Exception:
            pass


if __name__ == "__main__":
    multiprocessing.freeze_support()
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        import faulthandler
        if sys.stderr is not None:
            faulthandler.enable()
    except Exception:
        pass
    try:
        from app.main import main
        code = main()
    except ImportError:
        _report(traceback.format_exc() + "\n\nAs bibliotecas do Python estão danificadas ou faltando.\n"
                "Feche esta janela e rode o executar.bat de novo: ele conserta a instalação sozinho.\n"
                "Se continuar, apague a pasta .venv e rode o executar.bat outra vez.")
        sys.exit(1)
    except Exception:
        _report(traceback.format_exc())
        sys.exit(1)
    sys.exit(code)
