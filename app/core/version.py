"""Versão do Sindri mostrada ao técnico (sem Qt).

Ordem de consulta:
1. a tag gravada pelo atualizador no ``versao.txt`` ("sha v1.4.0") — é o que está de fato instalado;
2. o ``pyproject.toml`` da pasta do programa (instalação por pasta ou clone de desenvolvimento);
3. os metadados do pacote instalado (wheel);
4. ``"dev"``.
"""
from __future__ import annotations

import os
from typing import Optional

from . import updater

APP_NAME = "Sindri"
PACKAGE = "sindri"


def _pyproject_version(root: str) -> str:
    try:
        import tomllib
        with open(os.path.join(root, "pyproject.toml"), "rb") as fh:
            project = tomllib.load(fh).get("project", {})
    except (OSError, ValueError, ImportError):
        return ""
    if str(project.get("name", "")).lower() != PACKAGE:
        return ""
    return str(project.get("version", "")).strip()


def _metadata_version() -> str:
    try:
        from importlib.metadata import PackageNotFoundError, version
        try:
            return version(PACKAGE).strip()
        except PackageNotFoundError:
            return ""
    except Exception:
        from .fileutil import log_exception
        log_exception("versão: ler os metadados do pacote instalado")
        return ""


def app_version(root: Optional[str] = None) -> str:
    """'1.4.0' (sem o v) — ou 'dev' quando não dá para saber."""
    root = root or updater.app_root()
    tag = updater.local_tag(root)
    if tag:
        return tag[1:]
    return _pyproject_version(root) or _metadata_version() or "dev"


def short_sha(root: Optional[str] = None) -> str:
    """7 primeiros caracteres do commit instalado, ou vazio se desconhecido."""
    return (updater.local_version(root or updater.app_root()) or "")[:7]


def version_label(root: Optional[str] = None) -> str:
    """'Sindri 1.4.0 (abc1234)' — sem os parênteses quando o commit é desconhecido."""
    sha = short_sha(root)
    return f"{APP_NAME} {app_version(root)}" + (f" ({sha})" if sha else "")
