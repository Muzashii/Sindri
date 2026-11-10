"""Gravação segura de arquivos no Windows (antivírus e programas abertos às vezes seguram o arquivo)."""
from __future__ import annotations

import os
import time


def replace_file(tmp: str, path: str, tries: int = 5, wait: float = 0.15) -> None:
    """os.replace com novas tentativas; se não der, apaga o temporário e repassa o erro."""
    for i in range(tries):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if i == tries - 1:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise
            time.sleep(wait)


# ---------------------------------------------------------------------------- registro de erros
ERROR_LOG = "sindri_erro.log"
ERROR_LOG_MAX_BYTES = 512 * 1024      # ao passar disso, guarda só a metade mais recente
_REPEAT_LIMIT = 5                     # o mesmo erro no mesmo ponto é registrado no máximo 5 vezes por sessão
_seen: dict[tuple[str, str], int] = {}


def error_log_path() -> str:
    """Mesmo arquivo que o lançador (``sindri.py``) usa: ``%LOCALAPPDATA%\\Sindri\\sindri_erro.log``.

    Com ``SINDRI_DATA_DIR`` definido (testes, instalação portátil) o registro fica nessa pasta."""
    base = os.environ.get("SINDRI_DATA_DIR")
    if not base:
        base = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "Sindri")
    return os.path.join(base, ERROR_LOG)


def log_exception(context: str, exc: BaseException | None = None) -> None:
    """Anota no ``sindri_erro.log`` um erro que o programa decidiu ignorar (sem mudar o que acontece depois).

    Chame de dentro do ``except``: ``context`` diz, em poucas palavras, o que estava sendo feito. Nunca
    levanta exceção, não mostra nada na tela e não repete o mesmo erro sem parar (laços e temporizadores)."""
    try:
        import sys
        import traceback
        from datetime import datetime
        if exc is None:
            exc = sys.exc_info()[1]
        key = (context, type(exc).__name__ if exc is not None else "")
        _seen[key] = _seen.get(key, 0) + 1
        if _seen[key] > _REPEAT_LIMIT:
            return
        if exc is not None:
            detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).rstrip()
        else:
            detail = "(sem exceção ativa)"
        note = " (repetições seguintes deste erro não serão registradas)" if _seen[key] == _REPEAT_LIMIT else ""
        entry = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] ignorado — {context}{note}\n{detail}\n\n"
        path = error_log_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            if os.path.getsize(path) > ERROR_LOG_MAX_BYTES:
                with open(path, "rb") as fh:
                    fh.seek(-ERROR_LOG_MAX_BYTES // 2, os.SEEK_END)
                    tail = fh.read()
                with open(path, "wb") as fh:
                    fh.write(tail)
        except OSError:
            pass                      # arquivo ainda não existe (ou não deu para encurtar): segue anotando
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(entry)
    except Exception:                 # o registro nunca pode derrubar quem chamou
        return
