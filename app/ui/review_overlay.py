"""Camada de conferência sobre o "Arquivo original": quadro e nome de cada arquivo, aberturas,
pontas soltas e destaque do problema escolhido no painel de Conferência."""
from __future__ import annotations

import os

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsItem, QGraphicsPathItem

from ..core import import_issues as ii
from . import theme

STATUS_COLORS = {ii.ERROR: "danger", ii.CHECK: "warn", ii.FIXED: "success", "ok": "success"}


def frame_offset(frames: dict, path: str) -> tuple[float, float]:
    f = frames.get(os.path.abspath(path)) if frames else None
    return (float(f[0]), float(f[1])) if f else (0.0, 0.0)


def issue_rect(issue: ii.ImportIssue, frames: dict) -> QRectF | None:
    """Retângulo (coords da cena) que enquadra o problema; sem local, o arquivo inteiro."""
    dx, dy = frame_offset(frames, issue.file)
    xs, ys = [], []
    for x0, y0, x1, y1 in issue.where:
        xs += [x0, x1]
        ys += [y0, y1]
    for g in issue.gaps:
        xs += [g.a[0], g.b[0]]
        ys += [g.a[1], g.b[1]]
    if not xs:
        f = frames.get(os.path.abspath(issue.file)) if frames else None
        return QRectF(QPointF(f[2], f[3]), QPointF(f[4], f[5])).normalized() if f else None
    return QRectF(QPointF(min(xs) + dx, min(ys) + dy), QPointF(max(xs) + dx, max(ys) + dy)).normalized()


class FileLabelItem(QGraphicsItem):
    """Nome do arquivo (e da solicitação) acima do desenho, com a cor da situação. Tamanho fixo na tela;
    encolhe (corta o nome com "…") quando o desenho fica estreito na tela, para não invadir o vizinho."""

    def __init__(self, path: str, title: str, sub: str, status: str, canvas, scene_w: float = 0.0):
        super().__init__()
        self.path, self.title, self.sub, self.status, self.canvas = path, title, sub, status, canvas
        self.scene_w = scene_w
        self.setFlag(QGraphicsItem.ItemIgnoresTransformations)
        self.setZValue(20)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(f"{path}" + (f"\n{sub}" if sub else "") + "\nClique para ver os avisos deste arquivo")
        self.f1 = QFont()
        self.f1.setBold(True)
        self.f2 = QFont()
        self.f2.setPointSizeF(max(7.0, self.f2.pointSizeF() - 1))
        self.w1 = QFontMetricsF(self.f1).horizontalAdvance(title)
        self.w2 = QFontMetricsF(self.f2).horizontalAdvance(sub) if sub else 0
        self.full = 22 + self.w1 + (10 + self.w2 if sub else 0) + 10
        self._h = QFontMetricsF(self.f1).height() + 8

    def _avail(self) -> float:
        if not self.scene_w:
            return 1e9
        return abs(self.canvas.transform().m11()) * self.scene_w

    def boundingRect(self) -> QRectF:
        return QRectF(0, -self._h - 4, self.full, self._h)

    def paint(self, painter: QPainter, option, widget=None):
        width = max(26.0, min(self.full, self._avail()))
        r = QRectF(0, -self._h - 4, width, self._h)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(theme.qcolor("border"), 1))
        painter.setBrush(theme.qcolor("surface"))
        painter.drawRoundedRect(r, 6, 6)
        painter.setPen(Qt.NoPen)
        painter.setBrush(theme.qcolor(STATUS_COLORS.get(self.status, "muted")))
        painter.drawEllipse(QPointF(r.left() + 13, r.center().y()), 4.5, 4.5)
        room = width - 30
        if room < 12:
            return
        fm1 = QFontMetricsF(self.f1)
        title = fm1.elidedText(self.title, Qt.ElideMiddle, room)
        painter.setPen(theme.qcolor("text"))
        painter.setFont(self.f1)
        painter.drawText(r.adjusted(22, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, title)
        if self.sub and room > self.w1 + 10 + 30:
            painter.setPen(theme.qcolor("muted"))
            painter.setFont(self.f2)
            sub = QFontMetricsF(self.f2).elidedText(self.sub, Qt.ElideRight, room - self.w1 - 10)
            painter.drawText(r.adjusted(22 + self.w1 + 10, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, sub)

    def mousePressEvent(self, event):
        self.canvas.previewFileClicked.emit(self.path)
        event.accept()


class MarkerItem(QGraphicsItem):
    """Marcador de tamanho fixo na tela: abertura (anel âmbar + medida) ou ponta solta (anel vermelho)."""

    def __init__(self, kind: str, text: str = "", tip: str = ""):
        super().__init__()
        self.kind, self.text = kind, text
        self.setFlag(QGraphicsItem.ItemIgnoresTransformations)
        self.setZValue(30)
        if tip:
            self.setToolTip(tip)
        self.font = QFont()
        self.font.setBold(True)
        self._tw = QFontMetricsF(self.font).horizontalAdvance(text) + 10 if text else 0

    def boundingRect(self) -> QRectF:
        return QRectF(-11, -11, 22 + (self._tw + 4 if self.text else 0), 22)

    def paint(self, painter: QPainter, option, widget=None):
        painter.setRenderHint(QPainter.Antialiasing)
        col = theme.qcolor("warn" if self.kind == "gap" else "danger")
        painter.setPen(QPen(col, 2.5))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(QPointF(0, 0), 8, 8)
        if self.text:
            r = QRectF(12, -9, self._tw, 18)
            painter.setPen(Qt.NoPen)
            painter.setBrush(col)
            painter.drawRoundedRect(r, 4, 4)
            painter.setPen(QColor("#111111") if getattr(theme, "_dark", False) else QColor("#ffffff"))
            painter.setFont(self.font)
            painter.drawText(r, Qt.AlignCenter, self.text)


def _dashed_rect_item(rect: QRectF, color: QColor, width: float = 2.0) -> QGraphicsPathItem:
    path = QPainterPath()
    path.addRect(rect)
    item = QGraphicsPathItem(path)
    pen = QPen(color, width, Qt.DashLine)
    pen.setCosmetic(True)
    item.setPen(pen)
    item.setZValue(15)
    return item


def add_frames(canvas, frames: dict, labels: dict):
    """Quadro discreto em volta de cada arquivo + rótulo com nome, solicitação e situação."""
    scene = canvas.scene()
    for path, f in (frames or {}).items():
        rect = QRectF(QPointF(f[2], f[3]), QPointF(f[4], f[5])).normalized()
        pad = max(4.0, 0.03 * max(rect.width(), rect.height()))
        rr = rect.adjusted(-pad, -pad, pad, pad)
        title, sub, status = labels.get(path, (os.path.basename(path), "", "ok"))
        p = QPainterPath()
        p.addRoundedRect(rr, pad, pad)
        box = QGraphicsPathItem(p)
        col = theme.qcolor(STATUS_COLORS[status]) if status in (ii.ERROR, ii.CHECK) else theme.qcolor("sheet_border")
        pen = QPen(col, 1.5 if status in (ii.ERROR, ii.CHECK) else 1.0)
        pen.setCosmetic(True)
        box.setPen(pen)
        fill = theme.qcolor("sheet")
        fill.setAlpha(150)
        box.setBrush(QBrush(fill))
        box.setZValue(-10)
        scene.addItem(box)
        lab = FileLabelItem(path, title, sub, status, canvas, rr.width() + pad)
        lab.setPos(rr.left(), rr.bottom())        # Y para cima: "bottom" do QRectF é o topo do desenho
        scene.addItem(lab)


def add_issue_marks(canvas, issues: list, frames: dict, acked: set):
    """Aberturas e pontas soltas dos contornos abertos; contornos inválidos e marcas soltas em tracejado."""
    scene = canvas.scene()
    for it in issues:
        if it.severity == ii.FIXED or it.key in acked:
            continue
        dx, dy = frame_offset(frames, it.file)
        if it.kind == "open":
            for g in it.gaps:
                m = MarkerItem("gap", ii.fmt_mm(g.dist), f"Abertura de {ii.fmt_mm(g.dist)} entre duas pontas: "
                               "dá para fechar no painel de Conferência.")
                m.setPos((g.a[0] + g.b[0]) / 2 + dx, (g.a[1] + g.b[1]) / 2 + dy)
                scene.addItem(m)
            for x, y in it.loose_ends:
                m = MarkerItem("end", "", "Ponta solta: nenhuma outra ponta a menos de "
                               f"{ii.fmt_mm(ii.GAP_SEARCH_MM)}. Pode faltar um trecho ou ser uma linha proposital.")
                m.setPos(x + dx, y + dy)
                scene.addItem(m)
        elif it.where:
            for x0, y0, x1, y1 in it.where:
                item = _dashed_rect_item(QRectF(QPointF(x0 + dx, y0 + dy), QPointF(x1 + dx, y1 + dy)).normalized(),
                                         theme.qcolor("warn"))
                item.setToolTip(it.message)
                scene.addItem(item)


def highlight(canvas, issue: ii.ImportIssue, frames: dict):
    """Realça (em azul) os locais de um aviso, inclusive os já resolvidos (linhas repetidas etc.)."""
    clear_highlight(canvas)
    dx, dy = frame_offset(frames, issue.file)
    items = []
    for x0, y0, x1, y1 in issue.where:
        items.append(_dashed_rect_item(QRectF(QPointF(x0 + dx, y0 + dy), QPointF(x1 + dx, y1 + dy)).normalized(),
                                       theme.qcolor("accent"), 2.5))
    for item in items:
        item.setZValue(25)
        canvas.scene().addItem(item)
    canvas._review_hl = items


def clear_highlight(canvas):
    for item in getattr(canvas, "_review_hl", []) or []:
        if item.scene() is canvas.scene():
            canvas.scene().removeItem(item)
    canvas._review_hl = []
