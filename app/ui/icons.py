"""Ícones vetoriais (traço estilo 'lucide') desenhados na cor do tema."""
from __future__ import annotations

import os
import tempfile

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

PATHS = {
    "open": '<path d="M6 14l1.5-2.9A2 2 0 0 1 9.24 10H20a2 2 0 0 1 1.94 2.5l-1.54 6a2 2 0 0 1-1.95 1.5H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H18a2 2 0 0 1 2 2v2"/>',
    "save": '<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/>',
    "play": '<polygon points="6 3 20 12 6 21 6 3" fill="currentColor"/>',
    "pause": '<rect x="6" y="4" width="4" height="16" rx="1" fill="currentColor"/><rect x="14" y="4" width="4" height="16" rx="1" fill="currentColor"/>',
    "stop": '<rect x="5" y="5" width="14" height="14" rx="2" fill="currentColor"/>',
    "export": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/>',
    "moon": '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>',
    "fit": '<path d="M8 3H5a2 2 0 0 0-2 2v3M21 8V5a2 2 0 0 0-2-2h-3M3 16v3a2 2 0 0 0 2 2h3M16 21h3a2 2 0 0 0 2-2v-3"/>',
    "rotate": '<path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><polyline points="21 3 21 8 16 8"/>',
    "lock": '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
    "unlock": '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 7.9-1"/>',
    "left": '<polyline points="15 18 9 12 15 6"/>',
    "right": '<polyline points="9 18 15 12 9 6"/>',
    "plus": '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
    "minus": '<line x1="5" y1="12" x2="19" y2="12"/>',
    "trash": '<polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6M10 11v6M14 11v6M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>',
    "copy": '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
    "reset": '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><polyline points="3 3 3 8 8 8"/>',
    "warn": '<path d="M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
    "info": '<circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/>',
    "close": '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
    "check": '<polyline points="20 6 9 17 4 12"/>',
    "layers": '<polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/>',
    "grid": '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/>',
}


def svg(name: str, color: str, stroke: float = 2.0) -> str:
    body = PATHS[name].replace("currentColor", color)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
            f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')


def pixmap(name: str, color: str, size: int = 18, stroke: float = 2.0) -> QPixmap:
    scale = 2
    pm = QPixmap(size * scale, size * scale)
    pm.fill(Qt.transparent)
    r = QSvgRenderer(QByteArray(svg(name, color, stroke).encode()))
    p = QPainter(pm)
    r.render(p, QRectF(0, 0, size * scale, size * scale))
    p.end()
    pm.setDevicePixelRatio(scale)
    return pm


def icon(name: str, color: str, size: int = 18, disabled_color: str | None = None) -> QIcon:
    ic = QIcon()
    ic.addPixmap(pixmap(name, color, size), QIcon.Normal)
    ic.addPixmap(pixmap(name, disabled_color or "#9aa1ad", size), QIcon.Disabled)
    return ic


_files: dict[str, str] = {}


def image_file(name: str, color: str, size: int = 14, stroke: float = 3.0) -> str:
    """Grava o ícone em PNG temporário (para usar em url() no QSS) e devolve o caminho com '/'."""
    key = f"{name}_{color}_{size}"
    if key not in _files:
        d = os.path.join(tempfile.gettempdir(), "dxfnest_icons")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"{key.replace('#', '')}.png")
        pm = QPixmap(size * 2, size * 2)
        pm.fill(Qt.transparent)
        r = QSvgRenderer(QByteArray(svg(name, color, stroke).encode()))
        p = QPainter(pm)
        r.render(p, QRectF(2, 2, size * 2 - 4, size * 2 - 4))
        p.end()
        pm.save(path)
        _files[key] = path.replace("\\", "/")
    return _files[key]


def app_icon() -> QIcon:
    """Ícone do Sindri: martelo de ferreiro sobre peças encaixadas, em quadrado azul arredondado."""
    from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPen
    ic = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        u = size / 32.0
        g = QLinearGradient(0, 0, size, size)
        g.setColorAt(0, QColor("#1e3a8a"))
        g.setColorAt(1, QColor("#2563eb"))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(g))
        p.drawRoundedRect(QRectF(0, 0, size, size), 7 * u, 7 * u)
        # peças encaixadas (base)
        p.setBrush(QColor("#93c5fd"))
        p.drawRoundedRect(QRectF(5 * u, 21 * u, 9 * u, 6 * u), 1.2 * u, 1.2 * u)
        p.drawRoundedRect(QRectF(15 * u, 21 * u, 12 * u, 6 * u), 1.2 * u, 1.2 * u)
        # martelo
        p.save()
        p.translate(17 * u, 13 * u)
        p.rotate(-35)
        p.setBrush(QColor("#ffffff"))
        p.drawRoundedRect(QRectF(-1.6 * u, -1 * u, 3.2 * u, 15 * u), 1.4 * u, 1.4 * u)   # cabo
        p.drawRoundedRect(QRectF(-7 * u, -5.5 * u, 14 * u, 6 * u), 1.6 * u, 1.6 * u)    # cabeça
        p.setBrush(QColor("#fbbf24"))
        p.drawRoundedRect(QRectF(-7 * u, -5.5 * u, 3 * u, 6 * u), 1.2 * u, 1.2 * u)     # face
        p.restore()
        p.end()
        ic.addPixmap(pm)
    return ic
