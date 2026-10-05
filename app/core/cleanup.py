"""Limpeza dos arquivos que o Sindri cria: solicitações baixadas da intranet e arquivos exportados."""
from __future__ import annotations

import fnmatch
import os
from typing import Iterable

REPORT_PATTERNS = ("*_relatorio.pdf",)
CUT_PATTERNS = ("*_todas_placas.dxf",)


def downloaded_files(base: str) -> list[str]:
    """Tudo dentro da pasta de solicitações (Documentos\\Sindri\\Solicitações)."""
    out = []
    if base and os.path.isdir(base):
        for root, _dirs, files in os.walk(base):
            out += [os.path.join(root, f) for f in files]
    return sorted(out)


def exported_files(history: Iterable[str], folders: Iterable[str], patterns: tuple[str, ...]) -> list[str]:
    """Arquivos exportados pelo Sindri: os registrados no histórico + os com o nome padrão nas pastas usadas."""
    found = set()
    for p in history:
        if p and os.path.isfile(p) and any(fnmatch.fnmatch(os.path.basename(p).lower(), pt) for pt in patterns):
            found.add(os.path.abspath(p))
    for d in folders:
        if d and os.path.isdir(d):
            for f in os.listdir(d):
                if any(fnmatch.fnmatch(f.lower(), pt) for pt in patterns) and os.path.isfile(os.path.join(d, f)):
                    found.add(os.path.abspath(os.path.join(d, f)))
    return sorted(found)


def total_size(files: Iterable[str]) -> int:
    n = 0
    for f in files:
        try:
            n += os.path.getsize(f)
        except OSError:
            pass
    return n


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}".replace(".", ",")
        n /= 1024
    return str(n)


def remove_empty_dirs(base: str):
    """Apaga as pastas vazias que sobraram (mantém a pasta base)."""
    if not base or not os.path.isdir(base):
        return
    for root, dirs, files in os.walk(base, topdown=False):
        if root != base and not os.listdir(root):
            try:
                os.rmdir(root)
            except OSError:
                pass
