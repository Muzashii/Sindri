"""Aba "Caixa": gera uma caixa com encaixe de dentes, mostra em 3D e manda as peças para o encaixe."""
from __future__ import annotations

import json
import math
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtCore import QSize
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QDoubleSpinBox, QFormLayout, QFrame, QGraphicsScene, QGraphicsSimpleTextItem,
                               QGraphicsView, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
                               QSizePolicy, QSlider, QSpinBox, QStackedWidget, QToolButton, QVBoxLayout, QWidget)
from shapely.geometry import box as rect
from shapely.geometry.polygon import orient

from ..core.boxgen import (BOARDS, HINGED, JOINT_FINGER, JOINT_FLAT, LID_CHEST, LID_CLOSED, LID_DOORS, LID_HELP,
                           LID_LIFT, LID_NAMES, LID_OPEN, LID_SLIDE, LID_TYPES, MODEL_BOX, MODEL_DRAWER, MODEL_ELEC,
                           MODEL_HELP, MODEL_KERF, MODEL_NAMES, MODEL_TRAY, MODELS, PRIORITY, PULLS, BoxParams,
                           BoxResult, flat_layout, generate, summary, validate)
from . import theme
from .dialogs import settings
from .settings_panel import _dspin


def _fmt(v: float) -> str:
    return f"{v:.1f}".rstrip("0").rstrip(".").replace(".", ",")


def _move(q: tuple, motion: tuple, f: float) -> tuple:
    """Posição de um ponto da tampa aberta numa fração ``f`` (0..1) do movimento."""
    if motion[0] == "move":
        d = motion[1]
        return (q[0] + d[0] * f, q[1] + d[1] * f, q[2] + d[2] * f)
    _, pivot, k, ang = motion                  # gira: fórmula de Rodrigues em volta do eixo k
    th = math.radians(ang * f)
    c, s = math.cos(th), math.sin(th)
    v = (q[0] - pivot[0], q[1] - pivot[1], q[2] - pivot[2])
    kv = k[0] * v[0] + k[1] * v[1] + k[2] * v[2]
    cr = (k[1] * v[2] - k[2] * v[1], k[2] * v[0] - k[0] * v[2], k[0] * v[1] - k[1] * v[0])
    return tuple(pivot[i] + v[i] * c + cr[i] * s + k[i] * kv * (1 - c) for i in range(3))


def render_icon(params: BoxParams, w: int, h: int, open_f: float = 0.0, yaw: float = -35.0,
                pitch: float = 28.0) -> QPixmap:
    """Desenho 3D pequeno de uma caixa (para os botões ilustrados)."""
    v = Box3DView(compact=True)
    v.setAttribute(Qt.WA_TranslucentBackground)
    v.setMinimumSize(10, 10)
    v.resize(w, h)
    v.yaw, v.pitch = yaw, pitch
    v.open = open_f
    try:
        v.set_result(generate(params))
    except ValueError:                         # medidas inválidas: ícone vazio (nunca lixo de memória)
        empty = QPixmap(w, h)
        empty.fill(Qt.transparent)
        return empty
    pm = QPixmap(w * 2, h * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.transparent)
    v.render(pm)
    v.deleteLater()
    return pm


# ------------------------------------------------------------------------------------------ vista 3D
class Box3DView(QWidget):
    """Caixa em 3D desenhada com QPainter (projeção ortográfica, faces ordenadas por profundidade).

    Arrastar gira, a roda dá zoom, duplo clique volta à vista inicial."""

    def __init__(self, parent=None, compact: bool = False):
        super().__init__(parent)
        self.result: Optional[BoxResult] = None
        self.explode = 0.0                     # 0 = montada, 1 = bem separada
        self.open = 0.0                        # 0 = tampa fechada, 1 = aberta (gira no pino / desliza / sobe)
        self.compact = compact                 # ícone: sem fundo nem dica de uso
        self.yaw, self.pitch, self.zoom = -35.0, 28.0, 1.0
        self._bounds = ((0, 0, 0), 1.0)        # centro e diagonal do que está desenhado
        self._drag: Optional[QPointF] = None
        self._faces: list = []                 # cache: faces em 3D (independe da câmera)
        self.setMinimumSize(200, 200)
        self.setMouseTracking(False)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleDescription("Setas giram a vista, mais e menos ajustam o zoom, F restaura a vista. Medidas e peças têm descrição textual.")
        self.setCursor(Qt.OpenHandCursor)

    # ---------------- dados
    def set_result(self, result: Optional[BoxResult]):
        self.result = result
        self._build_faces()
        self.update()

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key_Left, Qt.Key_Right):
            self.yaw += -5 if key == Qt.Key_Left else 5
        elif key in (Qt.Key_Up, Qt.Key_Down):
            self.pitch = max(-85, min(85, self.pitch + (5 if key == Qt.Key_Up else -5)))
        elif key in (Qt.Key_Plus, Qt.Key_Equal, Qt.Key_Minus):
            self.zoom = max(0.1, min(10, self.zoom * (1 / 1.1 if key == Qt.Key_Minus else 1.1)))
        elif key == Qt.Key_F:
            self.reset_view()
        else:
            return super().keyPressEvent(event)
        self.update()
        event.accept()

    def set_explode(self, f: float):
        self.explode = max(0.0, min(1.0, f))
        self._build_faces()
        self.update()

    def set_open(self, f: float):
        self.open = max(0.0, min(1.0, f))
        self._build_faces()
        self.update()

    def reset_view(self):
        self.yaw, self.zoom = -35.0, 1.0
        self.pitch = 50.0 if (self.result and self.result.params.model == "bandeja") else 28.0
        self.update()

    def _build_faces(self):
        """Lista de faces: (pontos 3D, normal 3D, tipo) — "face" = lado largo, "borda" = espessura."""
        self._faces = []
        self._items = []                       # centro 3D de cada pedaço (ordem de desenho)
        self._boxes = []                       # caixa delimitadora 3D do miolo de cada pedaço
        self._ghost = []                       # pedaço é só de mostruário (placa do Arduino)?
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
        pts = [q for rings, *_ in self._faces for ring in rings for q in ring]
        lo = [min(q[k] for q in pts) for k in range(3)]
        hi = [max(q[k] for q in pts) for k in range(3)]
        self._bounds = (tuple((lo[k] + hi[k]) / 2 for k in range(3)),
                        max(1.0, math.dist(lo, hi)))

    def _add_piece(self, pn, poly, ex, t, circles):
        motion = pn.motion if (pn.motion and self.open > 0) else None

        def P(u, v, s):
            q = pn.to3d(u, v, s)
            if motion is not None:             # abrir a tampa: gira em volta do pino ou desliza
                q = _move(q, motion, self.open)
            return (q[0] + ex[0], q[1] + ex[1], q[2] + ex[2])

        U, V, N = pn.U, pn.V, pn.N
        minx, miny, maxx, maxy = poly.bounds
        center = P((minx + maxx) / 2, (miny + maxy) / 2, t / 2)
        item = len(self._items)
        self._items.append(center)
        self._ghost.append(pn.ghost)
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
        if not self.compact:
            p.fillRect(self.rect(), QColor(tk["canvas"]))
        r = self.result
        if r is None or not self._faces:
            p.setPen(QColor(tk["muted"]))
            p.drawText(self.rect(), Qt.AlignCenter, "" if self.compact else "Ajuste as medidas para ver a caixa")
            p.end()
            return
        right, up, fwd = self._basis()
        (cx, cy, cz), diag = self._bounds
        dot = lambda a, b: a[0] * b[0] + a[1] * b[1] + a[2] * b[2]  # noqa: E731
        scale = min(self.width(), self.height()) / diag * (0.98 if self.compact else 0.92) * self.zoom
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
            if self._ghost[item]:              # placa eletrônica: verde de placa de circuito
                lum = 0.65 + 0.35 * max(0.0, dot(nrm, light))
                col = QColor.fromRgbF(0.10 * lum, 0.48 * lum, 0.28 * lum)
            elif kind == "face":
                lum = 0.70 + 0.30 * max(0.0, dot(nrm, light))
                col = QColor.fromRgbF(min(1, wood.redF() * lum), min(1, wood.greenF() * lum),
                                      min(1, wood.blueF() * lum))
            else:
                lum = 0.75 + 0.25 * max(0.0, dot(nrm, light))
                col = QColor.fromRgbF(burnt.redF() * lum, burnt.greenF() * lum, burnt.blueF() * lum)
            draw.append((rank[item], -depth, kind == "face", pts2, col))
        # peça por peça (ordem topológica); dentro da peça, as faces mais longe primeiro
        draw.sort(key=lambda x: (x[0], x[1]))
        edge = QPen(QColor(60, 35, 25, 150), 0.6 if self.compact else 0.8)
        edge.setCosmetic(True)
        for _, _, is_face, pts2, col in draw:
            path = QPainterPath()
            path.setFillRule(Qt.OddEvenFill)
            for ring in pts2:
                path.addPolygon(QPolygonF(ring + [ring[0]]))
            p.setBrush(QBrush(col))
            p.setPen(edge if is_face else QPen(col.darker(115), 0.6))
            p.drawPath(path)
        if not self.compact:
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
        engrave = QColor("#2563eb")
        for pn, g, circles, (dx, dy) in flat_layout(result):
            for u, v, text, hgt in pn.texts:           # textos gravados da peça (ex.: valores do kerf)
                it = QGraphicsSimpleTextItem(text)
                it.setBrush(engrave)
                fnt = QFont()
                fnt.setPointSizeF(max(1.5, hgt * 0.9))
                it.setFont(fnt)
                it.setTransform(it.transform().scale(1, -1))
                br = it.boundingRect()
                it.setPos(u + dx - br.width() / 2, v + dy + br.height() / 2)
                it.setZValue(2)
                sc.addItem(it)
            path = QPainterPath()
            path.setFillRule(Qt.OddEvenFill)
            for ring in [g.exterior] + list(g.interiors):
                path.addPolygon(QPolygonF([QPointF(x, y) for x, y in ring.coords]))
            for cx, cy, r in circles:
                path.addEllipse(QPointF(cx, cy), r, r)
            sc.addPath(path, pen, fill)
            c = g.representative_point()
            if pn.texts:
                continue                               # peça com textos próprios: sem o nome por cima
            txt = QGraphicsSimpleTextItem(pn.name)
            txt.setBrush(QColor(tk["muted"]))
            f = QFont()
            f.setPointSizeF(max(2.5, min(5.0, (g.bounds[3] - g.bounds[1]) / 7)))
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
# Configuração no estilo do MakerCase: uma coluna de passos numerados, cada escolha visual vira um
# botão ilustrado (desenhado pelo próprio gerador), e as opções que só valem para uma escolha
# aparecem logo abaixo dela. A prévia 3D ocupa o resto da tela.

MATERIALS = [("MDF 3 mm", 3.0, "MDF 3mm"), ("MDF 6 mm", 6.0, "MDF 6mm"), ("Acrílico 3 mm", 3.0, "Acrílico 3mm")]
ICON_W, ICON_H = 84, 58

# passos que cada modelo mostra (o resto fica escondido: menos coisa para entender)
MODEL_STEPS = {
    MODEL_BOX: ["medidas", "material", "tampa", "arestas", "divisorias", "extras", "quantidade"],
    MODEL_DRAWER: ["medidas", "material", "gaveta", "arestas", "divisorias", "quantidade"],
    MODEL_ELEC: ["medidas", "material", "placa", "arestas", "quantidade"],
    MODEL_TRAY: ["medidas", "material", "arestas", "divisorias", "extras", "quantidade"],
    MODEL_KERF: ["material", "kerf", "quantidade"],
}
MEASURE_HINT = {
    MODEL_BOX: "Largura × profundidade × altura, em milímetros.",
    MODEL_DRAWER: "Medidas do móvel por fora (internas = espaço útil da gaveta).",
    MODEL_ELEC: "Medidas da caixa por fora, com a tampa (internas = espaço útil).",
    MODEL_TRAY: "Largura × profundidade × altura da bandeja, em milímetros.",
}

# receitas: um clique e a caixa já sai certa para um uso comum
RECIPES = [
    ("Caixa para Arduino Uno", dict(model=MODEL_ELEC, width=100, depth=80, height=45, board="uno",
                                    cable_hole=8, vents=True)),
    ("Caixa para Raspberry Pi", dict(model=MODEL_ELEC, width=110, depth=80, height=45, board="rpi",
                                     cable_hole=8, vents=True)),
    ("Organizador de parafusos 4 × 3", dict(model=MODEL_TRAY, width=200, depth=150, height=40, cols=4, rows=3,
                                             ramp=True)),
    ("Porta-cartas (2 baralhos)", dict(model=MODEL_BOX, inner=True, width=133, depth=22, height=90,
                                       lid=LID_LIFT, cols=2, rows=1, finger_hole=0)),
    ("Caixote com alças (MDF 6 mm)", dict(model=MODEL_BOX, width=300, depth=200, height=150, thickness=6,
                                          finger=18, material="MDF 6mm", handles=True)),
    ("Gaveta de mesa", dict(model=MODEL_DRAWER, width=200, depth=250, height=80, cols=2, rows=1)),
    ("Baú de lembranças", dict(model=MODEL_BOX, width=200, depth=120, height=90, lid=LID_CHEST,
                               lid_height=22)),
    ("Teste de kerf (MDF 3 mm)", dict(model=MODEL_KERF, thickness=3, material="MDF 3mm")),
]


class ValueSlider(QDoubleSpinBox):
    """Native numeric control: exact value, keyboard and accessible value interface."""
    def __init__(self, lo, hi, step=0.25, suffix=" mm", tip=""):
        super().__init__()
        self.setRange(lo, hi)
        self.setSingleStep(step)
        self.setDecimals(2)
        self.setSuffix(suffix)
        self.setToolTip(tip)
        self.setAccessibleName("Largura do dente")
        self.setKeyboardTracking(False)


class DividerEditor(QWidget):
    """Caixa vista de cima com as divisórias possíveis: clique numa para tirar ou pôr de volta.

    Divisórias feitas aparecem cheias; as tiradas, tracejadas. A frente da caixa fica embaixo."""
    toggled = Signal(str, int)          # ("col" | "row", nº da divisória)

    def __init__(self):
        super().__init__()
        self.W, self.D, self.cols, self.rows = 150.0, 120.0, 1, 1
        self.cols_off, self.rows_off = set(), set()
        self._hover = None
        self.setMinimumHeight(150)
        self.setMouseTracking(True)
        self.setToolTip("Clique numa divisória para tirar ou pôr de volta")

    def set_layout(self, W: float, D: float, cols: int, rows: int, cols_off, rows_off):
        self.W, self.D, self.cols, self.rows = max(1.0, W), max(1.0, D), cols, rows
        self.cols_off, self.rows_off = set(cols_off or []), set(rows_off or [])
        self.update()

    def _frame(self) -> QRectF:
        m = 10.0
        aw, ah = self.width() - 2 * m, self.height() - 2 * m - 14
        s = min(aw / self.W, ah / self.D)
        w, h = self.W * s, self.D * s
        return QRectF((self.width() - w) / 2, m, w, h)

    def _lines(self):
        """[(tipo, nº, p1, p2)] de todas as divisórias possíveis, em coordenadas da tela."""
        r = self._frame()
        out = []
        for i in range(1, self.cols):
            x = r.left() + r.width() * i / self.cols
            out.append(("col", i, QPointF(x, r.top()), QPointF(x, r.bottom())))
        for j in range(1, self.rows):
            y = r.bottom() - r.height() * j / self.rows      # linha 1 = a mais perto da frente
            out.append(("row", j, QPointF(r.left(), y), QPointF(r.right(), y)))
        return out

    def _hit(self, pos: QPointF):
        best, bd = None, 9.0
        for kind, i, a, b in self._lines():
            d = abs(pos.x() - a.x()) if kind == "col" else abs(pos.y() - a.y())
            inside = (a.y() <= pos.y() <= b.y()) if kind == "col" else (a.x() <= pos.x() <= b.x())
            if inside and d < bd:
                best, bd = (kind, i), d
        return best

    def paintEvent(self, _e):
        tk = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self._frame()
        p.setPen(QPen(QColor("#7a4127"), 3))
        p.setBrush(QColor(tk["surface2"]))
        p.drawRect(r)
        for kind, i, a, b in self._lines():
            off = i in (self.cols_off if kind == "col" else self.rows_off)
            hov = self._hover == (kind, i)
            if off:
                pen = QPen(QColor(tk["accent"] if hov else tk["muted"]), 2 if hov else 1.4, Qt.DashLine)
            else:
                pen = QPen(QColor(tk["danger"] if hov else "#7a4127"), 4 if hov else 3)
            pen.setCapStyle(Qt.FlatCap)
            p.setPen(pen)
            p.drawLine(a, b)
        p.setPen(QColor(tk["muted"]))
        f = QFont(self.font())
        f.setPointSizeF(max(6.5, f.pointSizeF() - 1))
        p.setFont(f)
        p.drawText(QRectF(r.left(), r.bottom() + 1, r.width(), 14), Qt.AlignCenter, "frente")
        if self.cols == 1 and self.rows == 1:
            p.drawText(r, Qt.AlignCenter, "Sem divisórias:\nuse + nas colunas ou linhas")
        p.end()

    def mouseMoveEvent(self, e):
        h = self._hit(e.position())
        if h != self._hover:
            self._hover = h
            self.setCursor(Qt.PointingHandCursor if h else Qt.ArrowCursor)
            self.update()

    def leaveEvent(self, _e):
        self._hover = None
        self.update()

    def mousePressEvent(self, e):
        h = self._hit(e.position())
        if h and e.button() == Qt.LeftButton:
            self.toggled.emit(*h)


class _Step(QFrame):
    """Cartão de um passo: número em destaque, título, explicação curta e o conteúdo."""

    def __init__(self, n: int, title: str, hint: str = ""):
        super().__init__()
        self.setObjectName("Card")
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 12, 14, 14)
        v.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(10)
        num = QLabel(str(n))
        self.num = num
        num.setObjectName("StepNum")
        num.setFixedSize(24, 24)
        num.setAlignment(Qt.AlignCenter)
        from .components import SectionToggle
        self.content = QWidget(self)
        content_layout = QVBoxLayout(self.content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(theme.metric("space_sm"))
        t = SectionToggle(title, self.content, expanded=title != "Encaixe das arestas")
        self.toggle = t
        head.addWidget(num)
        head.addWidget(t, 1)
        v.addLayout(head)
        self.hint = QLabel(hint)
        self.hint.setObjectName("StepHint")
        self.hint.setWordWrap(True)
        self.hint.setVisible(bool(hint))
        content_layout.addWidget(self.hint)
        self.body = QVBoxLayout()
        self.body.setSpacing(theme.metric("space_sm"))
        content_layout.addLayout(self.body)
        v.addWidget(self.content)


class _Tile(QToolButton):
    """Botão ilustrado (desenho em cima, nome embaixo), parte de um grupo de escolha única."""

    def __init__(self, text: str, tip: str = ""):
        super().__init__()
        self.setObjectName("Tile")
        self.setText(text)
        self.setToolTip(tip)
        self.setCheckable(True)
        self.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        self.setIconSize(QSize(ICON_W, ICON_H))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumSize(0, ICON_H + 30)        # a grade divide a largura da coluna


def _tiles(parent, items, cols: int = 3):
    """Grade de _Tile com escolha única. ``items``: [(chave, texto, dica)] → (layout, {chave: tile}, grupo)."""
    grid = QGridLayout()
    grid.setSpacing(6)
    group = QButtonGroup(parent)
    group.setExclusive(True)
    out = {}
    for i, (key, text, tip) in enumerate(items):
        b = _Tile(text, tip)
        b.setProperty("key", key)
        group.addButton(b, i)
        grid.addWidget(b, i // cols, i % cols)
        out[key] = b
    return grid, out, group


def _seg(parent, items):
    """Botões segmentados (escolha única em linha). ``items``: [(chave, texto)] → (layout, {chave: botão}, grupo)."""
    row = QHBoxLayout()
    row.setSpacing(0)
    group = QButtonGroup(parent)
    out = {}
    for i, (key, text) in enumerate(items):
        b = QPushButton(text)
        b.setObjectName("Seg")
        b.setCheckable(True)
        b.setProperty("pos", "first" if i == 0 else ("last" if i == len(items) - 1 else "mid"))
        group.addButton(b, i)
        row.addWidget(b, 1)
        out[key] = b
    return row, out, group


def _labeled(text: str, w: QWidget) -> QVBoxLayout:
    v = QVBoxLayout()
    v.setSpacing(3)
    lb = QLabel(text)
    lb.setBuddy(w)
    w.setAccessibleName(text)
    lb.setObjectName("FieldLabel")
    v.addWidget(lb)
    v.addWidget(w)
    return v


class BoxPanel(QWidget):
    """Passos numerados à esquerda (com as ações no rodapé); caixa em 3D / peças à direita."""
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
        self.refresh_icons()
        self.regenerate()

    # ================================================================== montagem
    def _build_controls(self) -> QWidget:
        col = QWidget()
        col.setFixedWidth(theme.metric("panel_box"))
        self._controls = col
        cv = QVBoxLayout(col)
        cv.setContentsMargins(12, 12, 6, 12)
        cv.setSpacing(8)
        head = QVBoxLayout()
        head.setSpacing(2)
        title_row = QHBoxLayout()
        title = QLabel("Gerador de caixas")
        title.setObjectName("PanelTitle")
        from PySide6.QtWidgets import QMenu
        self.btn_recipes = QPushButton("Receitas ▾")
        self.btn_recipes.setToolTip("Modelos prontos para usos comuns: um clique e a caixa já sai certa")
        menu = QMenu(self)
        for name, values in RECIPES:
            menu.addAction(name, lambda v=values: self.apply_recipe(v))
        self.btn_recipes.setMenu(menu)
        self.btn_recipes.setStyleSheet("QPushButton::menu-indicator { image: none; width: 0; }")
        title_row.addWidget(title)
        title_row.addStretch(1)
        title_row.addWidget(self.btn_recipes)
        sub = QLabel("Escolha o modelo e siga os passos; a prévia muda na hora.")
        sub.setObjectName("Muted")
        head.addLayout(title_row)
        head.addWidget(sub)
        cv.addLayout(head)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        scroll.setWidget(body)
        v = QVBoxLayout(body)
        v.setContentsMargins(0, 0, 6, 0)
        v.setSpacing(8)

        self.steps = {}
        # ---- 0. modelo
        s0 = _Step(1, "Modelo")
        grid, self.model_tiles, self.model_grp = _tiles(self, [(m, MODEL_NAMES[m], MODEL_HELP[m]) for m in MODELS])
        s0.body.addLayout(grid)
        s0.hint.setVisible(True)
        v.addWidget(s0)
        self.steps["modelo"] = s0

        # ---- 1. medidas
        s1 = _Step(1, "Medidas", "Largura × profundidade × altura, em milímetros.")
        self.steps["medidas"] = s1
        row = QHBoxLayout()
        row.setSpacing(6)
        self.w = _dspin(10, 3000, 5, 1, "", "Largura (X), em mm")
        self.d = _dspin(10, 3000, 5, 1, "", "Profundidade (Y), em mm")
        self.h = _dspin(10, 3000, 5, 1, "", "Altura (Z), em mm. Inclui a tampa que fica por cima.")
        for text, wdg in (("Largura", self.w), ("Profundidade", self.d), ("Altura", self.h)):
            row.addLayout(_labeled(text, wdg), 1)
        s1.body.addLayout(row)
        seg, self.measure_btns, self.measure_grp = _seg(self, [(False, "Medidas externas"), (True, "Medidas internas")])
        self.measure_btns[False].setToolTip("A caixa fica com exatamente estas medidas por fora")
        self.measure_btns[True].setToolTip("O espaço de dentro fica com estas medidas")
        s1.body.addLayout(seg)
        v.addWidget(s1)

        # ---- 2. material
        s2 = _Step(2, "Material", "A espessura define os dentes e o material vai para a placa certa no encaixe.")
        self.steps["material"] = s2
        chips = QHBoxLayout()
        chips.setSpacing(4)
        self.mat_grp = QButtonGroup(self)
        self.mat_btns = []
        for i, (text, thick, name) in enumerate(MATERIALS + [("Outro", None, None)]):
            b = QPushButton(text)
            b.setObjectName("Chip")
            b.setCheckable(True)
            b.setMinimumWidth(0)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.mat_grp.addButton(b, i)
            chips.addWidget(b)
            self.mat_btns.append(b)
        self.mat_grp.idClicked.connect(self._material_chip)
        s2.body.addLayout(chips)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.t = _dspin(0.5, 20, 0.5, 2, " mm", "Espessura da placa")
        self.material = QLineEdit()
        self.material.setToolTip("Nome do material no encaixe (peças de materiais diferentes nunca dividem placa)")
        row.addLayout(_labeled("Espessura", self.t), 2)
        row.addLayout(_labeled("Nome no encaixe", self.material), 3)
        s2.body.addLayout(row)
        v.addWidget(s2)

        # ---- gaveta (só no modelo Gaveta)
        sg = _Step(3, "Gaveta", "A gaveta corre dentro do móvel; a frente de acabamento cobre a boca.")
        seg, self.pull_btns, self.pull_grp = _seg(self, list(PULLS.items()))
        self.pull_btns["vazado"].setToolTip("Rasgo para os dedos atravessando a frente")
        self.pull_btns["furo"].setToolTip("Furo de 4 mm para parafusar um puxador")
        sg.body.addWidget(QLabel("Puxador"))
        sg.body.addLayout(seg)
        gf = QFormLayout()
        gf.setContentsMargins(0, 0, 0, 0)
        self.drawer_gap = _dspin(0, 3, 0.1, 1, " mm", "Folga entre a gaveta e o móvel (dos lados e em cima)")
        gf.addRow("Folga da gaveta", self.drawer_gap)
        sg.body.addLayout(gf)
        v.addWidget(sg)
        self.steps["gaveta"] = sg

        # ---- placa e furos (só no modelo Eletrônica)
        sp = _Step(3, "Placa e furos", "A tampa é presa com parafusos M3 e porca, que entram num rasgo das "
                                        "paredes.")
        chips = QGridLayout()
        chips.setSpacing(4)
        self.board_grp = QButtonGroup(self)
        self.board_btns = {}
        for i, (key, (name, *_)) in enumerate(BOARDS.items()):
            b = QPushButton(name)
            b.setObjectName("Chip")
            b.setCheckable(True)
            b.setToolTip("Sem furos de placa" if not key else f"Furos de fixação do {name} no fundo (centralizado)")
            self.board_grp.addButton(b, i)
            chips.addWidget(b, i // 2, i % 2)
            self.board_btns[key] = b
        sp.body.addWidget(QLabel("Placa"))
        sp.body.addLayout(chips)
        pf = QFormLayout()
        pf.setContentsMargins(0, 0, 0, 0)
        self.cable = _dspin(0, 60, 1, 0, " mm", "Furo redondo no fundo para o cabo (0 = sem furo)")
        self.screw = _dspin(8, 40, 1, 0, " mm", "Comprimento do parafuso M3 que prende a tampa")
        self.vents = QCheckBox("Rasgos de ventilação nas laterais")
        pf.addRow("Furo do cabo", self.cable)
        pf.addRow("Parafuso M3 de", self.screw)
        pf.addRow(self.vents)
        sp.body.addLayout(pf)
        v.addWidget(sp)
        self.steps["placa"] = sp

        # ---- 3. tampa
        s3 = _Step(3, "Tampa")
        self.steps["tampa"] = s3
        grid, self.lid_tiles, self.lid_grp = _tiles(self, [(k, LID_NAMES[k], LID_HELP[k]) for k in LID_TYPES])
        s3.body.addLayout(grid)
        self.lid_help = QLabel()
        self.lid_help.setObjectName("StepHint")
        self.lid_help.setWordWrap(True)
        s3.body.addWidget(self.lid_help)
        self.lid_opts = QWidget()
        lo = QFormLayout(self.lid_opts)
        lo.setContentsMargins(0, 0, 0, 0)
        self.lid_h = _dspin(8, 200, 1, 1, " mm", "Altura da parte que abre (a tampa); a dobradiça de MDF\n"
                                                 "fica na linha entre a tampa e o corpo")
        self.pin = _dspin(1, 12, 0.1, 1, " mm", "Diâmetro do furo do pino. Para parafuso M3: 3,2 mm")
        self.pivot = _dspin(0, 60, 1, 1, " mm", "Diâmetro do disco em volta do qual a tampa gira (como no MakerCase).\n"
                                                "Automático = 4 × a espessura. Maior = dobradiça mais forte.")
        self.pivot.setSpecialValueText("Automático")
        self.pivot_lbl = QLabel("Diâmetro do pivô")
        self.lid_gap = _dspin(0, 3, 0.1, 1, " mm", "Folga entre a tampa e a caixa")
        self.hole = _dspin(0, 80, 1, 0, " mm", "Diâmetro do furo para o dedo (0 = sem furo)")
        self.lid_h_lbl, self.pin_lbl = QLabel("Altura da tampa"), QLabel("Furo do pino")
        self.lid_gap_lbl, self.hole_lbl = QLabel("Folga"), QLabel("Furo para o dedo")
        lo.addRow(self.lid_h_lbl, self.lid_h)
        lo.addRow(self.pin_lbl, self.pin)
        lo.addRow(self.pivot_lbl, self.pivot)
        lo.addRow(self.lid_gap_lbl, self.lid_gap)
        lo.addRow(self.hole_lbl, self.hole)
        s3.body.addWidget(self.lid_opts)
        v.addWidget(s3)

        # ---- 4. juntas
        s4 = _Step(4, "Encaixe das arestas")
        self.steps["arestas"] = s4
        grid, self.joint_tiles, self.joint_grp = _tiles(self, [
            (JOINT_FINGER, "Dentes", "Encaixe de dentes (finger joint): firme, monta sem cola se o kerf estiver certo"),
            (JOINT_FLAT, "Lisa", "Arestas lisas, para colar (mais rápido de cortar)")], cols=2)
        s4.body.addLayout(grid)
        jf = QFormLayout()
        jf.setContentsMargins(0, 0, 0, 0)
        # chave de arrastar: de 2× a 4× a espessura (dentes menores quebram, maiores ficam frouxos)
        self.finger = ValueSlider(6.0, 12.0, 0.25, " mm",
                                  "Largura aproximada de cada dente: de 2× a 4× a espessura do material.\n"
                                  "O programa ajusta para caber um número ímpar de dentes em cada aresta.")
        self.kerf = _dspin(0, 1, 0.01, 2, " mm", "Quanto o laser queima de material (0 = sem compensação).\n"
                                                 "Típico: 0,10–0,20 mm em MDF 3 mm. Deixa o encaixe justo.")
        self.finger_lbl = QLabel("Largura do dente")
        s4.body.addWidget(self.finger_lbl)
        s4.body.addWidget(self.finger)
        jf.addRow("Kerf do laser", self.kerf)
        s4.body.addLayout(jf)
        v.addWidget(s4)

        # ---- 5. divisórias
        s5 = _Step(5, "Divisórias", "Escolha colunas e linhas e clique numa divisória para tirar ou pôr de volta.")
        self.steps["divisorias"] = s5
        self.custom_grid = QWidget()
        cg = QHBoxLayout(self.custom_grid)
        cg.setContentsMargins(0, 0, 0, 0)
        self.cols = QSpinBox()
        self.cols.setRange(1, 20)
        self.cols.setToolTip("Compartimentos ao longo da largura")
        self.rows = QSpinBox()
        self.rows.setRange(1, 20)
        self.rows.setToolTip("Compartimentos ao longo da profundidade")
        cg.addLayout(_labeled("Colunas", self.cols), 1)
        cg.addLayout(_labeled("Linhas", self.rows), 1)
        self.btn_all_div = QPushButton("Todas")
        self.btn_all_div.setToolTip("Põe de volta todas as divisórias tiradas")
        self.btn_all_div.clicked.connect(self._all_dividers)
        cg.addWidget(self.btn_all_div, 0, Qt.AlignBottom)
        s5.body.addWidget(self.custom_grid)
        self.div_editor = DividerEditor()
        self.div_editor.toggled.connect(self._toggle_divider)
        s5.body.addWidget(self.div_editor)
        self.div_checks = QWidget()
        self.div_checks_layout = QVBoxLayout(self.div_checks)
        self.div_checks_layout.setContentsMargins(0, 0, 0, 0)
        self._divider_checkboxes = {}
        s5.body.addWidget(self.div_checks)
        self.cols_off, self.rows_off = set(), set()
        v.addWidget(s5)

        # ---- extras (caixa e bandeja)
        se = _Step(6, "Extras")
        self.handles = QCheckBox("Alças vazadas nas laterais")
        self.handles.setToolTip("Rasgos para segurar a caixa pelas laterais")
        self.ramp = QCheckBox("Rampa na frente de cada compartimento")
        self.ramp.setToolTip("Facilita pegar parafusos e peças pequenas (cole cada rampa)")
        se.body.addWidget(self.handles)
        se.body.addWidget(self.ramp)
        v.addWidget(se)
        self.steps["extras"] = se

        # ---- teste de kerf (instruções)
        sk = _Step(3, "Como usar o teste",
                   "1. Corte o pente e a tira (sem compensação de kerf).\n"
                   "2. Encaixe a tira em cada rasgo, do mais largo (0,00) ao mais estreito.\n"
                   "3. O número embaixo do rasgo em que ela entra justa é o kerf: use esse valor no passo "
                   "\"Encaixe das arestas\" das próximas caixas.")
        v.addWidget(sk)
        self.steps["kerf"] = sk

        # ---- 6. quantidade
        s6 = _Step(6, "Quantidade")
        self.steps["quantidade"] = s6
        row = QHBoxLayout()
        self.qty = QSpinBox()
        self.qty.setRange(1, 200)
        self.qty.setSuffix(" caixa(s)")
        self.qty.setToolTip("Quantas caixas iguais mandar para o encaixe")
        self.engrave = QCheckBox("Gravar o nome nas peças")
        self.engrave.setToolTip("Escreve o nome (Frente/Fundo, Lateral…) numa camada azul de gravação")
        row.addWidget(self.qty)
        row.addWidget(self.engrave, 1)
        s6.body.addLayout(row)
        v.addWidget(s6)
        v.addStretch(1)
        cv.addWidget(scroll, 1)

        # ---- rodapé fixo: a ação principal sempre à vista
        foot = QFrame()
        foot.setObjectName("Card")
        fl = QVBoxLayout(foot)
        fl.setContentsMargins(12, 10, 12, 12)
        fl.setSpacing(6)
        self.foot_info = QLabel("")
        self.foot_info.setObjectName("Muted")
        self.foot_info.setWordWrap(True)
        self.btn_send = QPushButton("Enviar para o encaixe")
        self.btn_send.setObjectName("success")
        self.btn_send.setMinimumHeight(38)
        self.btn_send.setToolTip("Coloca as peças na aba Encaixe (junto com o que já estiver lá) e encaixa na placa")
        self.btn_send.clicked.connect(self.sendRequested.emit)
        self.btn_save = QPushButton("Salvar só o DXF…")
        self.btn_save.setToolTip("Grava o DXF com as peças da caixa, sem encaixar")
        self.btn_save.clicked.connect(self.saveRequested.emit)
        fl.addWidget(self.foot_info)
        fl.addWidget(self.btn_send)
        fl.addWidget(self.btn_save)
        cv.addWidget(foot)

        # ---- sinais
        for wdg in (self.w, self.d, self.h, self.t, self.finger, self.kerf, self.lid_gap, self.hole,
                    self.lid_h, self.pin, self.pivot, self.drawer_gap, self.cable, self.screw):
            wdg.valueChanged.connect(self._changed)
        for grp in (self.pull_grp, self.board_grp):
            grp.idClicked.connect(self._changed)
        for cb in (self.handles, self.ramp, self.vents):
            cb.toggled.connect(self._changed)
        self.model_grp.idClicked.connect(lambda *_: self._model_picked())
        for wdg in (self.cols, self.rows, self.qty):
            wdg.valueChanged.connect(self._changed)
        for grp in (self.measure_grp, self.lid_grp, self.joint_grp):
            grp.idClicked.connect(self._changed)
        self.cols.valueChanged.connect(lambda *_: self._grid_resized())
        self.rows.valueChanged.connect(lambda *_: self._grid_resized())
        self.t.valueChanged.connect(self._finger_range)
        self.lid_grp.idClicked.connect(lambda *_: self._lid_picked())
        self.engrave.toggled.connect(self._changed)
        self.t.valueChanged.connect(self._sync_material_chip)
        self.material.textChanged.connect(self._material_typed)
        return col

    def _build_view(self) -> QWidget:
        card = QFrame()
        card.setObjectName("Card")
        v = QVBoxLayout(card)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(6)
        head = QHBoxLayout()
        seg, self.view_btns, grp = _seg(self, [(0, "Caixa 3D"), (1, "Peças para cortar")])
        self.btn_3d, self.btn_flat = self.view_btns[0], self.view_btns[1]
        self.btn_3d.setChecked(True)
        grp.idClicked.connect(self._view_mode)
        segw = QWidget()
        segw.setLayout(seg)
        segw.setFixedWidth(260)
        head.addWidget(segw)
        head.addSpacing(14)
        self.open_lbl = QLabel("Abrir tampa")
        self.open_lbl.setObjectName("Muted")
        self.open_slider = QSlider(Qt.Horizontal)
        self.open_slider.setRange(0, 100)
        self.open_slider.setMaximumWidth(150)
        self.open_slider.setToolTip("Abre a tampa na prévia (gira no pino, desliza ou levanta)")
        self.open_slider.valueChanged.connect(lambda val: self.view3d.set_open(val / 100))
        self.explode_lbl = QLabel("Separar peças")
        self.explode_lbl.setObjectName("Muted")
        self.explode = QSlider(Qt.Horizontal)
        self.explode.setRange(0, 100)
        self.explode.setMaximumWidth(150)
        self.explode.setToolTip("Separa as peças para ver como a caixa se monta")
        self.explode.valueChanged.connect(lambda val: self.view3d.set_explode(val / 100))
        for wdg in (self.open_lbl, self.open_slider):
            head.addWidget(wdg)
        head.addSpacing(10)
        for wdg in (self.explode_lbl, self.explode):
            head.addWidget(wdg)
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
        self.info = QLabel("")
        self.info.setObjectName("Muted")
        self.info.setWordWrap(True)
        v.addWidget(self.info)
        outer = QWidget()
        ol = QVBoxLayout(outer)
        ol.setContentsMargins(6, 12, 12, 12)
        ol.addWidget(card)
        return outer

    # ================================================================== parâmetros
    def _checked_key(self, tiles: dict, default):
        for k, b in tiles.items():
            if b.isChecked():
                return k
        return default

    def lid(self) -> str:
        return self._checked_key(self.lid_tiles, LID_OPEN)

    def model(self) -> str:
        return self._checked_key(self.model_tiles, MODEL_BOX)

    def params(self) -> BoxParams:
        model = self.model()
        gap = self.drawer_gap.value() if model == MODEL_DRAWER else self.lid_gap.value()
        return BoxParams(model=model, handles=self.handles.isChecked(), ramp=self.ramp.isChecked(),
                         board=self._checked_key(self.board_btns, ""), cable_hole=self.cable.value(),
                         vents=self.vents.isChecked(), screw_len=self.screw.value(),
                         pull=self._checked_key(self.pull_btns, "vazado"),
                         width=self.w.value(), depth=self.d.value(), height=self.h.value(),
                         inner=bool(self.measure_btns[True].isChecked()), thickness=self.t.value(),
                         finger=self.finger.value(), kerf=self.kerf.value(), lid=self.lid(),
                         cols=self.cols.value(), rows=self.rows.value(), lid_clearance=gap,
                         cols_off=sorted(self.cols_off), rows_off=sorted(self.rows_off),
                         finger_hole=self.hole.value(), lid_height=self.lid_h.value(), pin=self.pin.value(),
                         pivot=self.pivot.value(),
                         joint=self._checked_key(self.joint_tiles, JOINT_FINGER),
                         engrave_names=self.engrave.isChecked(), quantity=self.qty.value(),
                         material=self.material.text().strip())

    def set_params(self, p: BoxParams):
        self.t.blockSignals(True)
        self.t.setValue(p.thickness)
        self.t.blockSignals(False)
        self._finger_range(emit=False)
        widgets = ((self.w, p.width), (self.d, p.depth), (self.h, p.height), (self.t, p.thickness),
                   (self.finger, p.finger), (self.kerf, p.kerf), (self.cols, p.cols), (self.rows, p.rows),
                   (self.lid_gap, p.lid_clearance), (self.hole, p.finger_hole), (self.qty, p.quantity),
                   (self.lid_h, p.lid_height), (self.pin, p.pin), (self.pivot, p.pivot), (self.drawer_gap, p.lid_clearance),
                   (self.cable, p.cable_hole), (self.screw, p.screw_len))
        for wdg, val in widgets:
            wdg.blockSignals(True)
            wdg.setValue(val)
            wdg.blockSignals(False)
        self.measure_btns[bool(p.inner)].setChecked(True)
        self.model_tiles.get(p.model, self.model_tiles[MODEL_BOX]).setChecked(True)
        self.pull_btns.get(p.pull, self.pull_btns["vazado"]).setChecked(True)
        self.board_btns.get(p.board, self.board_btns[""]).setChecked(True)
        for cb, val in ((self.handles, p.handles), (self.ramp, p.ramp), (self.vents, p.vents)):
            cb.blockSignals(True)
            cb.setChecked(bool(val))
            cb.blockSignals(False)
        self.lid_tiles.get(p.lid, self.lid_tiles[LID_OPEN]).setChecked(True)
        self.joint_tiles.get(p.joint, self.joint_tiles[JOINT_FINGER]).setChecked(True)
        self.cols_off = {i for i in (p.cols_off or []) if 0 < i < p.cols}
        self.rows_off = {j for j in (p.rows_off or []) if 0 < j < p.rows}
        self._sync_div_editor()
        self.engrave.blockSignals(True)
        self.engrave.setChecked(bool(p.engrave_names))
        self.engrave.blockSignals(False)
        self.material.blockSignals(True)
        self.material.setText(p.material)
        self.material.blockSignals(False)
        self._sync_material_chip()
        self._contextual()

    def _load_settings(self):
        try:
            p = BoxParams.from_json(json.loads(settings().value("box/params", "{}") or "{}"))
        except (ValueError, TypeError):
            p = BoxParams()
        self.set_params(p)

    def _save_settings(self):
        settings().setValue("box/params", json.dumps(self.params().to_json()))

    def apply_recipe(self, values: dict):
        """Receita: parte dos valores padrão e aplica os da receita (medidas, modelo, extras…)."""
        cur = self.params()
        keep = dict(kerf=cur.kerf, quantity=1)
        self.set_params(BoxParams(**{**keep, **values}))
        self._model_picked()
        self.regenerate()

    def _model_picked(self):
        m = self.model()
        self.open_slider.setValue(55 if m in (MODEL_DRAWER, MODEL_ELEC) else 0)
        # bandeja: olhar mais de cima para ver as rampas e os compartimentos
        self.view3d.pitch = 50.0 if m == MODEL_TRAY else 28.0
        self.view3d.update()
        if m == MODEL_BOX:
            self._lid_picked()
        self._changed()

    def _show_steps(self):
        m = self.model()
        visible = MODEL_STEPS.get(m, MODEL_STEPS[MODEL_BOX])
        self.steps["modelo"].hint.setText(MODEL_HELP.get(m, ""))
        n = 1
        self.steps["modelo"].num.setText("1")
        for key, step in self.steps.items():
            if key == "modelo":
                continue
            on = key in visible
            step.setVisible(on)
            if on:
                n += 1
                step.num.setText(str(n))
        self.steps["medidas"].hint.setText(MEASURE_HINT.get(m, ""))
        self.ramp.setVisible(m == MODEL_TRAY)
        self.handles.setEnabled(m == MODEL_TRAY or self.lid() in (LID_OPEN, LID_CLOSED, LID_LIFT))
        self.handles.setToolTip("Rasgos para segurar a caixa pelas laterais" if self.handles.isEnabled() else
                                "Não combina com esta tampa (a lateral tem dobradiça ou rasgo)")

    # ---------------- material
    def _material_chip(self, i: int):
        if i < len(MATERIALS):
            _, thick, name = MATERIALS[i]
            self.material.blockSignals(True)
            self.material.setText(name)
            self.material.blockSignals(False)
            self.t.setValue(thick)                  # dispara _changed
            self._changed()
        else:
            self.material.setFocus()
            self.material.selectAll()

    def _material_typed(self, *_):
        self._sync_material_chip()
        self._changed()

    def _sync_material_chip(self, *_):
        name = self.material.text().strip() or f"MDF {self.t.value():g}mm"
        self.material.setPlaceholderText(f"MDF {self.t.value():g}mm")
        idx = next((i for i, (_, thick, nm) in enumerate(MATERIALS)
                    if nm == name and abs(thick - self.t.value()) < 1e-6), len(MATERIALS))
        self.mat_btns[idx].setChecked(True)

    # ---------------- divisórias
    def _grid_resized(self):
        """Mudou o nº de colunas/linhas: as posições mudam, então todas as divisórias voltam."""
        self.cols_off = {i for i in self.cols_off if i < self.cols.value()}
        self.rows_off = {j for j in self.rows_off if j < self.rows.value()}
        self._sync_div_editor()

    def _toggle_divider(self, kind: str, i: int):
        off = self.cols_off if kind == "col" else self.rows_off
        off.symmetric_difference_update({i})
        self._sync_div_editor()
        self._changed()

    def _all_dividers(self):
        self.cols_off.clear()
        self.rows_off.clear()
        self._sync_div_editor()
        self._changed()

    def _sync_div_editor(self):
        p_w, p_d = self.w.value(), self.d.value()
        self.div_editor.setAccessibleDescription("Prévia das divisórias. Use as caixas de seleção abaixo para ativar cada divisória por teclado.")
        self.div_editor.set_layout(p_w, p_d, self.cols.value(), self.rows.value(), self.cols_off, self.rows_off)
        self.btn_all_div.setEnabled(bool(self.cols_off or self.rows_off))
        keys = [("col", i) for i in range(1, self.cols.value())] + [("row", i) for i in range(1, self.rows.value())]
        for key in list(self._divider_checkboxes):
            if key not in keys:
                cb = self._divider_checkboxes.pop(key)
                self.div_checks_layout.removeWidget(cb)
                cb.deleteLater()
        for kind, i in keys:
            key = (kind, i)
            if key not in self._divider_checkboxes:
                cb = QCheckBox(f"Divisória {'vertical' if kind == 'col' else 'horizontal'} {i}")
                cb.toggled.connect(lambda on, kind=kind, i=i: self._set_divider(kind, i, on))
                self._divider_checkboxes[key] = cb
                self.div_checks_layout.addWidget(cb)
            cb = self._divider_checkboxes[key]
            cb.blockSignals(True)
            cb.setChecked(i not in (self.cols_off if kind == "col" else self.rows_off))
            cb.blockSignals(False)

    def _set_divider(self, kind, i, on):
        off = self.cols_off if kind == "col" else self.rows_off
        (off.discard if on else off.add)(i)
        self._sync_div_editor()
        self._changed()

    def _finger_range(self, *_, emit: bool = True):
        """A largura do dente vai de 2× a 4× a espessura (acompanha o material)."""
        t = self.t.value()
        if not emit:
            self.finger.blockSignals(True)
        self.finger.setRange(2 * t, 4 * t)
        if not emit:
            self.finger.blockSignals(False)

    # ---------------- opções que dependem da escolha
    def _lid_picked(self):
        lid = self.lid()
        # mostra a tampa mexendo logo que é escolhida (fechada/aberta não têm movimento)
        self.open_slider.setValue(55 if lid in (LID_CHEST, LID_DOORS, LID_SLIDE, LID_LIFT) else 0)

    def _contextual(self):
        self._show_steps()
        lid = self.lid() if self.model() == MODEL_BOX else LID_OPEN
        self.lid_help.setText(LID_HELP.get(lid, ""))
        hinged, slide, lift = lid in HINGED, lid == LID_SLIDE, lid == LID_LIFT
        for wdg, on in ((self.lid_h, hinged), (self.lid_h_lbl, hinged), (self.pin, False), (self.pin_lbl, False),
                        (self.pivot, hinged), (self.pivot_lbl, hinged),
                        (self.lid_gap, hinged or slide or lift), (self.lid_gap_lbl, hinged or slide or lift),
                        (self.hole, slide or lift), (self.hole_lbl, slide or lift)):
            wdg.setVisible(on)
        self.lid_opts.setVisible(hinged or slide or lift)
        self.lid_gap_lbl.setText("Folga da guia" if lift else ("Folga do rasgo" if slide else "Folga"))
        self.lid_gap.setVisible(lift or slide)                 # dobradiça de MDF: folgas calculadas sozinhas
        self.lid_gap_lbl.setVisible(lift or slide)
        finger = self._checked_key(self.joint_tiles, JOINT_FINGER) == JOINT_FINGER
        self.finger.setVisible(finger)
        self.finger_lbl.setVisible(finger)
        movable = (lid != LID_OPEN and lid != LID_CLOSED) or self.model() in (MODEL_DRAWER, MODEL_ELEC)
        self.open_lbl.setText("Abrir gaveta" if self.model() == MODEL_DRAWER else "Abrir tampa")
        self.open_lbl.setVisible(movable and self.views.currentIndex() == 0)
        self.open_slider.setVisible(movable and self.views.currentIndex() == 0)

    def _changed(self, *_):
        self._sync_div_editor()
        self._contextual()
        self._timer.start()

    # ================================================================== geração e vistas
    def regenerate(self):
        p = self.params()
        errs = validate(p)
        if errs:
            self.result = None
            self._message(errs[0], "warn")
            self.view3d.set_result(None)
            self.flat.set_result(None)
            self.info.setText("")
            self.foot_info.setText("Corrija o aviso em amarelo para continuar.")
            self._update_buttons()
            return
        self.result = generate(p)
        self._save_settings()
        if self.result.warnings:
            self._message(" ".join(self.result.warnings), "info")
        else:
            self.msg_box.hide()
        self.view3d.setAccessibleName("Prévia 3D da caixa; medidas e peças descritas abaixo")
        self.view3d.set_result(self.result)
        if self.views.currentIndex() == 1:
            self.flat.set_result(self.result)
        s = summary(self.result)
        ext, inn = s["externa"], s["interna"]
        litros = f"{s['volume_l']:.2f}".replace(".", ",")
        if p.model == MODEL_KERF:
            self.info.setText(f"Pente <b>{_fmt(ext[0])} × {_fmt(ext[2])} mm</b> com {len(self.result.panels[0].texts)}"
                              " rasgos (kerf de 0,00 a 0,30 mm) + tira de teste")
        else:
            what = "gaveta: " if p.model == MODEL_DRAWER else ""
            self.info.setText(
                f"Externa <b>{_fmt(ext[0])} × {_fmt(ext[1])} × {_fmt(ext[2])} mm</b>  ·  "
                f"interna ({what}espaço útil) {_fmt(inn[0])} × {_fmt(inn[1])} × {_fmt(inn[2])} mm  ·  {litros} L")
        n = s["pecas"] * p.quantity
        kind = LID_NAMES[p.lid] if p.model == MODEL_BOX else MODEL_NAMES[p.model]
        self.foot_info.setText(f"{kind} · {n} peças · {_fmt(s['area_cm2'] * p.quantity)} cm² de "
                               f"{p.material_name()}")
        from .accessibility import announce
        announce(self.foot_info, self.foot_info.text())
        self._update_buttons()

    def _update_buttons(self):
        ok = self.result is not None
        self.btn_send.setEnabled(ok)
        self.btn_save.setEnabled(ok)

    def _view_mode(self, i: int):
        self.views.setCurrentIndex(i)
        self.view_btns[i].setChecked(True)
        self.explode.setVisible(i == 0)
        self.explode_lbl.setVisible(i == 0)
        self._contextual()
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

    # ================================================================== ícones e tema
    def refresh_icons(self):
        """Desenha os botões ilustrados com o próprio gerador (mesma geometria da caixa real)."""
        base = dict(width=100, depth=78, height=60, thickness=4, finger=13, lid_height=24, pin=3.2,
                    finger_hole=16, lid_clearance=0.8)
        for lid, tile in self.lid_tiles.items():
            opened = {LID_CHEST: 0.6, LID_DOORS: 0.55, LID_SLIDE: 0.45, LID_LIFT: 0.5}.get(lid, 0.0)
            prm = dict(base)
            if lid in HINGED:                       # a dobradiça precisa de um pouco mais de altura
                prm.update(height=70, lid_height=20)
            if lid == LID_DOORS:                    # e a porta dupla, de largura para as duas
                prm.update(width=150)
            tile.setIcon(QIcon(render_icon(BoxParams(lid=lid, **prm), ICON_W, ICON_H, opened)))
        model_icons = {
            MODEL_BOX: (BoxParams(lid=LID_CHEST, **{**base, "height": 70, "lid_height": 20}), 0.6),
            MODEL_DRAWER: (BoxParams(model=MODEL_DRAWER, width=100, depth=90, height=60, thickness=4, finger=13,
                                     lid_clearance=0.8), 0.6),
            MODEL_ELEC: (BoxParams(model=MODEL_ELEC, width=100, depth=80, height=45, thickness=4, finger=13,
                                   board="uno", vents=True), 0.5),
            MODEL_TRAY: (BoxParams(model=MODEL_TRAY, width=110, depth=90, height=30, thickness=3, finger=12,
                                   cols=3, rows=2, ramp=True), 0.0),
            MODEL_KERF: (BoxParams(model=MODEL_KERF, thickness=4), 0.0),
        }
        for m, tile in self.model_tiles.items():
            prm, opened = model_icons[m]
            pitch = 50 if m == MODEL_TRAY else (18 if m == MODEL_KERF else 28)
            tile.setIcon(QIcon(render_icon(prm, ICON_W, ICON_H, opened, pitch=pitch)))
        for joint, tile in self.joint_tiles.items():
            pm = render_icon(BoxParams(width=60, depth=50, height=40, thickness=6, finger=11, joint=joint,
                                       lid=LID_CLOSED), ICON_W, ICON_H, yaw=-40, pitch=24)
            tile.setIcon(QIcon(pm))

    def set_compact(self, on: bool):
        self._controls.setFixedWidth(theme.metric("panel_box_compact" if on else "panel_box"))

    def refresh_theme(self):
        self.refresh_icons()
        self.view3d.update()
        if self.views.currentIndex() == 1:
            self.flat.set_result(self.result)
