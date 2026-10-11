"""Conversão das peças em QPainterPath (canvas, miniaturas e relatório)."""
from __future__ import annotations


import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap, QTransform, QBrush

from ..core.geometry import flatten_prim, prim_rgb
from ..core.models import Part, Prim

PREVIEW_TOL = 0.05


def _poly_path(pts: np.ndarray, closed: bool) -> QPainterPath:
    path = QPainterPath()
    if len(pts) == 0:
        return path
    path.moveTo(float(pts[0][0]), float(pts[0][1]))
    for x, y in pts[1:]:
        path.lineTo(float(x), float(y))
    if closed:
        path.closeSubpath()
    return path


def text_path(p: Prim) -> QPainterPath:
    d = p.data
    txt = d.get("text", "")
    if p.kind == "MTEXT":
        try:
            from ezdxf.tools.text import plain_mtext
            txt = plain_mtext(txt)
        except Exception:
            pass
    lines = str(txt).split("\n")
    font = QFont("Arial")
    font.setPixelSize(100)
    path = QPainterPath()
    for i, ln in enumerate(lines):
        path.addText(0, i * 160, font, ln)
    bb = np.asarray(d["bbox"])
    br = path.boundingRect()
    if br.isEmpty():
        return QPainterPath()
    # encaixa o texto na caixa calculada na importação (canto inferior-esquerdo = bbox[0])
    w = float(np.hypot(*(bb[1] - bb[0])))
    h = float(np.hypot(*(bb[3] - bb[0])))
    sx = w / max(br.width(), 1e-6)
    sy = h / max(br.height(), 1e-6)
    s = min(sx, sy)
    ang = float(np.degrees(np.arctan2(bb[1][1] - bb[0][1], bb[1][0] - bb[0][0])))
    t = QTransform()
    t.translate(float(bb[0][0]), float(bb[0][1]))
    t.rotate(ang)
    t.scale(s, -s)            # glifos do Qt têm Y para baixo
    t.translate(-br.left(), -br.bottom())
    return t.map(path)


def prim_path(p: Prim, tol: float = PREVIEW_TOL) -> QPainterPath:
    if p.kind in ("TEXT", "MTEXT"):
        return text_path(p)
    pts = flatten_prim(p, tol)
    return _poly_path(pts, False)


class PartGraphics:
    """Caminhos de desenho de uma peça em coordenadas locais (espelhamento já aplicado)."""

    def __init__(self, part: Part, mirrored: bool = False):
        self.lines: list[tuple[QColor, QPainterPath]] = []
        groups: dict[tuple, QPainterPath] = {}
        for pr in part.prims:
            col = prim_rgb(pr)
            path = groups.setdefault(col, QPainterPath())
            path.addPath(prim_path(pr))
        m = QTransform()
        if mirrored:
            m.scale(-1, 1)
        for col, path in groups.items():
            self.lines.append((QColor(*col), m.map(path)))
        fill = _poly_path(np.asarray(part.outer.exterior.coords), True)
        for h in part.holes:
            fill.addPath(_poly_path(np.asarray(h.exterior.coords), True))
        fill.setFillRule(Qt.OddEvenFill)
        self.fill = m.map(fill)
        c = part.display_color
        self.color = QColor(*c) if c != (0, 0, 0) else QColor(90, 110, 140)
        self.rect = self.fill.boundingRect()
        for _, p in self.lines:
            self.rect = self.rect.united(p.boundingRect())


_graphics_cache: dict[tuple, PartGraphics] = {}


def part_graphics(part: Part, mirrored: bool = False) -> PartGraphics:
    key = (id(part), part.id, mirrored)
    g = _graphics_cache.get(key)
    if g is None:
        g = PartGraphics(part, mirrored)
        _graphics_cache[key] = g
    return g


def clear_graphics_cache():
    _graphics_cache.clear()


def thumbnail(part: Part, size: int = 56, dark: bool = False, height: int | None = None,
              mdf: bool = False) -> QPixmap:
    """Miniatura da peça. ``mdf``: preenchida como a chapa de MDF, com o contorno de corte em marrom
    (as outras cores do desenho, como gravação, continuam com a cor original)."""
    from . import theme
    w, h = size, height or size
    scale = 2                                    # nítida em telas com zoom do Windows
    pm = QPixmap(w * scale, h * scale)
    pm.fill(Qt.transparent)
    pm.setDevicePixelRatio(scale)
    g = part_graphics(part)
    r = g.rect
    if r.isEmpty():
        return pm
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing)
    pad = 4
    s = min((w - 2 * pad) / max(r.width(), 1e-6), (h - 2 * pad) / max(r.height(), 1e-6))
    painter.translate(w / 2, h / 2)
    painter.scale(s, -s)
    painter.translate(-r.center().x(), -r.center().y())
    t = theme.tokens()
    if mdf:
        painter.fillPath(g.fill, QBrush(QColor(t.get("mdf", "#d9bc92"))))
    else:
        fillc = QColor(g.color)
        fillc.setAlpha(60)
        painter.fillPath(g.fill, QBrush(fillc))
    outer = QColor(g.color)
    for col, path in g.lines:
        c = (QColor(t.get("mdf_line", "#7c2d12")) if mdf and (col == outer or col.lightness() < 60)
             else theme.line_color(col, dark))
        pen = QPen(c, 1.2 if mdf else 0)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.drawPath(path)
    if part.warnings:
        pen = QPen(QColor(220, 40, 40), 2)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.resetTransform()
        painter.drawRect(1, 1, w - 2, h - 2)
    painter.end()
    return pm
