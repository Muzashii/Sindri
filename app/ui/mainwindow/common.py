"""Constantes e pequenas funções usadas pelas partes da janela principal."""
from __future__ import annotations

APP_NAME = "Sindri"
MAX_UNDO = 100


def fmt_pct(v: float) -> str:
    return f"{100 * v:.1f}".replace(".", ",") + "%"


def now_txt() -> str:
    import datetime as _dt
    return f"{_dt.datetime.now():%d/%m/%Y %H:%M}"
