"""Preferências do usuário (QSettings) com tipos: liga/desliga, listas e JSON."""
from __future__ import annotations

import json

from .dialogs import settings


def get_bool(key: str, default: bool = False) -> bool:
    v = settings().value(key, "true" if default else "false")
    return v is True or str(v).lower() == "true"


def set_bool(key: str, value: bool) -> None:
    settings().setValue(key, "true" if value else "false")


def get_list_value(v) -> list[str]:
    """QSettings devolve str quando a lista tem 1 item (Windows) — normaliza para lista."""
    if isinstance(v, str):
        return [v] if v else []
    return [x for x in (v or []) if isinstance(x, str)]


def get_list(key: str) -> list[str]:
    return get_list_value(settings().value(key, []))


def get_json(key: str, default=None):
    try:
        return json.loads(settings().value(key, "") or "null") or (default if default is not None else {})
    except (TypeError, ValueError):
        return default if default is not None else {}


def set_json(key: str, value) -> None:
    settings().setValue(key, json.dumps(value))
