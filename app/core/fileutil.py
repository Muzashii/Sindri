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
