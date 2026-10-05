"""Aba "Corte": checklist das placas (marcar ao cortar) e das solicitações (marcar ao entregar)."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QScrollArea,
                               QSizePolicy, QToolButton, QVBoxLayout, QWidget)

from . import theme


def _dot(color: QColor, text: str = "") -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("MatChip")
    lab.setStyleSheet(f"background: {color.name()};")
    lab.setToolTip(text)
    lab.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    return lab


class CutPanel(QWidget):
    sheetToggled = Signal(int, bool)        # índice interno da placa, cortada?
    deliveredToggled = Signal(str, bool)    # nº da solicitação, entregue?
    sheetClicked = Signal(int)              # mostrar a placa no canvas
    resetRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)
        head = QHBoxLayout()
        t = QLabel("Checklist de corte")
        t.setObjectName("SectionHead")
        head.addWidget(t)
        head.addStretch(1)
        self.btn_reset = QPushButton("Desmarcar")
        self.btn_reset.setToolTip("Desmarca todas as placas e solicitações")
        self.btn_reset.clicked.connect(self.resetRequested.emit)
        head.addWidget(self.btn_reset)
        lay.addLayout(head)
        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        self.progress.setFormat("%v de %m placas cortadas")
        self.progress.setMaximumHeight(18)
        lay.addWidget(self.progress)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.body = QWidget()
        self.bl = QVBoxLayout(self.body)
        self.bl.setContentsMargins(0, 0, 0, 0)
        self.bl.setSpacing(6)
        self.scroll.setWidget(self.body)
        lay.addWidget(self.scroll, 1)
        self.sheet_boxes: dict[int, QCheckBox] = {}
        self.owner_boxes: dict[str, QCheckBox] = {}
        self.set_data([], [], set(), set())

    def _clear(self):
        while self.bl.count():
            it = self.bl.takeAt(0)
            w = it.widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()

    def set_data(self, sheets: list[dict], owners: list[dict], cut: set, delivered: set):
        """sheets: [{si, n, material, count, tags:[(tag, QColor)]}]
        owners: [{tag, color, title, who, count, sheets:[(si, n)]}]"""
        scroll = self.scroll.verticalScrollBar().value()
        self._clear()
        self.sheet_boxes, self.owner_boxes = {}, {}
        done = sum(1 for s in sheets if s["si"] in cut)
        self.progress.setMaximum(max(1, len(sheets)))
        self.progress.setValue(done if sheets else 0)
        self.progress.setVisible(bool(sheets))
        self.btn_reset.setEnabled(bool(cut or delivered))
        if not sheets:
            e = QLabel("Faça o encaixe para ver aqui a lista das placas para ir marcando conforme corta.")
            e.setObjectName("Muted")
            e.setWordWrap(True)
            self.bl.addWidget(e)
            self.bl.addStretch(1)
            return
        h = QLabel("Placas")
        h.setObjectName("Muted")
        self.bl.addWidget(h)
        for s in sheets:
            fr = QFrame()
            fr.setObjectName("PartRow")
            fl = QVBoxLayout(fr)
            fl.setContentsMargins(10, 6, 6, 6)
            fl.setSpacing(4)
            top = QHBoxLayout()
            cb = QCheckBox(f"Placa {s['n']}")
            cb.setStyleSheet("font-weight: 700;")
            cb.setChecked(s["si"] in cut)
            cb.setToolTip("Marque quando esta placa já tiver sido cortada")
            cb.toggled.connect(lambda on, si=s["si"]: self.sheetToggled.emit(si, on))
            self.sheet_boxes[s["si"]] = cb
            top.addWidget(cb)
            if s.get("material"):
                top.addWidget(_dot(theme.material_color(s["material"]), s["material"]))
            top.addStretch(1)
            cnt = QLabel(f"{s['count']} peças")
            cnt.setObjectName("Muted")
            top.addWidget(cnt)
            go = QToolButton()
            go.setText("Ver")
            go.setToolTip("Mostrar esta placa")
            go.clicked.connect(lambda _=False, si=s["si"]: self.sheetClicked.emit(si))
            top.addWidget(go)
            fl.addLayout(top)
            if s.get("tags"):
                row = QHBoxLayout()
                row.setSpacing(4)
                row.addSpacing(24)
                for tag, col in s["tags"]:
                    row.addWidget(_dot(col, tag))
                row.addStretch(1)
                fl.addLayout(row)
            if s["si"] in cut:
                fr.setStyleSheet("QFrame#PartRow { border-left: 4px solid #16a34a; }")
            self.bl.addWidget(fr)
        if owners:
            h = QLabel("Solicitações — separe as peças pela cor")
            h.setObjectName("Muted")
            h.setContentsMargins(0, 8, 0, 0)
            self.bl.addWidget(h)
        for o in owners:
            fr = QFrame()
            fr.setObjectName("PartRow")
            fr.setStyleSheet(f"QFrame#PartRow {{ border-left: 6px solid {o['color'].name()}; }}")
            fl = QVBoxLayout(fr)
            fl.setContentsMargins(10, 6, 8, 6)
            fl.setSpacing(2)
            top = QHBoxLayout()
            t = QLabel(f"<b>{o['title']}</b>" + (f"  ·  {o['who']}" if o.get("who") else ""))
            t.setWordWrap(True)
            top.addWidget(t, 1)
            cb = QCheckBox("Entregue")
            cb.setChecked(o["tag"] in delivered)
            cb.toggled.connect(lambda on, tag=o["tag"]: self.deliveredToggled.emit(tag, on))
            self.owner_boxes[o["tag"]] = cb
            top.addWidget(cb)
            fl.addLayout(top)
            missing = [n for si, n in o["sheets"] if si not in cut]
            if not o["sheets"]:
                st = "sem peças nas placas"
            elif missing:
                st = f"{o['count']} peça(s) · placa(s) {', '.join(str(n) for _, n in o['sheets'])} · " \
                     f"falta cortar: {', '.join(str(n) for n in missing)}"
            else:
                st = f"{o['count']} peça(s) · ✓ tudo cortado — pode separar"
            sl = QLabel(st)
            sl.setObjectName("Muted")
            sl.setWordWrap(True)
            if o["sheets"] and not missing:
                sl.setStyleSheet("color: #16a34a; font-weight: 600;")
            fl.addWidget(sl)
            self.bl.addWidget(fr)
        self.bl.addStretch(1)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(scroll))
