"""Aba "Caixa": gera uma caixa com encaixe de dentes, mostra em 3D e manda as peças para o encaixe."""
from __future__ import annotations

import json
import math
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFormLayout, QFrame, QGraphicsScene,
                               QGraphicsSimpleTextItem, QGraphicsView, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QScrollArea, QSlider, QSpinBox, QStackedWidget, QVBoxLayout, QWidget)
from shapely.geometry import box as rect
from shapely.geometry.polygon import orient

from ..core.boxgen import (PRIORITY, LID_LIFT, LID_NAMES, LID_TYPES, BoxParams, BoxResult, flat_layout, generate,
                           summary, validate)
from . import theme
from .dialogs import settings
from .settings_panel import _dspin, _Section


def _fmt(v: float) -> str:
    return f"{v:.1f}".rstrip("0").rstrip(".").replace(".", ",")


# ------------------------------------------------------------------------------------------ vista 3D
class Box3DView(QWidget):
    """Caixa em 3D desenhada com QPainter (projeção ortográfica, faces ordenadas por profundidade).

    Arrastar gira, a roda dá zoom, duplo clique volta à vista inicial."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.result: Optional[BoxResult] = None
        self.explode = 0.0                     # 0 = montada, 1 = bem separada
        self.yaw, self.pitch, self.zoom = -35.0, 28.0, 1.0
        self._drag: Optional[QPointF] = None
        self._faces: list = []                 # cache: faces em 3D (independe da câmera)
        self.setMinimumSize(200, 200)
        self.setMouseTracking(False)
        self.setCursor(Qt.OpenHandCursor)

    # ---------------- dados
    def set_result(self, result: Optional[BoxResult]):
        self.result = result
        self._build_faces()
        self.update()

    def set_explode(self, f: float):
        self.explode = max(0.0, min(1.0, f))
        self._build_faces()
        self.update()

    def reset_view(self):
        self.yaw, self.pitch, self.zoom = -35.0, 28.0, 1.0
        self.update()

    def _build_faces(self):
        """Lista de faces: (pontos 3D, normal 3D, tipo) — "face" = lado largo, "borda" = espessura."""
        self._faces = []
        self._items = []                       # centro 3D de cada pedaço (ordem de desenho)
        self._boxes = []                       # caixa delimitadora 3D do miolo de cada pedaço
        self._order_key = None
        r = self.result
        if r is None:
            return
        t = r.params.thickness
        d = r.dims
        dist = self.explode * max(d.W, d.D, d.total_h) * 0.55
        for pn in r.panels:
            ex = tuple(c * dist for c in pn.explode)
            pieces = [pn.poly]
            if pn.splits:                      # divisórias: um pedaço entre cada cruzamento
                minx, miny, maxx, maxy = pn.poly.bounds
                cuts = [minx - 1] + list(pn.splits) + [maxx + 1]
                pieces = [pn.poly.intersection(rect(a, miny - 1, b, maxy + 1)) for a, b in zip(cuts[:-1], cuts[1:])]
                pieces = [g for pc in pieces for g in getattr(pc, "geoms", [pc]) if g.area > 1e-6]
            for piece in pieces:
                self._add_piece(pn, orient(piece, 1.0), ex, t, pn.circles if piece is pn.poly else [])

    def _add_piece(self, pn, poly, ex, t, circles):
        def P(u, v, s):
            x, y, z = pn.to3d(u, v, s)
            return (x + ex[0], y + ex[1], z + ex[2])

        U, V, N = pn.U, pn.V, pn.N
        minx, miny, maxx, maxy = poly.bounds
        center = P((minx + maxx) / 2, (miny + maxy) / 2, t / 2)
        item = len(self._items)
        self._items.append(center)
        # "miolo" da peça: sem os dentes que entram nas vizinhas, para as caixas não se cruzarem
        a, b, c, d = minx, miny, maxx, maxy
        if pn.kind in PRIORITY:
            a, b, c, d = a + t, b + t, c - t, d - t
        elif pn.kind == "divisoria":
            b = max(b, 0.0)
            if any(abs(a - sp) < 1e-6 for sp in pn.splits):
                a += t / 2
            if any(abs(c - sp) < 1e-6 for sp in pn.splits):
                c -= t / 2
        e = 1e-3
        corners = [P(uu, vv, ss) for uu in (a + e, c - e) for vv in (b + e, d - e) for ss in (e, t - e)]
        self._boxes.append((tuple(min(q[k] for q in corners) for k in range(3)),
                            tuple(max(q[k] for q in corners) for k in range(3))))
        rings = [list(poly.exterior.coords)[:-1]] + [list(h.coords)[:-1] for h in poly.interiors]
        for cu, cv, rad in circles:            # furos redondos: anéis de 32 lados (só na vista)
            rings.append([(cu + rad * math.cos(-2 * math.pi * k / 32), cv + rad * math.sin(-2 * math.pi * k / 32))
                          for k in range(32)])
        for s, sign in ((0.0, -1.0), (t, 1.0)):
            pts = [[P(u, v, s) for u, v in ring] for ring in rings]
            self._faces.append((pts, tuple(sign * c for c in N), "face", item))
        for ring in rings:
            n = len(ring)
            for i in range(n):
                (u0, v0), (u1, v1) = ring[i], ring[(i + 1) % n]
                du, dv = u1 - u0, v1 - v0
                L = math.hypot(du, dv)
                if L < 1e-9:
                    continue
                if abs(du) < 1e-6 and any(abs(u0 - sp) < 1e-6 for sp in pn.splits):
                    continue                   # corte artificial entre pedaços: não é borda de verdade
                nu, nv = dv / L, -du / L       # normal para fora do material (anel externo anti-horário)
                nrm = (nu * U[0] + nv * V[0], nu * U[1] + nv * V[1], nu * U[2] + nv * V[2])
                quad = [P(u0, v0, 0), P(u1, v1, 0), P(u1, v1, t), P(u0, v0, t)]
                self._faces.append(([quad], nrm, "borda", item))

    def _draw_rank(self, fwd) -> list:
        """Posição de desenho de cada pedaço (0 = desenha primeiro).

        Duas peças que não se cruzam têm um eixo (x, y ou z) que as separa: a que fica do lado de
        lá desse eixo, olhando da câmera, vem antes. Isso só depende do octante para onde a câmera
        olha, então a ordem é calculada uma vez por octante (ordenação topológica)."""
        signs = tuple(1 if c > 1e-9 else (-1 if c < -1e-9 else 0) for c in fwd)
        if self._order_key == signs:
            return self._rank
        import numpy as np
        n = len(self._boxes)
        mins = np.array([b[0] for b in self._boxes]).reshape(n, 3)
        maxs = np.array([b[1] for b in self._boxes]).reshape(n, 3)
        before = np.zeros((n, n), dtype=bool)            # before[i, j]: i é desenhada antes de j
        decided = np.zeros((n, n), dtype=bool)
        for k in sorted(range(3), key=lambda k: -abs(fwd[k])):
            if signs[k] == 0:
                continue
            lt = maxs[:, None, k] <= mins[None, :, k] + 1e-6   # i inteira abaixo de j no eixo k
            sep = (lt | lt.T) & ~decided
            # olhando para +k, quem tem k maior está mais longe e vai antes
            before |= sep & (lt.T if signs[k] > 0 else lt)
            decided |= sep
        np.fill_diagonal(before, False)
        depth = np.array([sum((c[q]) * fwd[q] for q in range(3)) for c in self._items])
        indeg = before.sum(axis=0)
        done = np.zeros(n, dtype=bool)
        rank = [0] * n
        for pos in range(n):
            ready = np.nonzero(~done & (indeg == 0))[0]
            if len(ready) == 0:                          # ciclo (raro): quebra pelo mais distante
                ready = np.nonzero(~done)[0]
            i = int(ready[np.argmax(depth[ready])])
            rank[i] = pos
            done[i] = True
            indeg -= before[i].astype(indeg.dtype)
        self._order_key, self._rank = signs, rank
        return rank

    # ---------------- câmera
    def _basis(self):
        cy, sy = math.cos(math.radians(self.yaw)), math.sin(math.radians(self.yaw))
        cp, sp = math.cos(math.radians(self.pitch)), math.sin(math.radians(self.pitch))
        right = (cy, -sy, 0.0)
        fwd = (sy * cp, cy * cp, -sp)                 # direção em que a câmera olha
        up = (sy * sp, cy * sp, cp)
        return right, up, fwd

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        tk = theme.tokens()
        p.fillRect(self.rect(), QColor(tk["canvas"]))
        r = self.result
        if r is None or not self._faces:
            p.setPen(QColor(tk["muted"]))
            p.drawText(self.rect(), Qt.AlignCenter, "Ajuste as medidas para ver a caixa")
            p.end()
            return
        right, up, fwd = self._basis()
        d = r.dims
        cx, cy, cz = d.W / 2, d.D / 2, d.total_h / 2
        dot = lambda a, b: a[0] * b[0] + a[1] * b[1] + a[2] * b[2]  # noqa: E731
        diag = math.sqrt(d.W ** 2 + d.D ** 2 + d.total_h ** 2) * (1 + self.explode * 0.9)
        scale = min(self.width(), self.height()) / diag * 0.92 * self.zoom
        ox, oy = self.width() / 2, self.height() / 2

        def proj(q):
            rel = (q[0] - cx, q[1] - cy, q[2] - cz)
            return QPointF(ox + dot(rel, right) * scale, oy - dot(rel, up) * scale), dot(rel, fwd)

        light = (0.35, -0.55, 0.76)
        dark = theme._dark
        wood = QColor("#d9d4cc") if not dark else QColor("#c9c2b6")
        burnt = QColor("#7a4127")
        rank = self._draw_rank(fwd)
        draw = []
        for rings, nrm, kind, item in self._faces:
            if dot(nrm, fwd) >= -1e-6:              # de costas para a câmera
                continue
            pts2, depth = [], 0.0
            for ring in rings:
                pr = [proj(q) for q in ring]
                pts2.append([a for a, _ in pr])
                depth += sum(b for _, b in pr) / len(pr)
            depth /= len(rings)
            if kind == "face":
                lum = 0.70 + 0.30 * max(0.0, dot(nrm, light))
                col = QColor.fromRgbF(min(1, wood.redF() * lum), min(1, wood.greenF() * lum),
                                      min(1, wood.blueF() * lum))
            else:
                lum = 0.75 + 0.25 * max(0.0, dot(nrm, light))
                col = QColor.fromRgbF(burnt.redF() * lum, burnt.greenF() * lum, burnt.blueF() * lum)
            draw.append((rank[item], -depth, kind == "face", pts2, col))
        # peça por peça (ordem topológica); dentro da peça, as faces mais longe primeiro
        draw.sort(key=lambda x: (x[0], x[1]))
        edge = QPen(QColor(60, 35, 25, 150), 0.8)
        edge.setCosmetic(True)
        for _, _, is_face, pts2, col in draw:
            path = QPainterPath()
            path.setFillRule(Qt.OddEvenFill)
            for ring in pts2:
                path.addPolygon(QPolygonF(ring + [ring[0]]))
            p.setBrush(QBrush(col))
            p.setPen(edge if is_face else QPen(col.darker(115), 0.6))
            p.drawPath(path)
        p.setPen(QColor(tk["muted"]))
        f = QFont(p.font())
        f.setPointSizeF(8)
        p.setFont(f)
        p.drawText(QRectF(8, self.height() - 22, self.width() - 16, 18), Qt.AlignLeft | Qt.AlignVCenter,
                   "Arrastar: girar  ·  roda: zoom  ·  duplo clique: vista inicial")
        p.end()

    # ---------------- mouse
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.position()
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            dpos = e.position() - self._drag
            self._drag = e.position()
            self.yaw = (self.yaw + dpos.x() * 0.5) % 360
            self.pitch = max(-89.0, min(89.0, self.pitch + dpos.y() * 0.5))
            self.update()

    def mouseReleaseEvent(self, _e):
        self._drag = None
        self.setCursor(Qt.OpenHandCursor)

    def mouseDoubleClickEvent(self, _e):
        self.reset_view()

    def wheelEvent(self, e):
        self.zoom = max(0.3, min(6.0, self.zoom * (1.12 if e.angleDelta().y() > 0 else 1 / 1.12)))
        self.update()


# ------------------------------------------------------------------------------------------ peças planificadas
class FlatView(QGraphicsView):
    """As peças como vão para o laser (já com kerf), lado a lado, com o nome de cada uma."""

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setFrameShape(QFrame.NoFrame)
        self.scale(1, -1)                       # y para cima, como no DXF

    def set_result(self, result: Optional[BoxResult]):
        sc = self.scene()
        sc.clear()
        tk = theme.tokens()
        self.setBackgroundBrush(QColor(tk["canvas"]))
        if result is None:
            return
        pen = QPen(QColor(tk["text"]), 0)
        pen.setCosmetic(True)
        fill = QBrush(theme.qcolor("part_fill"))
        for pn, g, circles in flat_layout(result):
            path = QPainterPath()
            path.setFillRule(Qt.OddEvenFill)
            for ring in [g.exterior] + list(g.interiors):
                path.addPolygon(QPolygonF([QPointF(x, y) for x, y in ring.coords]))
            for cx, cy, r in circles:
                path.addEllipse(QPointF(cx, cy), r, r)
            sc.addPath(path, pen, fill)
            c = g.representative_point()
            txt = QGraphicsSimpleTextItem(pn.name)
            txt.setBrush(QColor(tk["muted"]))
            f = QFont()
            f.setPointSizeF(max(2.5, min(7.0, (g.bounds[3] - g.bounds[1]) / 7)))
            txt.setFont(f)
            txt.setTransform(txt.transform().scale(1, -1))
            br = txt.boundingRect()
            ty = c.y + br.height() / 2
            if circles:                        # não escrever em cima do furo do dedo
                cx_, cy_, rr = circles[0]
                ty = cy_ - rr - 2
            txt.setPos(c.x - br.width() / 2, ty)
            sc.addItem(txt)
        QTimer.singleShot(0, self.fit)

    def fit(self):
        r = self.scene().itemsBoundingRect().adjusted(-10, -10, 10, 10)
        if r.isValid():
            self.fitInView(r, Qt.KeepAspectRatio)

    def wheelEvent(self, e):
        f = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
        self.scale(f, f)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.fit()


# ------------------------------------------------------------------------------------------ painel
class BoxPanel(QWidget):
    """Controles à esquerda; caixa em 3D (ou peças planificadas) à direita."""
    sendRequested = Signal()        # mandar as peças para o encaixe
    saveRequested = Signal()        # salvar só o DXF

    def __init__(self, parent=None):
        super().__init__(parent)
        self.result: Optional[BoxResult] = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self.regenerate)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._build_controls())
        lay.addWidget(self._build_view(), 1)
        self._load_settings()
        self.regenerate()

    # ---------------- montagem
    def _build_controls(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFixedWidth(340)
        self._controls = scroll
        body = QWidget()
        scroll.setWidget(body)
        v = QVBoxLayout(body)
        v.setContentsMargins(12, 12, 6, 12)
        v.setSpacing(4)
        title = QLabel("Gerador de caixas")
        title.setObjectName("SectionTitle")
        v.addWidget(title)

        g1 = _Section("Medidas", "box")
        f1 = QFormLayout(g1.body)
        self.measure = QComboBox()
        self.measure.addItem("Externas (tamanho final)", False)
        self.measure.addItem("Internas (espaço útil)", True)
        self.measure.setToolTip("Externas: a caixa fica com exatamente estas medidas por fora.\n"
                                "Internas: o espaço de dentro fica com estas medidas.")
        self.w = _dspin(10, 3000, 5, 1, " mm", "Largura (X)")
        self.d = _dspin(10, 3000, 5, 1, " mm", "Profundidade (Y)")
        self.h = _dspin(10, 3000, 5, 1, " mm", "Altura (Z). Com tampa solta, inclui a tampa.")
        f1.addRow("Medidas", self.measure)
        f1.addRow("Largura", self.w)
        f1.addRow("Profundidade", self.d)
        f1.addRow("Altura", self.h)
        v.addWidget(g1)

        g2 = _Section("Material e encaixe", "layers")
        f2 = QFormLayout(g2.body)
        self.t = _dspin(0.5, 20, 0.5, 2, " mm", "Espessura da placa (MDF 3 mm, acrílico 3 mm…)")
        self.finger = _dspin(2, 200, 1, 1, " mm",
                             "Largura aproximada de cada dente. O programa ajusta para caber um\n"
                             "número ímpar de dentes em cada aresta.")
        self.kerf = _dspin(0, 1, 0.01, 2, " mm",
                           "Quanto o laser queima de material (0 = sem compensação).\n"
                           "Típico: 0,10–0,20 mm em MDF 3 mm. Deixa o encaixe justo, sem cola.")
        f2.addRow("Espessura", self.t)
        f2.addRow("Largura do dente", self.finger)
        f2.addRow("Kerf (laser)", self.kerf)
        v.addWidget(g2)

        g3 = _Section("Tampa", "box")
        f3 = QFormLayout(g3.body)
        self.lid = QComboBox()
        for k in LID_TYPES:
            self.lid.addItem(LID_NAMES[k], k)
        self.lid.setToolTip("Aberta: só base e paredes.\nFechada: tampa com dentes (fica colada).\n"
                            "Tampa solta: placa de cima + uma guia colada por baixo que encaixa na boca da caixa.")
        self.lid_gap = _dspin(0, 3, 0.1, 1, " mm", "Folga de cada lado da guia da tampa solta")
        self.hole = _dspin(0, 80, 1, 0, " mm", "Diâmetro do furo para levantar a tampa (0 = sem furo)")
        f3.addRow("Tipo", self.lid)
        self.lid_gap_lbl = QLabel("Folga da guia")
        self.hole_lbl = QLabel("Furo para o dedo")
        f3.addRow(self.lid_gap_lbl, self.lid_gap)
        f3.addRow(self.hole_lbl, self.hole)
        v.addWidget(g3)

        g4 = _Section("Divisórias", "grid")
        f4 = QFormLayout(g4.body)
        self.cols = QSpinBox()
        self.cols.setRange(1, 20)
        self.cols.setToolTip("Quantos compartimentos ao longo da largura")
        self.rows = QSpinBox()
        self.rows.setRange(1, 20)
        self.rows.setToolTip("Quantos compartimentos ao longo da profundidade")
        presets = QHBoxLayout()
        presets.setSpacing(4)
        for c, r in ((1, 1), (2, 2), (3, 2), (4, 3)):
            b = QPushButton("Sem" if (c, r) == (1, 1) else f"{c}×{r}")
            b.setToolTip("Sem divisórias" if (c, r) == (1, 1) else f"{c} × {r} compartimentos")
            b.clicked.connect(lambda _=False, c=c, r=r: self._set_grid(c, r))
            presets.addWidget(b)
        f4.addRow("Colunas", self.cols)
        f4.addRow("Linhas", self.rows)
        f4.addRow(presets)
        v.addWidget(g4)

        g5 = _Section("Saída", "export")
        f5 = QFormLayout(g5.body)
        self.qty = QSpinBox()
        self.qty.setRange(1, 200)
        self.qty.setToolTip("Quantas caixas iguais mandar para o encaixe")
        self.material = QLineEdit()
        self.material.setToolTip("Nome do material no encaixe (peças de materiais diferentes nunca dividem placa).\n"
                                 "Em branco: MDF + espessura.")
        self.engrave = QCheckBox("Gravar o nome em cada peça")
        self.engrave.setToolTip("Escreve o nome (Frente/Fundo, Lateral…) numa camada azul de gravação")
        f5.addRow("Quantidade", self.qty)
        f5.addRow("Material", self.material)
        f5.addRow(self.engrave)
        v.addWidget(g5)
        v.addStretch(1)

        for form in body.findChildren(QFormLayout):
            form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
            form.setContentsMargins(0, 0, 0, 0)
            form.setHorizontalSpacing(10)
            form.setVerticalSpacing(8)
        for wdg in (self.w, self.d, self.h, self.t, self.finger, self.kerf, self.lid_gap, self.hole):
            wdg.valueChanged.connect(self._changed)
        for wdg in (self.cols, self.rows, self.qty):
            wdg.valueChanged.connect(self._changed)
        for cb in (self.measure, self.lid):
            cb.currentIndexChanged.connect(self._changed)
        self.engrave.toggled.connect(self._changed)
        self.material.textChanged.connect(self._material_changed)
        return scroll

    def _build_view(self) -> QWidget:
        card = QFrame()
        card.setObjectName("Card")
        v = QVBoxLayout(card)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(6)
        head = QHBoxLayout()
        self.btn_3d = QPushButton("Caixa 3D")
        self.btn_flat = QPushButton("Peças para cortar")
        grp = QButtonGroup(self)
        for i, b in enumerate((self.btn_3d, self.btn_flat)):
            b.setCheckable(True)
            grp.addButton(b, i)
        self.btn_3d.setChecked(True)
        grp.idClicked.connect(self._view_mode)
        head.addWidget(self.btn_3d)
        head.addWidget(self.btn_flat)
        head.addSpacing(12)
        self.explode_lbl = QLabel("Montagem")
        self.explode_lbl.setObjectName("Muted")
        self.explode = QSlider(Qt.Horizontal)
        self.explode.setRange(0, 100)
        self.explode.setMaximumWidth(200)
        self.explode.setToolTip("Separa as peças para ver como a caixa se monta")
        self.explode.valueChanged.connect(lambda val: self.view3d.set_explode(val / 100))
        head.addWidget(self.explode_lbl)
        head.addWidget(self.explode)
        head.addStretch(1)
        self.btn_fit = QPushButton("Enquadrar")
        self.btn_fit.clicked.connect(self._fit)
        head.addWidget(self.btn_fit)
        v.addLayout(head)

        self.msg_box = QFrame()
        self.msg_box.setObjectName("Banner")
        mb = QHBoxLayout(self.msg_box)
        mb.setContentsMargins(10, 6, 6, 6)
        self.msg = QLabel()
        self.msg.setObjectName("BannerText")
        self.msg.setWordWrap(True)
        mb.addWidget(self.msg, 1)
        self.msg_box.hide()
        v.addWidget(self.msg_box)

        self.views = QStackedWidget()
        self.view3d = Box3DView()
        self.flat = FlatView()
        self.views.addWidget(self.view3d)
        self.views.addWidget(self.flat)
        v.addWidget(self.views, 1)

        bottom = QHBoxLayout()
        self.info = QLabel("")
        self.info.setObjectName("Muted")
        self.info.setWordWrap(True)
        self.btn_save = QPushButton("Salvar DXF…")
        self.btn_save.setToolTip("Só grava o DXF com as peças da caixa (sem encaixar)")
        self.btn_save.clicked.connect(self.saveRequested.emit)
        self.btn_send = QPushButton("Enviar para o encaixe")
        self.btn_send.setObjectName("success")
        self.btn_send.setToolTip("Coloca as peças na aba Encaixe (junto com o que já estiver lá) e encaixa na placa")
        self.btn_send.clicked.connect(self.sendRequested.emit)
        bottom.addWidget(self.info, 1)
        bottom.addWidget(self.btn_save)
        bottom.addWidget(self.btn_send)
        v.addLayout(bottom)
        outer = QWidget()
        ol = QVBoxLayout(outer)
        ol.setContentsMargins(6, 12, 12, 12)
        ol.addWidget(card)
        return outer

    # ---------------- parâmetros
    def params(self) -> BoxParams:
        return BoxParams(width=self.w.value(), depth=self.d.value(), height=self.h.value(),
                         inner=bool(self.measure.currentData()), thickness=self.t.value(),
                         finger=self.finger.value(), kerf=self.kerf.value(),
                         lid=self.lid.currentData() or LID_TYPES[0], cols=self.cols.value(), rows=self.rows.value(),
                         lid_clearance=self.lid_gap.value(), finger_hole=self.hole.value(),
                         engrave_names=self.engrave.isChecked(), quantity=self.qty.value(),
                         material=self.material.text().strip())

    def set_params(self, p: BoxParams):
        widgets = ((self.w, p.width), (self.d, p.depth), (self.h, p.height), (self.t, p.thickness),
                   (self.finger, p.finger), (self.kerf, p.kerf), (self.cols, p.cols), (self.rows, p.rows),
                   (self.lid_gap, p.lid_clearance), (self.hole, p.finger_hole), (self.qty, p.quantity))
        for wdg, val in widgets:
            wdg.blockSignals(True)
            wdg.setValue(val)
            wdg.blockSignals(False)
        for cb, data in ((self.measure, bool(p.inner)), (self.lid, p.lid)):
            cb.blockSignals(True)
            cb.setCurrentIndex(max(0, cb.findData(data)))
            cb.blockSignals(False)
        self.engrave.blockSignals(True)
        self.engrave.setChecked(bool(p.engrave_names))
        self.engrave.blockSignals(False)
        self.material.blockSignals(True)
        self.material.setText(p.material)
        self.material.blockSignals(False)
        self._update_material_hint()
        self._lid_ui()

    def _load_settings(self):
        try:
            p = BoxParams.from_json(json.loads(settings().value("box/params", "{}") or "{}"))
        except (ValueError, TypeError):
            p = BoxParams()
        self.set_params(p)

    def _save_settings(self):
        settings().setValue("box/params", json.dumps(self.params().to_json()))

    def _set_grid(self, c: int, r: int):
        self.cols.blockSignals(True)
        self.cols.setValue(c)
        self.cols.blockSignals(False)
        self.rows.setValue(r)
        if (self.cols.value(), self.rows.value()) == (c, r):
            self._changed()

    def _lid_ui(self):
        lift = self.lid.currentData() == LID_LIFT
        for wdg in (self.lid_gap, self.hole, self.lid_gap_lbl, self.hole_lbl):
            wdg.setVisible(lift)

    def _update_material_hint(self):
        self.material.setPlaceholderText(f"MDF {self.t.value():g}mm")

    def _material_changed(self, *_):
        self._save_settings()

    def _changed(self, *_):
        self._lid_ui()
        self._update_material_hint()
        self._timer.start()

    # ---------------- geração e vistas
    def regenerate(self):
        p = self.params()
        errs = validate(p)
        if errs:
            self.result = None
            self._message(errs[0], "warn")
            self.view3d.set_result(None)
            self.flat.set_result(None)
            self.info.setText("")
            self._update_buttons()
            return
        self.result = generate(p)
        self._save_settings()
        if self.result.warnings:
            self._message(" ".join(self.result.warnings), "info")
        else:
            self.msg_box.hide()
        self.view3d.set_result(self.result)
        if self.views.currentIndex() == 1:
            self.flat.set_result(self.result)
        s = summary(self.result)
        ext, inn = s["externa"], s["interna"]
        litros = f"{s['volume_l']:.2f}".replace(".", ",")
        self.info.setText(
            f"Externa <b>{_fmt(ext[0])} × {_fmt(ext[1])} × {_fmt(ext[2])} mm</b>  ·  "
            f"interna {_fmt(inn[0])} × {_fmt(inn[1])} × {_fmt(inn[2])} mm  ·  {litros} L  ·  "
            f"{s['pecas']} peças por caixa  ·  {_fmt(s['area_cm2'] * p.quantity)} cm² de {p.material_name()}")
        self._update_buttons()

    def _update_buttons(self):
        ok = self.result is not None
        self.btn_send.setEnabled(ok)
        self.btn_save.setEnabled(ok)

    def _view_mode(self, i: int):
        self.views.setCurrentIndex(i)
        self.explode.setVisible(i == 0)
        self.explode_lbl.setVisible(i == 0)
        if i == 1:
            self.flat.set_result(self.result)

    def _fit(self):
        if self.views.currentIndex() == 0:
            self.view3d.reset_view()
        else:
            self.flat.fit()

    def _message(self, text: str, kind: str = "info"):
        self.msg_box.setProperty("kind", kind)
        self.msg_box.style().unpolish(self.msg_box)
        self.msg_box.style().polish(self.msg_box)
        self.msg.setText(text)
        self.msg_box.show()

    def show_message(self, html: str, kind: str = "ok"):
        self.msg.setTextFormat(Qt.RichText)
        self._message(html, kind)

    # ---------------- integração com a janela
    def set_compact(self, on: bool):
        self._controls.setFixedWidth(290 if on else 340)

    def refresh_theme(self):
        for sec in self.findChildren(_Section):
            sec.refresh_icon()
        self.view3d.update()
        if self.views.currentIndex() == 1:
            self.flat.set_result(self.result)
