"""Canvas (QGraphicsView): placas, peças, zoom, pan, arrastar e girar."""
from __future__ import annotations


from PySide6.QtCore import QPointF, QRectF, Qt, Signal
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
    gap = max(30.0, 0.08 * params.sheet_width)
    return index * (params.sheet_width + gap)


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
        self.setPos(sheet_offset(slot or params, index), 0)
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

    def __init__(self, title: str, sub: str, material: str = "", done: bool = False, sheet_w: float = 0.0):
        super().__init__()
        self.title, self.sub, self.material, self.done = title, sub, material, done
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
        self.setToolTip("Clique em “cortada” para marcar/desmarcar esta placa (atalho: C)")
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
            painter.setPen(theme.qcolor("muted"))
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
        self.setPos(sheet_offset(self.canvas.params, pl.sheet_index) + pl.x, pl.y)
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
        for col, path in g.lines:
            c = col
            if dark and c.lightness() < 60:
                c = QColor(230, 230, 230)
            pen = QPen(c, 0)
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

    def show_preview(self, preview: list[tuple[Prim, bool]]):
        """Mostra o desenho original importado (contornos abertos em vermelho)."""
        self.clear()
        self.mode = "preview"
        groups: dict[tuple, QPainterPath] = {}
        problem = QPainterPath()
        for pr, bad in preview:
            path = prim_path(pr)
            if bad:
                problem.addPath(path)
            else:
                groups.setdefault(prim_rgb(pr), QPainterPath()).addPath(path)
        for col, path in groups.items():
            item = QGraphicsPathItem(path)
            c = QColor(*col)
            if self.dark and c.lightness() < 60:
                c = QColor(230, 230, 230)
            pen = QPen(c, 0)
            item.setPen(pen)
            self.scene().addItem(item)
        if not problem.isEmpty():
            item = QGraphicsPathItem(problem)
            pen = QPen(RED, 3)
            pen.setCosmetic(True)
            item.setPen(pen)
            item.setToolTip("Contorno aberto: a linha não fecha. Verifique no CAD se falta um trecho.")
            self.scene().addItem(item)
        self.fit_all()

    owner_colors: dict = {}
    specs: dict = {}                    # placa -> SheetSpec (chapa do material ou retalho)
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
            label = SheetLabel(txt, sub_txt, mat, done, sp.width)
            label.setPos(sheet_offset(slot, i), sp.height)
            label.index = i
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
        if keep_view and old is not None:
            self.setTransform(old)
            self.centerOn(center)
        else:
            self.fit_all()

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
        if self.mode == "layout":
            scale = max(abs(self.transform().m11()), 1e-3)
            self.fitInView(r.adjusted(-15, -15, 15, 40 / scale), Qt.KeepAspectRatio)
        self.zoomChanged.emit(self.transform().m11())

    def focus_sheet(self, i: int):
        p = self.params
        sp = getattr(self, "specs", {}).get(i)
        r = QRectF(sheet_offset(p, i), 0, sp.width if sp else p.sheet_width, sp.height if sp else p.sheet_height)
        self.fitInView(r.adjusted(-15, -25, 15, 25), Qt.KeepAspectRatio)

    def current_sheet(self) -> int:
        if not self.sheet_items:
            return 0
        c = self.mapToScene(self.viewport().rect().center())
        best = min(self.sheet_items, key=lambda s: abs(s.pos().x() + self.params.sheet_width / 2 - c.x()))
        return best.index

    def sheet_at(self, x: float) -> int:
        p = self.params
        gap = sheet_offset(p, 1) - p.sheet_width
        i = int((x + gap / 2) // (p.sheet_width + gap))
        return max(0, i)

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
        cur = self.transform().m11()
        if (cur > 200 and f > 1) or (cur < 0.005 and f < 1):
            return
        self.scale(f, f)
        self.zoomChanged.emit(self.transform().m11())

    def mousePressEvent(self, event):
        if event.button() == Qt.MiddleButton or (event.button() == Qt.LeftButton and
                                                   QApplication.keyboardModifiers() & Qt.AltModifier):
            self._panning = True
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
