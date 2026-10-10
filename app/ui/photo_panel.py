"""Aba "Gravação de foto": a imagem vira linhas horizontais com potências diferentes."""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, fields, replace
from typing import Callable, Optional

import numpy as np
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame, QGraphicsItem, QGraphicsPixmapItem,
                               QGraphicsRectItem, QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QSlider, QSpinBox, QVBoxLayout, QWidget)

from ..core.photo import LEVEL_ACI, LEVEL_NAMES, MAX_LEVELS, PhotoParams, PhotoResult, estimate_minutes, trace
from . import theme
from .dialogs import settings
from .settings_panel import _dspin, _Section

IMAGE_FILTER = "Imagens (*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff *.webp)"
HEAVY_SEGMENTS = 60000              # acima disto o RDWorks demora muito para abrir o DXF
MAX_SOURCE_PX = 3000                 # fotos maiores são reduzidas ao abrir (o resultado não precisa mais)


def load_gray(path: str) -> np.ndarray:
    """Imagem -> tons de cinza 0..1 (1 = branco). Transparência vira branco."""
    img = QImage(path)
    if img.isNull():
        raise ValueError("não consegui abrir esta imagem")
    if max(img.width(), img.height()) > MAX_SOURCE_PX:
        img = img.scaled(MAX_SOURCE_PX, MAX_SOURCE_PX, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    flat = QImage(img.size(), QImage.Format_RGB32)
    flat.fill(Qt.white)
    p = QPainter(flat)
    p.drawImage(0, 0, img)
    p.end()
    g = flat.convertToFormat(QImage.Format_Grayscale8)
    w, h, bpl = g.width(), g.height(), g.bytesPerLine()
    arr = np.frombuffer(g.constBits(), dtype=np.uint8, count=bpl * h).reshape(h, bpl)[:, :w]
    return arr.astype(np.float32) / 255.0


def preview_image(result: PhotoResult, levels: int) -> QImage:
    """Como vai ficar: nível 0 (mais potência) bem escuro, o mais claro quase branco, madeira ao fundo."""
    n = max(1, min(MAX_LEVELS, levels))
    shades = [int(35 + (175 - 35) * k / max(1, n - 1)) for k in range(n)]
    lut = np.full(256, 255, dtype=np.uint8)
    for k, s in enumerate(shades):
        lut[k] = s
    lv = result.levels.astype(np.int16)
    idx = np.where(lv < 0, 255, lv).astype(np.uint8)
    gray = lut[idx]
    # tom de madeira clara para o fundo e marrom para a queima
    r = (gray.astype(np.float32) * 0.93 + 18).clip(0, 255)
    gch = (gray.astype(np.float32) * 0.80 + 20).clip(0, 255)
    b = (gray.astype(np.float32) * 0.58 + 22).clip(0, 255)
    rgb = np.dstack([r, gch, b]).astype(np.uint8)
    rgb = np.ascontiguousarray(rgb)
    h, w = gray.shape
    img = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888)
    return img.copy()


def preview_gray(dark: np.ndarray) -> QImage:
    """Modo imagem: tons contínuos (queima proporcional) sobre madeira clara."""
    g = (255 - np.clip(dark, 0, 1) * 220).astype(np.float32)
    rgb = np.dstack([(g * 0.93 + 18).clip(0, 255), (g * 0.80 + 20).clip(0, 255),
                     (g * 0.58 + 22).clip(0, 255)]).astype(np.uint8)
    rgb = np.ascontiguousarray(rgb)
    h, w = g.shape
    return QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()


class _Bridge(QObject):
    done = Signal(int, object, object)          # geração, resultado, erro


class _PhotoItem(QGraphicsPixmapItem):
    """A foto na placa: pode ser arrastada (muda a posição X/Y)."""

    def __init__(self, on_move: Callable[[QPointF], None]):
        super().__init__()
        self._on_move = on_move
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemSendsGeometryChanges)
        self.setCursor(Qt.OpenHandCursor)
        self.setTransformationMode(Qt.SmoothTransformation)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            self._on_move(self.pos())
        return super().itemChange(change, value)


class PhotoView(QGraphicsView):
    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.NoDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setFrameShape(QFrame.NoFrame)

    def wheelEvent(self, e):
        f = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
        self.scale(f, f)

    def fit(self):
        r = self.scene().itemsBoundingRect().adjusted(-20, -20, 20, 20)
        if r.isValid():
            self.fitInView(r, Qt.KeepAspectRatio)


class PhotoPanel(QWidget):
    """Controles à esquerda, a placa com a foto à direita."""
    exportRequested = Signal()

    def __init__(self, plate: Callable[[], tuple], parent=None):
        super().__init__(parent)
        self._plate = plate                       # () -> (largura, altura) da placa atual em mm
        self.gray: Optional[np.ndarray] = None
        self.image_path = ""
        self.result: Optional[PhotoResult] = None
        self._result_params: Optional[PhotoParams] = None
        self._gen = 0
        self._busy = False
        self._trace_active = False
        self._trace_error = ""
        self._bridge = _Bridge()
        self._bridge.done.connect(self._traced)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(350)
        self._timer.timeout.connect(self.retrace)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._build_controls())
        lay.addWidget(self._build_view(), 1)
        self._load_settings()
        self._refresh_plate()
        self._update_info()

    # ------------------------------------------------------------------ montagem
    def _build_controls(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFixedWidth(theme.metric("panel_photo"))
        self._controls = scroll
        body = QWidget()
        from PySide6.QtWidgets import QSizePolicy
        body.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        scroll.setWidget(body)
        v = QVBoxLayout(body)
        v.setContentsMargins(12, 12, 6, 12)
        v.setSpacing(4)
        title = QLabel("Gravação de foto")
        title.setObjectName("SectionTitle")
        v.addWidget(title)
        scope = QLabel("Foto é um trabalho independente. Salvar projeto guarda apenas o encaixe; exporte a foto antes de fechar.")
        scope.setWordWrap(True)
        scope.setObjectName("Muted")
        v.addWidget(scope)

        g0 = _Section("Imagem", "open")
        f0 = QVBoxLayout(g0.body)
        f0.setContentsMargins(0, 0, 0, 0)
        self.btn_open = QPushButton("Abrir imagem…")
        self.btn_open.setObjectName("primary")
        self.btn_open.setToolTip("Foto ou desenho (PNG, JPG, BMP…). Você também pode arrastar para cá.")
        self.btn_open.clicked.connect(self.open_dialog)
        self.file_lbl = QLabel("Nenhuma imagem aberta.")
        self.file_lbl.setObjectName("Muted")
        self.file_lbl.setWordWrap(True)
        f0.addWidget(self.btn_open)
        f0.addWidget(self.file_lbl)
        v.addWidget(g0)

        g1 = _Section("Tamanho e posição", "grid")
        f1 = QFormLayout(g1.body)
        self.w = _dspin(5, 3000, 5, 1, " mm", "Largura da gravação; a altura acompanha a proporção da foto")
        self.h_lbl = QLabel("—")
        self.x = _dspin(-1000, 5000, 1, 1, " mm", "Distância da borda esquerda da placa (ou arraste a foto)")
        self.y = _dspin(-1000, 5000, 1, 1, " mm", "Distância da borda de baixo da placa (ou arraste a foto)")
        self.btn_center = QPushButton("Centralizar na placa")
        self.btn_center.clicked.connect(self.center)
        f1.addRow("Largura", self.w)
        f1.addRow("Altura", self.h_lbl)
        f1.addRow("X", self.x)
        f1.addRow("Y", self.y)
        f1.addRow(self.btn_center)
        v.addWidget(g1)

        g2 = _Section("Linhas e tons", "layers")
        f2 = QFormLayout(g2.body)
        self.mode = QComboBox()
        self.mode.addItem("Imagem BMP (recomendado)", "imagem")
        self.mode.addItem("Linhas DXF (experimental)", "linhas")
        self.mode.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.mode.setMinimumContentsLength(10)
        self.mode.setToolTip(
            "Imagem (recomendado): um BMP em tons de cinza; o RDWorks faz o scan linha a linha variando a\n"
            "potência entre a mínima (claro) e a máxima (escuro). Abre na hora, mesmo fotos grandes.\n"
            "Linhas (experimental): cada tom vira traços vetoriais. O RDWorks trata como corte: acelera e\n"
            "freia em cada traço (muito mais lento) e deixa marca no início/fim de cada um.")
        f2.addRow("Exportar como", self.mode)
        self.line = _dspin(0.05, 2, 0.05, 2, " mm",
                           "Distância entre as linhas. Foto em madeira com laser de CO2: 0,08–0,1 mm (ponto de\n"
                           "partida para teste). Menor = mais detalhe, arquivo maior e gravação mais demorada.")
        self.levels = QSpinBox()
        self.levels.setRange(1, MAX_LEVELS)
        self.levels.setToolTip("Quantas potências diferentes (cada uma vira uma camada/cor no RDWorks)")
        self.dither = QCheckBox("Pontilhado (mais tons)")
        self.dither.setToolTip("Mistura os níveis vizinhos para dar a impressão de mais tons de cinza")
        self.invert = QCheckBox("Inverter (negativo)")
        self.frame = _dspin(0, 50, 0.5, 1, " mm", "Moldura escura em volta da foto (0 = sem moldura)")
        f2.addRow("Espaço entre linhas", self.line)
        f2.addRow("Níveis de potência", self.levels)
        f2.addRow(self.dither)
        f2.addRow(self.invert)
        f2.addRow("Moldura", self.frame)
        v.addWidget(g2)

        g3 = _Section("Ajustes da imagem", "settings")
        f3 = QFormLayout(g3.body)
        self.bright = self._slider(-100, 100, "Mais claro (direita) ou mais escuro (esquerda)")
        self.contrast = self._slider(-100, 100, "Mais contraste deixa a foto mais marcada")
        self.gamma = _dspin(0.2, 3, 0.05, 2, "", "Gama: acima de 1 clareia os tons médios, abaixo escurece")
        self.cut = self._slider(0, 60, "Tons mais claros que isto não são gravados (fundo limpo)")
        f3.addRow("Brilho", self.bright)
        f3.addRow("Contraste", self.contrast)
        f3.addRow("Gama", self.gamma)
        f3.addRow("Ignorar claros", self.cut)
        v.addWidget(g3)

        g4 = _Section("Laser (RDWorks)", "zap")
        f4 = QFormLayout(g4.body)
        self.speed = _dspin(1, 2000, 10, 1, " mm/s", "Velocidade de todas as camadas da foto")
        self.pmax = _dspin(1, 100, 1, 1, " %", "Potência do tom mais escuro")
        self.pmin = _dspin(1, 100, 1, 1, " %", "Potência do tom mais claro gravado")
        f4.addRow("Velocidade", self.speed)
        self.pmax_label = QLabel("Potência (escuro)")
        f4.addRow(self.pmax_label, self.pmax)
        f4.addRow("Potência (claro)", self.pmin)
        self.levels_lbl = QLabel("")
        self.levels_lbl.setObjectName("Muted")
        self.levels_lbl.setWordWrap(True)
        f4.addRow(self.levels_lbl)
        v.addWidget(g4)
        v.addStretch(1)

        for form in body.findChildren(QFormLayout):
            form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
            form.setContentsMargins(0, 0, 0, 0)
            form.setHorizontalSpacing(10)
            form.setVerticalSpacing(8)
        trace_widgets = (self.w, self.line, self.gamma, self.frame)
        for wdg in trace_widgets:
            wdg.valueChanged.connect(self._changed)
        for wdg in (self.levels,):
            wdg.valueChanged.connect(self._changed)
        for wdg in (self.bright, self.contrast, self.cut):
            wdg.valueChanged.connect(self._changed)
        for c in (self.dither, self.invert):
            c.toggled.connect(self._changed)
        self.mode.currentIndexChanged.connect(self._changed)
        for wdg in (self.x, self.y):
            wdg.valueChanged.connect(self._moved_by_spin)
        for wdg in (self.speed, self.pmax, self.pmin):
            wdg.valueChanged.connect(self._laser_changed)
        return scroll

    @staticmethod
    def _slider(lo, hi, tip):
        s = QSlider(Qt.Horizontal)
        s.setRange(lo, hi)
        s.setToolTip(tip)
        return s

    def _build_view(self) -> QWidget:
        card = QFrame()
        card.setObjectName("Card")
        v = QVBoxLayout(card)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(6)
        head = QHBoxLayout()
        self.show_orig = QCheckBox("Ver a foto original")
        self.show_orig.toggled.connect(self._refresh_pixmap)
        btn_fit = QPushButton("Enquadrar")
        btn_fit.clicked.connect(lambda: self.view.fit())
        head.addWidget(self.show_orig)
        head.addStretch(1)
        head.addWidget(btn_fit)
        v.addLayout(head)
        self.msg_box = QFrame()
        self.msg_box.setObjectName("Banner")
        self.msg_box.setProperty("kind", "ok")
        mb = QHBoxLayout(self.msg_box)
        mb.setContentsMargins(10, 6, 6, 6)
        self.msg = QLabel()
        self.msg.setObjectName("BannerText")
        self.msg.setWordWrap(True)
        self.msg.setTextFormat(Qt.RichText)
        close = QPushButton("×")
        close.setFixedWidth(28)
        close.clicked.connect(self.msg_box.hide)
        mb.addWidget(self.msg, 1)
        mb.addWidget(close)
        self.msg_box.hide()
        v.addWidget(self.msg_box)
        self.view = PhotoView()
        self.view.setAccessibleName("Prévia da foto na placa; use X e Y para posicionar")
        v.addWidget(self.view, 1)
        bottom = QHBoxLayout()
        self.info = QLabel("")
        self.info.setObjectName("Muted")
        self.info.setWordWrap(True)
        self.btn_export = QPushButton("Exportar para RDWorks")
        self.btn_export.setObjectName("success")
        self.btn_export.setToolTip("Gera o DXF da foto, preenche velocidade/potência de cada nível e abre no RDWorks")
        self.btn_export.clicked.connect(self.exportRequested.emit)
        bottom.addWidget(self.info, 1)
        bottom.addWidget(self.btn_export)
        v.addLayout(bottom)
        outer = QWidget()
        ol = QVBoxLayout(outer)
        ol.setContentsMargins(6, 12, 12, 12)
        ol.addWidget(card)
        sc = self.view.scene()
        self.plate_item = QGraphicsRectItem()
        self.plate_item.setZValue(-1)
        sc.addItem(self.plate_item)
        self.photo_item = _PhotoItem(self._dragged)
        sc.addItem(self.photo_item)
        self.setAcceptDrops(True)
        return outer

    # ------------------------------------------------------------------ parâmetros
    def params(self) -> PhotoParams:
        return PhotoParams(width_mm=self.w.value(), line_mm=self.line.value(), levels=self.levels.value(),
                           power_min=min(self.pmin.value(), self.pmax.value()),
                           power_max=max(self.pmin.value(), self.pmax.value()), speed=self.speed.value(),
                           brightness=self.bright.value() / 100.0, contrast=self.contrast.value() / 100.0 * 0.9,
                           gamma=self.gamma.value(), invert=self.invert.isChecked(),
                           dither=self.dither.isChecked(), white_cut=self.cut.value() / 100.0,
                           frame_mm=self.frame.value(), x_mm=self.x.value(), y_mm=self.y.value(),
                           mode=self.mode.currentData() or "imagem")

    def _set_params(self, p: PhotoParams):
        for wdg, val in ((self.w, p.width_mm), (self.line, p.line_mm), (self.levels, p.levels),
                         (self.pmin, p.power_min), (self.pmax, p.power_max), (self.speed, p.speed),
                         (self.bright, int(round(p.brightness * 100))),
                         (self.contrast, int(round(p.contrast / 0.9 * 100))), (self.gamma, p.gamma),
                         (self.cut, int(round(p.white_cut * 100))), (self.frame, p.frame_mm),
                         (self.x, p.x_mm), (self.y, p.y_mm)):
            wdg.blockSignals(True)
            wdg.setValue(val)
            wdg.blockSignals(False)
        for c, val in ((self.invert, p.invert), (self.dither, p.dither)):
            c.blockSignals(True)
            c.setChecked(bool(val))
            c.blockSignals(False)
        self.mode.blockSignals(True)
        self.mode.setCurrentIndex(max(0, self.mode.findData(p.mode)))
        self.mode.blockSignals(False)
        self._mode_ui()

    def _load_settings(self):
        p = PhotoParams()
        try:
            saved = json.loads(settings().value("photo/params", "{}") or "{}")
            names = {f.name for f in fields(PhotoParams)}
            for k, val in saved.items():
                if k in names:
                    setattr(p, k, val)
        except (ValueError, TypeError):
            pass
        self._set_params(p)

    def _save_settings(self):
        settings().setValue("photo/params", json.dumps(asdict(self.params())))

    def laser_values(self, palette=None) -> dict:
        """{camada do RDWorks: (velocidade, potência)} de cada nível."""
        from ..core import laser
        p = self.export_params() or self.params()
        pal = palette or laser.DEFAULT_PALETTE
        if p.mode == "imagem":                       # bitmap na camada preta
            if p.dither:                             # pontilhado: um ponto é queimado ou não, potência única
                return {laser.nearest_layer((0, 0, 0), pal): (p.speed, p.power_max, p.power_max)}
            return {laser.nearest_layer((0, 0, 0), pal): (p.speed, p.power_min, p.power_max)}
        n = max(1, min(MAX_LEVELS, int(p.levels)))
        return {laser.nearest_layer(laser.aci_rgb(LEVEL_ACI[k]), pal): (p.speed, pw)
                for k, pw in enumerate(p.level_powers()[:n])}

    # ------------------------------------------------------------------ imagem
    def open_dialog(self):
        start = os.path.dirname(self.image_path) if self.image_path else os.path.expanduser("~")
        path, _ = QFileDialog.getOpenFileName(self, "Abrir imagem", start, IMAGE_FILTER)
        if path:
            self.open_image(path)

    def open_image(self, path: str) -> bool:
        try:
            gray = load_gray(path)
        except Exception as e:
            self.file_lbl.setText(f"Não consegui abrir: {e}")
            return False
        first = self.gray is None
        self.gray, self.image_path = gray, path
        h, w = gray.shape
        self.file_lbl.setText(f"{os.path.basename(path)} · {w} × {h} px")
        self._update_size()
        if first:
            self.center()
        self._timer.stop()
        self._invalidate_trace()
        self.retrace()
        return True

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            if u.isLocalFile() and self.open_image(u.toLocalFile()):
                break

    # ------------------------------------------------------------------ traçado (em segundo plano)
    def _mode_ui(self):
        lines = (self.mode.currentData() or "imagem") == "linhas"
        self.levels.setEnabled(lines)
        if lines:
            self.dither.setText("Pontilhado (mais tons)")
            self.dither.setToolTip("Mistura os níveis vizinhos para dar a impressão de mais tons de cinza")
        else:
            self.dither.setText("Pontilhado (recomendado)")
            self.dither.setToolTip(
                "Ligado: a imagem sai em pontos pretos e brancos e os tons vêm da quantidade de pontos —\n"
                "funciona em qualquer RDWorks e fica melhor em MDF/madeira (use 0,1–0,15 mm entre linhas).\n"
                "Desligado: tons de cinza, e o RDWorks precisa estar configurado para variar a potência\n"
                "(senão a foto sai só com 2 tons).")
        single = not lines and self.dither.isChecked()
        self.pmin.setEnabled(not single)
        if hasattr(self, "pmax_label"):
            self.pmax_label.setText("Potência" if single else "Potência (escuro)")
        for wdg in (self.x, self.y, self.btn_center):
            wdg.setToolTip(wdg.toolTip().split("\n(Imagem")[0] + ("" if lines else
                           "\n(Imagem: só para ver na placa — no RDWorks a imagem entra onde você colocar)"))

    def export_params(self):
        if self.result is None or self._busy or self._timer.isActive() or self._done_gen != self._gen:
            return None
        # Position and laser controls do not change pixels; merge them into the valid snapshot.
        current = self.params()
        return replace(self._result_params, x_mm=current.x_mm, y_mm=current.y_mm,
                       speed=current.speed, power_min=current.power_min, power_max=current.power_max)

    def _invalidate_trace(self):
        self._gen += 1
        self._busy = True
        self._trace_error = ""
        self.btn_export.setEnabled(False)
        self.info.setText("Gerando… aguarde a prévia atualizada para exportar.")

    def _changed(self, *_):
        self._mode_ui()
        self._save_settings()
        self._update_size()
        if self.gray is not None:
            self._invalidate_trace()
            self._timer.start()

    def retrace(self):
        if self.gray is None:
            return
        if self._trace_active:
            self._timer.start()
            return
        self._trace_active = True
        self._busy = True
        self.btn_export.setEnabled(False)
        gen, gray, p = self._gen, self.gray, self.params()

        def work():
            try:
                self._bridge.done.emit(gen, (trace(gray, p), p), None)
            except Exception as e:                 # pragma: no cover - mostrado na tela
                self._bridge.done.emit(gen, None, e)

        threading.Thread(target=work, daemon=True).start()

    def wait_idle(self, timeout: float = 30.0) -> bool:
        """Para testes: espera o traçado em andamento terminar."""
        import time
        from PySide6.QtWidgets import QApplication
        t = time.time()
        while time.time() - t < timeout:
            QApplication.processEvents()
            if self._timer.isActive():
                continue
            if self.result is not None and self._done_gen == self._gen:
                return True
            time.sleep(0.01)
        return False

    _done_gen = -1

    def _traced(self, gen, payload, err):
        self._trace_active = False
        if gen != self._gen:
            if not self._timer.isActive():
                self._timer.start()
            return
        self._busy = False
        self._timer.stop()
        self._done_gen = gen
        if err is not None or payload is None:
            self.result = self._result_params = None
            self._trace_error = f"Erro ao gerar: {err}. Altere um ajuste ou abra a imagem novamente para tentar."
            self.btn_export.setEnabled(False)
            self.info.setText(self._trace_error)
            return
        self.result, self._result_params = payload
        self._refresh_pixmap()
        self._update_info()

    # ------------------------------------------------------------------ desenho
    def plate_size(self) -> tuple[float, float]:
        try:
            w, h = self._plate()
            return float(w), float(h)
        except Exception:
            return 600.0, 400.0

    def _refresh_plate(self):
        t = theme.tokens()
        w, h = self.plate_size()
        self.plate_item.setRect(QRectF(0, 0, w, h))
        self.plate_item.setBrush(QBrush(QColor(t["sheet"])))
        pen = QPen(QColor(t["sheet_border"]))
        pen.setCosmetic(True)
        self.plate_item.setPen(pen)
        self.view.setBackgroundBrush(QColor(t["canvas"]))
        self._place_photo()
        self.view.fit()

    def _photo_height(self) -> float:
        if self.gray is None:
            return 0.0
        h, w = self.gray.shape
        return self.w.value() * h / w

    def _update_size(self):
        hh = self._photo_height()
        self.h_lbl.setText(f"{hh:.1f} mm" if hh else "—")
        self._refresh_pixmap()

    def _refresh_pixmap(self, *_):
        if self.gray is None:
            self.photo_item.setPixmap(QPixmap())
            return
        if self.show_orig.isChecked() or self.result is None:
            g = (np.clip(self.gray, 0, 1) * 255).astype(np.uint8)
            g = np.ascontiguousarray(g)
            hh, ww = g.shape
            img = QImage(g.data, ww, hh, ww, QImage.Format_Grayscale8).copy()
        elif self._result_params is not None and self._result_params.mode == "imagem" \
                and self.result.dark is not None:
            img = preview_gray(self.result.dark)
        else:
            img = preview_image(self.result, self._result_params.levels if self._result_params else 5)
        pm = QPixmap.fromImage(img)
        self.photo_item.setPixmap(pm)
        width_mm = self.w.value()
        self.photo_item.setScale(width_mm / max(1, pm.width()))
        self._place_photo()

    def _place_photo(self):
        """Converte X/Y (canto inferior esquerdo, Y para cima) para a cena (Y para baixo)."""
        _, ph = self.plate_size()
        top = ph - self.y.value() - self._photo_height()
        self.photo_item._on_move, keep = (lambda _p: None), self.photo_item._on_move
        self.photo_item.setPos(self.x.value(), top)
        self.photo_item._on_move = keep

    def _dragged(self, pos: QPointF):
        _, ph = self.plate_size()
        for wdg, val in ((self.x, pos.x()), (self.y, ph - pos.y() - self._photo_height())):
            wdg.blockSignals(True)
            wdg.setValue(round(val, 1))
            wdg.blockSignals(False)
        self._save_settings()
        self._update_info()

    def _moved_by_spin(self, *_):
        self._place_photo()
        self._save_settings()
        self._update_info()

    def center(self):
        w, h = self.plate_size()
        self.x.setValue(round(max(0.0, (w - self.w.value()) / 2), 1))
        self.y.setValue(round(max(0.0, (h - self._photo_height()) / 2), 1))

    def _laser_changed(self, *_):
        self._save_settings()
        self._update_info()

    def _update_info(self):
        p = self.params()
        n = max(1, min(MAX_LEVELS, int(p.levels)))
        pw = p.level_powers()
        if p.mode == "imagem" and p.dither:
            self.levels_lbl.setText(f"Camada preta do RDWorks: {p.power_max:g}% a {p.speed:g} mm/s, em modo "
                                    "varredura (scan). Os tons vêm da densidade dos pontos.")
        elif p.mode == "imagem":
            self.levels_lbl.setText(f"Camada preta do RDWorks: potência {p.power_min:g}% nos claros até "
                                    f"{p.power_max:g}% nos escuros. Deixe a camada em modo varredura (scan).")
        else:
            self.levels_lbl.setText("Camadas no RDWorks (do mais escuro ao mais claro): " + "; ".join(
                f"{LEVEL_NAMES[k]} {pw[k]:g}%" for k in range(n)))
        if self.gray is None:
            self.info.setText("Abra uma imagem para começar (botão à esquerda ou arraste o arquivo).")
            self.btn_export.setEnabled(False)
            return
        if self._busy or self._timer.isActive():
            self.btn_export.setEnabled(False)
            self.info.setText("Gerando… aguarde a prévia atualizada para exportar.")
            return
        if self.result is None:
            self.btn_export.setEnabled(False)
            self.info.setText(self._trace_error or "Altere um ajuste para gerar a prévia.")
            return
        w, h = self.plate_size()
        outside = (p.x_mm < -1e-6 or p.y_mm < -1e-6 or p.x_mm + self.result.width_mm > w + 1e-6
                   or p.y_mm + self.result.height_mm > h + 1e-6)
        rp = self._result_params or p
        rows, cols = self.result.levels.shape
        if rp.mode == "imagem":
            mins = (rows * (self.result.width_mm * 1.15 + 10)) / max(p.speed, 1) / 60.0
            txt = (f"{self.result.width_mm:.0f} × {self.result.height_mm:.0f} mm · imagem {cols} × {rows} px · "
                   f"≈ {mins:.0f} min a {p.speed:g} mm/s")
            ok = bool((self.result.dark is not None) and (self.result.dark > 0).any())
            if rp.dither and rp.line_mm > 0.18:
                txt += "  ·  dica: para pontilhado use 0,1–0,15 mm entre linhas (pontos mais finos)"
        else:
            mins = estimate_minutes(self.result, p)
            txt = (f"{self.result.width_mm:.0f} × {self.result.height_mm:.0f} mm · "
                   f"{rows} linhas · {self.result.count:,} traços · "
                   f"≈ {mins:.0f} min a {p.speed:g} mm/s").replace(",", ".")
            if self.result.count > HEAVY_SEGMENTS:
                txt += ("  ·  ⚠ arquivo pesado: o RDWorks vai demorar para abrir — use “Imagem (BMP)”, "
                        "desligue o pontilhado ou aumente o espaço entre linhas")
            ok = self.result.count > 0
        if outside:
            txt += "  ·  ⚠ a foto passa da borda da placa"
        self.info.setText(txt)
        from .accessibility import announce
        announce(self.info, txt)
        self.btn_export.setEnabled(ok)

    def set_compact(self, on: bool):
        """Telas pequenas: coluna de controles mais estreita."""
        self._controls.setFixedWidth(290 if on else theme.metric("panel_photo"))
        for form in self._controls.findChildren(QFormLayout):
            form.setRowWrapPolicy(QFormLayout.WrapAllRows if on else QFormLayout.WrapLongRows)

    def show_message(self, html: str):
        self.msg.setText(html)
        self.msg_box.show()

    def plate_changed(self):
        self._refresh_plate()
        self._update_info()

    def refresh_theme(self):
        self._refresh_plate()
