"""Canvas (QGraphicsView): placas, peças, zoom, pan, arrastar e girar."""
from __future__ import annotations


from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QPainter, QPainterPath, QPen)
from PySide6.QtWidgets import (QGraphicsItem, QGraphicsPathItem, QGraphicsScene,
                               QGraphicsSimpleTextItem, QGraphicsView, QApplication)

from ..core.models import NestParams, Part, Placement, Prim
from ..core.geometry import prim_rgb
from .render import part_graphics, prim_path
from .owners import part_label
from . import theme

RED = QColor(220, 40, 40)


def sheet_offset(params: NestParams, index: int) -> float:
    """Posição X da placa numa fileira única (compatibilidade; o canvas usa ``sheet_origin``)."""
    return grid_origin(params, index, 0)[0]


def grid_gaps(params: NestParams) -> tuple[float, float]:
    """Folga entre placas na grade: horizontal e vertical (a vertical cabe a etiqueta da placa de baixo)."""
    gx = max(30.0, 0.08 * params.sheet_width)
    return gx, max(gx, 0.25 * params.sheet_height)


def grid_origin(params: NestParams, index: int, cols: int) -> tuple[float, float]:
    """Canto inferior esquerdo da vaga ``index`` numa grade de ``cols`` colunas (0 = uma fileira só).
    As linhas descem (Y para cima, como no DXF)."""
    gx, gy = grid_gaps(params)
    col, row = (index, 0) if cols <= 0 else (index % cols, index // cols)
    return col * (params.sheet_width + gx), -row * (params.sheet_height + gy)


def grid_index(params: NestParams, x: float, y: float, cols: int) -> int:
    """Vaga da grade sob o ponto (x, y) da cena — o inverso de ``grid_origin`` (meio da folga = divisa)."""
    gx, gy = grid_gaps(params)
    col = max(0, int((x + gx / 2) // (params.sheet_width + gx)))
    if cols <= 0:
        return col
    col = min(col, cols - 1)
    row = max(0, int((params.sheet_height - y + gy / 2) // (params.sheet_height + gy)))
    return row * cols + col


def best_columns(n: int, sheet_w: float, sheet_h: float, gaps: tuple[float, float], view_w: float,
                 view_h: float) -> int:
    """Quantas colunas mostram ``n`` placas maiores na área visível (várias linhas se a área for alta)."""
    if n <= 1 or view_w < 50 or view_h < 50:
        return max(1, n)
    gx, gy = gaps
    best, best_scale = n, -1.0
    for c in range(n, 0, -1):                      # empate: mais colunas (fileira, como antes)
        rows = -(-n // c)
        tw, th = c * sheet_w + (c - 1) * gx, rows * sheet_h + (rows - 1) * gy + 0.06 * sheet_h
        scale = min(view_w / tw, view_h / th)
        if scale > best_scale * 1.02:
            best, best_scale = c, scale
    return best


NUDGE_KEYS = {Qt.Key_Left: (-1.0, 0.0), Qt.Key_Right: (1.0, 0.0),
              Qt.Key_Up: (0.0, 1.0), Qt.Key_Down: (0.0, -1.0)}     # Y para cima, como no DXF


def nudge_step(key, modifiers):
    """((dx, dy), encostar?) para uma tecla de seta com modificadores, ou None se não for seta.
    Seta = 1 mm · Shift = 10 mm · Alt = 0,1 mm · Ctrl = encostar (desliza até a vizinha ou a margem)."""
    d = NUDGE_KEYS.get(key)
    if d is None:
        return None
    mods = modifiers
    if mods & Qt.ControlModifier:
        return d, True
    size = 10.0 if mods & Qt.ShiftModifier else 0.1 if mods & Qt.AltModifier else 1.0
    return (d[0] * size, d[1] * size), False


LOW_USE = 0.15          # última placa de um material com menos que isso de uso: "pouco usada"


def usage_split(used: dict, areas: dict, materials: dict) -> dict:
    """Aproveitamento separado em placas cheias × última placa de cada material.

    ``used``: {placa: mm² de peças} (só placas com peças); ``areas``: {placa: mm² da placa};
    ``materials``: {placa: material}. Devolve {"total", "full" (None se não há placa cheia), "last",
    "lasts" (placas últimas), "low" (últimas com < 15% num material com ≥ 2 placas)}."""
    sheets = sorted(s for s in used if areas.get(s))
    by_mat: dict = {}
    for s in sheets:
        by_mat.setdefault(materials.get(s, "") or "", []).append(s)
    lasts = {max(v) for v in by_mat.values()}
    full = [s for s in sheets if s not in lasts]

    def ratio(ss):
        a = sum(areas[s] for s in ss)
        return sum(used[s] for s in ss) / a if a else 0.0
    low = {max(v) for v in by_mat.values()
           if len(v) >= 2 and used[max(v)] / areas[max(v)] < LOW_USE}
    return {"total": ratio(sheets), "full": ratio(full) if full else None,
            "last": ratio(sorted(lasts)), "lasts": lasts, "low": low}


def _spec_of(params: NestParams):
    from ..core.sheetspec import SheetSpec
    return SheetSpec(params.sheet_width, params.sheet_height, params.margin, params.spacing)


def _ring_path(pts, close=True) -> QPainterPath:
    path = QPainterPath()
    if pts:
        path.moveTo(QPointF(*pts[0]))
        for x, y in pts[1:]:
            path.lineTo(QPointF(x, y))
        if close:
            path.closeSubpath()
    return path


class SheetItem(QGraphicsItem):
    """Uma placa: chapa inteira do tamanho do material ou retalho (contorno próprio)."""

    def __init__(self, params: NestParams, index: int, dark: bool, material: str = "", done: bool = False,
                 spec=None, slot: NestParams | None = None):
        super().__init__()
        self.params = params
        self.spec = spec or _spec_of(params)
        self.index = index
        self.dark = dark
        self.material = material
        self.done = done
        self.setZValue(-10)
        self.setPos(*grid_origin(slot or params, index, 0))
        if self.spec.is_remnant:
            self.setToolTip(f"Retalho {self.spec.name or self.spec.remnant_id} · "
                            f"{self.spec.width:.0f} × {self.spec.height:.0f} mm")

    def boundingRect(self) -> QRectF:
        return QRectF(-1, -1, self.spec.width + 2, self.spec.height + 2)

    def _shape_path(self) -> QPainterPath:
        path = _ring_path(self.spec.outline)
        for hole in self.spec.holes:
            path.addPath(_ring_path(hole))
        path.setFillRule(Qt.OddEvenFill)
        return path

    def paint(self, painter: QPainter, option, widget=None):
        w, h = self.spec.width, self.spec.height
        lod = option.levelOfDetailFromTransform(painter.worldTransform())
        # sombra
        sh = 6.0 / max(lod, 1e-3)
        if self.spec.is_remnant:
            shape = self._shape_path()
            painter.fillPath(shape.translated(sh * 0.4, -sh), theme.qcolor("shadow"))
            painter.fillPath(shape, theme.qcolor("sheet"))
            painter.save()
            painter.setClipPath(shape)
        else:
            painter.fillRect(QRectF(sh * 0.4, -sh, w, h), theme.qcolor("shadow"))
            painter.fillRect(QRectF(0, 0, w, h), theme.qcolor("sheet"))
        step = 10 if lod > 1.0 else 50
        pen = QPen(theme.qcolor("grid"), 0)
        pen2 = QPen(theme.qcolor("grid2"), 0)
        for k in range(1, int(w // step) + 1):
            x = k * step
            if x >= w:
                break
            painter.setPen(pen2 if x % 100 == 0 else pen)
            painter.drawLine(QPointF(x, 0), QPointF(x, h))
        for k in range(1, int(h // step) + 1):
            y = k * step
            if y >= h:
                break
            painter.setPen(pen2 if y % 100 == 0 else pen)
            painter.drawLine(QPointF(0, y), QPointF(w, y))
        if self.spec.is_remnant:
            painter.restore()
        m = self.spec.margin
        mc = theme.qcolor("warn")
        mc.setAlpha(150)
        if m > 0 and not self.spec.is_remnant:
            painter.setPen(QPen(mc, 0, Qt.DashLine))
            painter.drawRect(QRectF(m, m, w - 2 * m, h - 2 * m))
        elif self.spec.is_remnant:
            usable = self.spec.usable()
            painter.setPen(QPen(mc, 0, Qt.DashLine))
            for g in getattr(usable, "geoms", [usable]):
                if g.is_empty:
                    continue
                painter.drawPath(_ring_path(list(g.exterior.coords)))
                for ring in g.interiors:
                    painter.drawPath(_ring_path(list(ring.coords)))
        if self.material:
            pen = QPen(theme.material_color(self.material), 3)
            pen.setCosmetic(True)
            painter.setPen(pen)
        else:
            painter.setPen(QPen(theme.qcolor("sheet_border"), 0))
        if self.spec.is_remnant:
            pen = painter.pen()
            pen.setStyle(Qt.DashDotLine)
            painter.setPen(pen)
            painter.drawPath(self._shape_path())
        else:
            painter.drawRect(QRectF(0, 0, w, h))
        if self.done:
            done_path = self._shape_path() if self.spec.is_remnant else None
            if done_path is not None:
                painter.fillPath(done_path, QColor(22, 163, 74, 38))
            else:
                painter.fillRect(QRectF(0, 0, w, h), QColor(22, 163, 74, 38))
            pen = QPen(theme.qcolor("success_text"), 4)
            pen.setCosmetic(True)
            painter.setPen(pen)
            if done_path is not None:
                painter.drawPath(done_path)
            else:
                painter.drawRect(QRectF(0, 0, w, h))


class SheetLabel(QGraphicsItem):
    """Etiqueta "Placa N · x peças · y%" acima da placa (tamanho fixo na tela), com o botão
    "cortada" clicável. Encolhe quando a placa fica pequena na tela (não invade a vizinha)."""

    def __init__(self, title: str, sub: str, material: str = "", done: bool = False, sheet_w: float = 0.0,
                 low: bool = False):
        super().__init__()
        self.title, self.sub, self.material, self.done = title, sub, material, done
        self.low = low                     # última placa do material quase vazia: selo "pouco usada"
        self.sheet_w = sheet_w
        self.index = 0
        self.setFlag(QGraphicsItem.ItemIgnoresTransformations)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setZValue(5)
        self.f1 = QFont()
        self.f1.setBold(True)
        self.f1.setPointSize(9)
        self.f2 = QFont()
        self.f2.setPointSize(8)
        from PySide6.QtGui import QFontMetrics
        self.w1 = QFontMetrics(self.f1).horizontalAdvance(title)
        self.w2 = QFontMetrics(self.f2).horizontalAdvance(sub)
        self.w0 = (QFontMetrics(self.f2).horizontalAdvance(material) + 18) if material else 0
        self.wd = QFontMetrics(self.f2).horizontalAdvance("✓ cortada") + 18
        self.full = self.w0 + self.w1 + self.w2 + 34 + self.wd + 6
        self.setToolTip("Clique em “cortada” para marcar/desmarcar esta placa (atalho: C)" +
                        ("\nPouco usada: dá para encaixar de novo com mais tempo ou guardar a sobra como retalho."
                         if low else ""))
        self.set_done(done)

    def set_done(self, done: bool):
        self.prepareGeometryChange()
        self.done = done
        self.width = self.full
        self.update()

    def _avail(self) -> float:
        vs = self.scene().views() if self.scene() else []
        if not vs or not self.sheet_w:
            return 1e9
        return abs(vs[0].transform().m11()) * self.sheet_w

    def boundingRect(self) -> QRectF:
        return QRectF(0, -32, self.width, 26)

    def paint(self, painter: QPainter, option, widget=None):
        painter.setRenderHint(QPainter.Antialiasing)
        avail = self._avail()
        compact = avail < self.full
        width = min(self.full, max(60.0, avail - 4)) if compact else self.full
        r = QRectF(0, -32, width, 24)
        painter.setPen(QPen(theme.qcolor("success_text") if self.done else theme.qcolor("border"), 1))
        painter.setBrush(theme.qcolor("surface"))
        painter.drawRoundedRect(r, 12, 12)
        x = 12
        show_mat = self.material and (not compact or width > self.w0 + self.w1 + 50)
        if show_mat:
            chip = QRectF(4, -29, self.w0, 18)
            painter.setPen(Qt.NoPen)
            painter.setBrush(theme.material_color(self.material))
            painter.drawRoundedRect(chip, 9, 9)
            painter.setPen(QColor("white"))
            painter.setFont(self.f2)
            painter.drawText(chip, Qt.AlignCenter, self.material)
            x = self.w0 + 10
        painter.setPen(theme.qcolor("text"))
        painter.setFont(self.f1)
        painter.drawText(QRectF(x, -32, self.w1 + 2, 24), Qt.AlignVCenter | Qt.AlignLeft, self.title)
        if not compact:
            painter.setPen(theme.qcolor("warn" if self.low else "muted"))
            painter.setFont(self.f2)
            painter.drawText(QRectF(x + self.w1 + 10, -32, self.w2 + 4, 24), Qt.AlignVCenter | Qt.AlignLeft,
                             self.sub)
        chip = QRectF(width - (22 if compact else self.wd) - 4, -29, 22 if compact else self.wd, 18)
        painter.setFont(self.f2)
        if self.done:
            painter.setPen(Qt.NoPen)
            painter.setBrush(theme.qcolor("success_text"))
            painter.drawRoundedRect(chip, 9, 9)
            painter.setPen(QColor("white"))
        else:
            painter.setPen(QPen(theme.qcolor("muted"), 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(chip, 9, 9)
            painter.setPen(theme.qcolor("muted"))
        painter.drawText(chip, Qt.AlignCenter, ("✓" if self.done else "○") if compact
                         else ("✓ cortada" if self.done else "○ cortada"))

    def mousePressEvent(self, event):
        vs = self.scene().views() if self.scene() else []
        if vs and hasattr(vs[0], "sheetCutClicked"):
            vs[0].sheetCutClicked.emit(self.index)
        event.accept()


class PartItem(QGraphicsItem):
    def __init__(self, part: Part, placement: Placement, canvas: NestCanvas):
        super().__init__()
        self.part = part
        self.placement = placement
        self.canvas = canvas
        self.colliding = False
        self.gfx = part_graphics(part, placement.mirrored)
        self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable |
                      QGraphicsItem.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self.sync_from_placement()
        tip = f"{part.name}  (#{placement.instance + 1})\nGire com R · trave com L · botão direito: mais opções"
        self.setToolTip(tip)
        self.label_txt = part_label(part, len(canvas.owner_colors) > 1)
        self.label_pt = self._label_point()

    def _label_point(self) -> QPointF:
        cache = self.part.__dict__.setdefault("_label_pts", {})
        key = bool(self.placement.mirrored)
        if key in cache:
            return cache[key]
        from shapely import affinity
        from shapely.ops import polylabel
        g = self.part.outer
        if self.placement.mirrored:
            g = affinity.scale(g, -1, 1, origin=(0, 0))
        try:
            pt = polylabel(g, tolerance=max(0.5, (g.bounds[2] - g.bounds[0]) / 50))
        except Exception:
            pt = g.representative_point()
        cache[key] = QPointF(pt.x, pt.y)
        return cache[key]

    def sync_from_placement(self):
        pl = self.placement
        self._syncing = True
        ox, oy = self.canvas.sheet_origin(pl.sheet_index)
        self.setPos(ox + pl.x, oy + pl.y)
        self.setRotation(pl.rotation)
        self._syncing = False

    def boundingRect(self) -> QRectF:
        return self.gfx.rect.adjusted(-1, -1, 1, 1)

    def shape(self) -> QPainterPath:
        return self.gfx.fill

    def paint(self, painter: QPainter, option, widget=None):
        g = self.gfx
        dark = self.canvas.dark
        hidden = bool(self.canvas.filter_tag) and self.part.tag != self.canvas.filter_tag
        if hidden:                       # filtro por solicitação: as outras peças quase somem
            painter.setOpacity(0.12)
        if self.colliding:
            fill = theme.qcolor("danger")
            fill.setAlpha(120)
        elif self._is_done():
            fill = QColor(148, 163, 184, 150)            # peça feita: cinza
        else:
            own = self.canvas.owner_colors.get(self.part.tag)
            if own is not None:
                fill = QColor(own)
                fill.setAlpha(150 if dark else 105)
            else:
                fill = theme.qcolor("part_fill")
        painter.fillPath(g.fill, QBrush(fill))
        warn = self.canvas.issue_path(self.part.id, self.placement.mirrored)
        if warn is not None:                 # trechos finos demais (fabricabilidade): laranja
            painter.fillPath(warn, QBrush(QColor(249, 115, 22, 190)))
        for col, path in g.lines:
            pen = QPen(theme.line_color(col, dark), 0)
            painter.setPen(pen)
            painter.drawPath(path)
        if self.isSelected():
            pen = QPen(theme.qcolor("accent"), 2.5)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(g.fill)
        if self.placement.locked:
            pen = QPen(theme.qcolor("warn"), 2, Qt.DashLine)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(g.fill.boundingRect())
        if self.colliding:
            pen = QPen(theme.qcolor("danger"), 2, Qt.DashLine)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(g.fill)
        if self.canvas.show_labels and self.label_txt and not hidden:
            self._paint_label(painter)

    def _is_done(self) -> bool:
        """Feita: o tipo de peça foi marcado como feito ou esta cópia está numa placa já cortada."""
        return self.part.id in self.canvas.done_parts or self.placement.sheet_index in self.canvas.cut_sheets

    def _paint_label(self, painter: QPainter):
        """Nº da solicitação (ou da peça) em cima da peça, sempre legível (texto sem espelhar)."""
        t = painter.worldTransform()
        br = t.mapRect(self.gfx.rect)
        done = self._is_done()
        txt = ("✓ " + self.label_txt) if done else self.label_txt
        emphasized = self.isSelected() or self.isUnderMouse()
        size = min(br.height() * 0.28, (br.width() - 8) / max(1.0, 0.66 * len(txt)),
                   16.0 if emphasized else 12.0)
        if size < (8 if emphasized else 10):
            return
        c = t.map(self.label_pt)
        painter.save()
        painter.resetTransform()
        f = QFont()
        f.setBold(True)
        f.setPixelSize(int(size))
        painter.setFont(f)
        from PySide6.QtGui import QFontMetricsF
        fm = QFontMetricsF(f)
        w = fm.horizontalAdvance(txt) + 8
        r = QRectF(c.x() - w / 2, c.y() - fm.height() / 2, w, fm.height())
        painter.setPen(Qt.NoPen)
        background = theme.qcolor("surface")
        background.setAlpha(240 if emphasized else 220)
        painter.setBrush(background)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.drawRoundedRect(r, 4, 4)
        painter.setPen(theme.qcolor("success_text" if done else "text"))
        painter.drawText(r, Qt.AlignCenter, txt)
        painter.restore()

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged and not getattr(self, "_syncing", False):
            self.canvas.on_item_dragging(self)
        return super().itemChange(change, value)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.canvas.on_item_released()


class NestCanvas(QGraphicsView):
    """Visualização com zoom (roda), pan (botão do meio ou espaço+arrastar) e edição."""
    itemsReleased = Signal()
    itemsDragging = Signal(object)
    contextMenuForItems = Signal(object, object)   # lista de PartItem, QPoint global
    zoomChanged = Signal(float)
    sheetCutClicked = Signal(int)                  # clique no "cortada" da etiqueta da placa
    previewFileClicked = Signal(str)               # clique no nome de um arquivo no "Arquivo original"
    nudgeRequested = Signal(float, float, bool)    # setas: deslocamento em mm (x, y) e "encostar" (Ctrl)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scale(1, -1)  # Y para cima, como no DXF
        self.params = NestParams()
        self.dark = False
        self.part_items: list[PartItem] = []
        self.sheet_items: list[SheetItem] = []
        self.sheet_labels: list = []
        self._panning = False
        self._pan_start = None
        self._auto_fit = False
        self._fit_pending = False
        self.editable = True
        self.mode = "empty"
        self._empty_text = None
        self.show_empty_hint()

    # ------------------------------------------------------------------
    def set_dark(self, dark: bool):
        self.dark = dark
        self.setBackgroundBrush(theme.qcolor("canvas"))
        for it in self.scene().items():
            it.update()
        for s in self.sheet_items:
            s.dark = dark

    def clear(self):
        self.part_items = []
        self.sheet_items = []
        self.sheet_labels = []
        self._review_hl = []                # realces da conferência (somem junto com a cena)
        self.scene().blockSignals(True)
        self.scene().clear()
        self.scene().blockSignals(False)
        self._empty_text = None

    def show_empty_hint(self):
        self.clear()
        self.mode = "empty"
        t = QGraphicsSimpleTextItem("Arraste arquivos DXF para cá\nou clique em “Abrir DXF” (Ctrl+O)")
        f = QFont()
        f.setPointSize(16)
        t.setFont(f)
        t.setBrush(QColor("#8a9099"))
        t.setFlag(QGraphicsItem.ItemIgnoresTransformations)
        self.scene().addItem(t)
        self._empty_text = t
        self.scene().setSceneRect(QRectF(-200, -100, 400, 200))
        t.setPos(-150, 30)

    def show_preview(self, preview: list[tuple[Prim, bool]], frames: dict | None = None,
                     labels: dict | None = None, issues: list | None = None, acked: set | None = None):
        """Mostra o desenho original importado (contornos abertos em vermelho).

        Com ``frames`` ({arquivo: [dx, dy, x0, y0, x1, y1]}), cada arquivo ganha quadro e rótulo
        (``labels``: {arquivo: (nome, solicitação, situação)}); ``issues`` marca aberturas e pontas
        soltas que ainda não foram conferidas (``acked``)."""
        from . import review_overlay
        self.clear()
        self.mode = "preview"
        self.preview_frames = dict(frames or {})
        groups: dict[tuple, QPainterPath] = {}
        problem = QPainterPath()
        repaired = QPainterPath()
        for pr, bad in preview:
            path = prim_path(pr)
            if pr.kind == "LINE" and pr.data.get("repair"):
                repaired.addPath(path)
            elif bad:
                problem.addPath(path)
            else:
                groups.setdefault(prim_rgb(pr), QPainterPath()).addPath(path)
        for col, path in groups.items():
            item = QGraphicsPathItem(path)
            pen = QPen(theme.line_color(QColor(*col), self.dark), 0)
            item.setPen(pen)
            self.scene().addItem(item)
        if not problem.isEmpty():
            item = QGraphicsPathItem(problem)
            pen = QPen(RED, 3)
            pen.setCosmetic(True)
            item.setPen(pen)
            item.setToolTip("Contorno aberto: a linha não fecha. Verifique no CAD se falta um trecho.")
            self.scene().addItem(item)
        if not repaired.isEmpty():
            item = QGraphicsPathItem(repaired)
            pen = QPen(theme.qcolor("success"), 3)
            pen.setCosmetic(True)
            item.setPen(pen)
            item.setToolTip("Trecho acrescentado na conferência para fechar uma abertura")
            self.scene().addItem(item)
        if self.preview_frames:
            review_overlay.add_frames(self, self.preview_frames, labels or {})
        if issues:
            review_overlay.add_issue_marks(self, issues, self.preview_frames, set(acked or ()))
        self.fit_all()

    def focus_rect(self, rect: QRectF, min_size: float = 40.0):
        """Aproxima a vista de um trecho do desenho (sem ficar absurdamente perto de uma abertura de 0,1 mm)."""
        if rect is None:
            return
        c = rect.center()
        w = max(rect.width() * 1.6, min_size)
        h = max(rect.height() * 1.6, min_size)
        self._auto_fit = False
        self.fitInView(QRectF(c.x() - w / 2, c.y() - h / 2, w, h), Qt.KeepAspectRatio)
        self.zoomChanged.emit(self.transform().m11())

    owner_colors: dict = {}
    specs: dict = {}                    # placa -> SheetSpec (chapa do material ou retalho)
    issue_geoms: dict = {}              # id da peça -> região com problema (parede fina…), coords da peça

    def issue_path(self, pid: str, mirrored: bool = False):
        geom = self.issue_geoms.get(pid)
        if geom is None or geom.is_empty:
            return None
        cache = self.__dict__.setdefault("_issue_paths", {})
        key = (pid, bool(mirrored), id(geom))
        path = cache.get(key)
        if path is None:
            path = QPainterPath()
            path.setFillRule(Qt.OddEvenFill)
            sx = -1.0 if mirrored else 1.0
            for g in getattr(geom, "geoms", [geom]):
                if g.geom_type != "Polygon":
                    continue
                for ring in [g.exterior, *g.interiors]:
                    pts = [QPointF(sx * x, y) for x, y in ring.coords]
                    if len(pts) > 2:
                        path.moveTo(pts[0])
                        for q in pts[1:]:
                            path.lineTo(q)
                        path.closeSubpath()
            cache[key] = path
        return path
    show_labels: bool = True
    cut_sheets: set = set()
    done_parts: set = set()
    filter_tag: str = ""

    def show_layout(self, parts: dict[str, Part], placements: list[Placement], params: NestParams,
                    n_sheets: int, keep_view: bool = False, specs: dict | None = None):
        """``specs``: {placa: SheetSpec} — chapa de cada material (banco de materiais) ou retalho. As placas
        ficam em "vagas" do tamanho da maior, para arrastar peças entre placas continuar simples."""
        import copy
        old = self.transform() if keep_view else None
        center = self.mapToScene(self.viewport().rect().center()) if keep_view else None
        selected = {(it.placement.part_id, it.placement.instance) for it in self.part_items if it.isSelected()}
        self.clear()
        self.mode = "layout"
        n = max(1, n_sheets)
        from ..core.sheets import SheetIndex
        from ..core.sheetspec import standard_spec
        idx = SheetIndex(parts, placements)
        specs = dict(specs or {})
        for i in range(n):
            specs.setdefault(i, standard_spec(params, idx.material.get(i)) if i in idx.material
                             else _spec_of(params))
        slot = copy.copy(params)
        slot.sheet_width = max([params.sheet_width] + [sp.width for sp in specs.values()])
        slot.sheet_height = max([params.sheet_height] + [sp.height for sp in specs.values()])
        self.params = slot
        self.specs = specs
        # grade de placas: colunas conforme a proporção da área visível (refeita quando a vista segue o
        # enquadramento automático; com zoom/arraste do técnico, a grade fica como está)
        grew = self._auto_fit and n != self._grid_n          # placa nova enquanto a vista segue o enquadramento
        if not keep_view or grew or self._cols <= 0:
            self._cols = self._best_columns(n)
        self._grid_n = n
        usage = usage_split({i: idx.net_area(i) for i in range(n) if idx.count(i)},
                            {i: specs[i].area for i in range(n)}, idx.material)
        for i in range(n):
            mat = idx.material.get(i, "")
            done = i in self.cut_sheets
            sp = specs[i]
            s = SheetItem(params, i, self.dark, mat, done, spec=sp, slot=slot)
            self.scene().addItem(s)
            self.sheet_items.append(s)
            cnt = idx.count(i)
            util = idx.net_area(i) / sp.area if sp.area else 0.0
            txt = f"Placa {idx.number.get(i, i + 1)}" + (" · retalho" if sp.is_remnant else "")
            sub_txt = (f"{cnt} {'peça' if cnt == 1 else 'peças'} · {100 * util:.1f}%".replace(".", ",")
                       if cnt else "vazia")
            low = i in usage["low"]
            if low:
                sub_txt += " · pouco usada"
            label = SheetLabel(txt, sub_txt, mat, done, sp.width, low=low)
            label.sheet_h = sp.height
            label.index = i
            ox, oy = self.sheet_origin(i)
            s.setPos(ox, oy)
            label.setPos(ox, oy + sp.height)
            self.scene().addItem(label)
            self.sheet_labels.append(label)
        for pl in placements:
            part = parts.get(pl.part_id)
            if part is None:
                continue
            it = PartItem(part, pl, self)
            it.setFlag(QGraphicsItem.ItemIsMovable, self.editable)
            self.scene().addItem(it)
            self._add_engrave_label(it, part, pl)
            self.part_items.append(it)
            if (pl.part_id, pl.instance) in selected:
                it.setSelected(True)
        r = self.scene().itemsBoundingRect().adjusted(-200, -200, 200, 200)
        self.scene().setSceneRect(r)
        if keep_view and old is not None and not grew:
            self.setTransform(old)
            self.centerOn(center)
        else:
            self.fit_all()

    # ------------------------------------------------------------------ grade de placas
    _cols = 0                           # colunas da grade (0 = ainda não decidido)
    _grid_n = 0                         # nº de placas quando a grade foi decidida

    def _best_columns(self, n: int) -> int:
        vp = self.viewport()
        return best_columns(n, self.params.sheet_width, self.params.sheet_height, grid_gaps(self.params),
                            vp.width(), vp.height())

    def sheet_origin(self, i: int) -> tuple[float, float]:
        """Canto inferior esquerdo da placa ``i`` na cena (única fonte da posição das placas)."""
        return grid_origin(self.params, i, self._cols)

    def _place_grid(self):
        """Reposiciona placas, etiquetas e peças depois de mudar o nº de colunas."""
        for s in self.sheet_items:
            s.setPos(*self.sheet_origin(s.index))
        for lb in self.sheet_labels:
            ox, oy = self.sheet_origin(lb.index)
            lb.setPos(ox, oy + getattr(lb, "sheet_h", self.params.sheet_height))
        for it in self.part_items:
            it.sync_from_placement()
        r = self.scene().itemsBoundingRect().adjusted(-200, -200, 200, 200)
        self.scene().setSceneRect(r)

    engrave_labels: dict = {}           # id da peça -> nº gravado (igual ao que vai no DXF)
    engrave_height: float = 3.0
    engrave_aci: int = 1

    def _add_engrave_label(self, item, part: Part, pl: Placement):
        """Mostra o nº da solicitação exatamente onde ele vai ser gravado (filho da peça: acompanha
        quando ela é arrastada)."""
        text = self.engrave_labels.get(pl.part_id)
        if not text:
            return
        from ..core.dxf_export import label_spot, stroke_lines
        from ..core.validate import fine_solid, placed_geometry
        cache = part.__dict__.setdefault("_engrave_spots", {})
        key = (text, round(float(self.engrave_height), 2), round(pl.rotation, 3), bool(pl.mirrored))
        if key not in cache:
            try:
                solid = fine_solid(part)
                cache[key] = (solid, label_spot(placed_geometry(solid, Placement(pl.part_id, 0, 0, 0, 0,
                                                                                 pl.rotation, pl.mirrored)),
                                                text, self.engrave_height))
            except Exception:
                cache[key] = (None, None)
        _, spot = cache[key]
        if spot is None:
            return
        x, y, h = spot
        path = QPainterPath()
        for pts in stroke_lines(text, x, y, h):
            path.moveTo(*pts[0])
            for p in pts[1:]:
                path.lineTo(*p)
        # coordenadas da peça posicionada em (0, 0) -> coordenadas locais do item (desfaz a rotação)
        from PySide6.QtGui import QTransform
        local = QTransform().rotate(-pl.rotation).map(path)
        from PySide6.QtWidgets import QGraphicsPathItem
        from ..core.geometry import aci_to_rgb
        child = QGraphicsPathItem(local, item)
        pen = QPen(QColor(*aci_to_rgb(int(self.engrave_aci))), max(0.25, h * 0.12))
        pen.setCapStyle(Qt.RoundCap)
        child.setPen(pen)
        child.setToolTip(f"Nº {text} gravado aqui")
        child.setAcceptedMouseButtons(Qt.NoButton)

    def set_filter(self, tag: str):
        self.filter_tag = tag
        for it in self.part_items:
            it.update()

    def set_cut(self, cut: set):
        """Atualiza só a marcação 'cortada' das placas (sem redesenhar as peças)."""
        self.cut_sheets = cut
        for it in self.sheet_items:
            if it.done != (it.index in cut):
                it.done = it.index in cut
                it.update()
        for lb in self.sheet_labels:
            if lb.done != (lb.index in cut):
                lb.set_done(lb.index in cut)
        for it in self.part_items:
            it.update()

    def set_editable(self, editable: bool):
        self.editable = editable
        for it in self.part_items:
            it.setFlag(QGraphicsItem.ItemIsMovable, editable)

    # ------------------------------------------------------------------
    def fit_all(self):
        r = self.scene().itemsBoundingRect()
        if r.isEmpty():
            return
        self.scene().setSceneRect(r.adjusted(-r.width(), -r.height(), r.width(), r.height()))
        self.fitInView(r.adjusted(-15, -15, 15, 15), Qt.KeepAspectRatio)
        # As etiquetas têm altura fixa na tela, independente da escala do desenho.
        if self.mode in ("layout", "preview"):
            scale = max(abs(self.transform().m11()), 1e-3)
            self.fitInView(r.adjusted(-15, -15, 15, 40 / scale), Qt.KeepAspectRatio)
        self._auto_fit = True                # janela mudou de tamanho: enquadra de novo (até o usuário dar zoom)
        self.zoomChanged.emit(self.transform().m11())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(self, "_auto_fit", False) and self.mode in ("layout", "preview") \
                and not getattr(self, "_fit_pending", False):
            self._fit_pending = True
            QTimer.singleShot(0, self._refit)

    def _refit(self):
        self._fit_pending = False
        if self._auto_fit:
            if self.mode == "layout" and self.sheet_items:
                cols = self._best_columns(len(self.sheet_items))
                if cols != self._cols:
                    self._cols = cols
                    self._place_grid()
            self.fit_all()

    def focus_sheet(self, i: int):
        self._auto_fit = False
        p = self.params
        sp = getattr(self, "specs", {}).get(i)
        ox, oy = self.sheet_origin(i)
        r = QRectF(ox, oy, sp.width if sp else p.sheet_width, sp.height if sp else p.sheet_height)
        self.fitInView(r.adjusted(-15, -25, 15, 25), Qt.KeepAspectRatio)

    def current_sheet(self) -> int:
        """Placa mais perto do centro da vista (em linha ou em grade)."""
        if not self.sheet_items:
            return 0
        c = self.mapToScene(self.viewport().rect().center())
        w, h = self.params.sheet_width / 2, self.params.sheet_height / 2

        def dist(s):
            return (s.pos().x() + w - c.x()) ** 2 + (s.pos().y() + h - c.y()) ** 2
        return min(self.sheet_items, key=dist).index

    def sheet_at(self, x: float, y: float = 0.0) -> int:
        """Vaga de placa sob o ponto (x, y) da cena; pode ser a vaga logo depois da última (placa nova)."""
        return grid_index(self.params, x, y, self._cols)

    def selected_items(self) -> list[PartItem]:
        return [it for it in self.part_items if it.isSelected()]

    # ------------------------------------------------------------------
    def on_item_dragging(self, item: PartItem):
        self.itemsDragging.emit(item)

    def on_item_released(self):
        self.itemsReleased.emit()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta == 0:
            return
        f = 1.0015 ** delta
        self._auto_fit = False
        cur = self.transform().m11()
        if (cur > 200 and f > 1) or (cur < 0.005 and f < 1):
            return
        self.scale(f, f)
        self.zoomChanged.emit(self.transform().m11())

    def mousePressEvent(self, event):
        if event.button() == Qt.MiddleButton or (event.button() == Qt.LeftButton and
                                                   QApplication.keyboardModifiers() & Qt.AltModifier):
            self._panning = True
            self._auto_fit = False
            self._pan_start = event.position()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._panning:
            d = event.position() - self._pan_start
            self._pan_start = event.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(d.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(d.y()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._panning and event.button() in (Qt.MiddleButton, Qt.LeftButton):
            self._panning = False
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        """Setas movem a(s) peça(s) selecionada(s): 1 mm; Shift = 10 mm; Alt = 0,1 mm; Ctrl = encostar
        na vizinha/margem. Só com o desenho em foco (campos numéricos continuam com as setas deles)."""
        step = nudge_step(event.key(), event.modifiers())
        if step is not None and self.mode == "layout" and self.editable and self.selected_items():
            (dx, dy), snap = step
            self.nudgeRequested.emit(dx, dy, snap)
            event.accept()
            return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event):
        if self.mode != "layout":
            return
        item = self.itemAt(event.pos())
        while item is not None and not isinstance(item, PartItem):
            item = item.parentItem()
        if isinstance(item, PartItem):
            if not item.isSelected():
                self.scene().clearSelection()
                item.setSelected(True)
            self.contextMenuForItems.emit(self.selected_items(), event.globalPos())
