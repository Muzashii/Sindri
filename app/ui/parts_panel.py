"""Painel lateral de peças: cartões com miniatura, medidas, quantidade e trava de rotação."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QPushButton, QSpinBox, QToolButton, QVBoxLayout,
                               QWidget)

from ..core.models import Part
from . import theme
from .icons import icon
from .render import thumbnail


def _fmt(v: float) -> str:
    s = f"{v:.1f}".rstrip("0").rstrip(".")
    return s.replace(".", ",")


class PartRow(QFrame):
    def __init__(self, part: Part, warns: list[str], dark: bool, panel: "PartsPanel"):
        super().__init__()
        self.setObjectName("PartRow")
        self.part = part
        t = theme.tokens()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(10)

        th = QLabel()
        th.setObjectName("Thumb")
        th.setFixedSize(56, 56)
        th.setAlignment(Qt.AlignCenter)
        th.setPixmap(thumbnail(part, 50, dark))
        lay.addWidget(th)

        info = QVBoxLayout()
        info.setSpacing(2)
        top = QHBoxLayout()
        top.setSpacing(6)
        name = QLabel(part.name.split(" (")[0])
        name.setObjectName("PartName")
        top.addWidget(name)
        if part.material:
            chip = QLabel(part.material)
            chip.setObjectName("MatChip")
            chip.setStyleSheet(f"background: {theme.material_color(part.material).name()};")
            top.addWidget(chip)
        if warns:
            b = QLabel("⚠ aviso")
            b.setObjectName("Badge")
            b.setToolTip("\n".join(warns))
            top.addWidget(b)
        top.addStretch(1)
        info.addLayout(top)
        w, h = part.size
        sub = QLabel(f"{_fmt(w)} × {_fmt(h)} mm")
        sub.setObjectName("PartSub")
        info.addWidget(sub)
        extra = [f"{part.file_quantity}× no arquivo"]
        if part.holes:
            extra.append(f"{len(part.holes)} furo(s)")
        sub2 = QLabel(" · ".join(extra))
        sub2.setObjectName("PartSub")
        info.addWidget(sub2)
        lay.addLayout(info, 1)

        ctl = QVBoxLayout()
        ctl.setSpacing(4)
        self.spin = QSpinBox()
        self.spin.setRange(0, 9999)
        self.spin.setValue(part.quantity)
        self.spin.setFixedWidth(70)
        self.spin.setAlignment(Qt.AlignCenter)
        self.spin.setToolTip("Quantidade a cortar")
        self.spin.valueChanged.connect(lambda v: panel.quantityChanged.emit(part.id, v))
        ctl.addWidget(self.spin)
        self.lock = QToolButton()
        self.lock.setCheckable(True)
        self.lock.setChecked(part.rotation_locked)
        self.lock.setToolTip("Travar rotação: a peça nunca será girada\n(útil para veio da madeira ou gravações)")
        self._lock_icon()
        self.lock.toggled.connect(self._lock_toggled)
        self.lock.setText(" Girar" if not part.rotation_locked else " Fixa")
        self.lock.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.lock.setFixedWidth(70)
        self.panel = panel
        ctl.addWidget(self.lock)
        lay.addLayout(ctl)

        tips = [part.name, f"{_fmt(w)} × {_fmt(h)} mm · área {part.area / 100:.1f} cm²",
                f"Arquivo: {part.source_file.replace(chr(92), '/').split('/')[-1]}"]
        if warns:
            tips += [""] + ["⚠ " + x for x in warns]
            self.setProperty("warn", True)
        self.setToolTip("\n".join(tips))

    def _lock_icon(self):
        t = theme.tokens()
        on = self.lock.isChecked()
        self.lock.setIcon(icon("lock" if on else "rotate", t["accent"] if on else t["muted"], 14))

    def _lock_toggled(self, v: bool):
        self._lock_icon()
        self.lock.setText(" Fixa" if v else " Girar")
        self.panel.rotationLockChanged.emit(self.part.id, v)

    def set_selected(self, sel: bool):
        self.setProperty("selected", sel)
        self.style().unpolish(self)
        self.style().polish(self)


class PartsPanel(QWidget):
    quantityChanged = Signal(str, int)
    rotationLockChanged = Signal(str, bool)
    partSelected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 6, 12)
        lay.setSpacing(8)
        head = QHBoxLayout()
        title = QLabel("Peças")
        title.setObjectName("SectionTitle")
        head.addWidget(title)
        head.addStretch(1)
        self.btn_clear = QToolButton()
        self.btn_clear.setObjectName("Danger")
        self.btn_clear.setText(" Limpar")
        self.btn_clear.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.btn_clear.setToolTip("Remover todas as peças e o encaixe (Ctrl+Shift+Del)")
        head.addWidget(self.btn_clear)
        lay.addLayout(head)
        self.req_card = QFrame()
        self.req_card.setObjectName("RequestCard")
        rc = QVBoxLayout(self.req_card)
        rc.setContentsMargins(12, 10, 12, 10)
        rc.setSpacing(2)
        self.req_title = QLabel()
        self.req_title.setObjectName("ReqTitle")
        self.req_title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.req_line1 = QLabel()
        self.req_line1.setObjectName("ReqLine")
        self.req_line1.setWordWrap(True)
        self.req_line1.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.req_line2 = QLabel()
        self.req_line2.setObjectName("Muted")
        self.req_line2.setWordWrap(True)
        self.req_mats = QHBoxLayout()
        self.req_mats.setSpacing(4)
        rc.addWidget(self.req_title)
        rc.addWidget(self.req_line1)
        rc.addWidget(self.req_line2)
        rc.addLayout(self.req_mats)
        self.req_card.hide()
        lay.addWidget(self.req_card)
        self.summary = QLabel("Nenhum arquivo aberto")
        self.summary.setObjectName("Muted")
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)

        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.currentRowChanged.connect(self._sel)
        lay.addWidget(self.list, 1)

        self.empty = QLabel("Abra ou arraste um arquivo DXF\npara ver as peças aqui.")
        self.empty.setObjectName("Muted")
        self.empty.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.empty, 1)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.btn_kits = QPushButton("Kits…")
        self.btn_kits.setToolTip("Multiplica todas as quantidades (ex.: quero 3 kits completos)")
        self.btn_reset = QPushButton("Qtd. do arquivo")
        self.btn_reset.setToolTip("Volta as quantidades para as do arquivo DXF")
        row.addWidget(self.btn_kits)
        row.addWidget(self.btn_reset)
        lay.addLayout(row)
        self.parts: list[Part] = []
        self.rows: list[PartRow] = []
        self.dark = False
        self._update_empty()

    def set_request(self, info: dict | None):
        """Cartão com nº da solicitação, RM, aluno e projeto (arquivos vindos da intranet)."""
        while self.req_mats.count():
            w = self.req_mats.takeAt(0).widget()
            if w:
                w.deleteLater()
        if not info:
            self.req_card.hide()
            return
        if info.get("batch"):
            reqs = info.get("requests", [])
            self.req_title.setText(f"Lote · {len(reqs)} solicitações")
            self.req_line1.setText("\n".join(f"{r.get('code', '')}  ·  RM {r.get('rm', '—')}  ·  {r.get('nome', '')}"
                                             for r in reqs))
            self.req_line2.hide()
            for m in info.get("materials", []):
                chip = QLabel(m)
                chip.setObjectName("MatChip")
                chip.setStyleSheet(f"background: {theme.material_color(m).name()};")
                self.req_mats.addWidget(chip)
            self.req_mats.addStretch(1)
            self.req_card.setToolTip("")
            self.req_card.show()
            return
        self.req_title.setText(f"Solicitação nº {info.get('code', '')}")
        self.req_line1.setText(f"RM {info.get('rm', '—')}  ·  {info.get('nome', '')}")
        extra = []
        for k, lab in (("projeto", "Projeto"), ("professor", "Prof."), ("turma", "Turma")):
            if info.get(k):
                extra.append(f"{lab}: {info[k]}")
        self.req_line2.setText("  ·  ".join(extra))
        self.req_line2.setVisible(bool(extra))
        for m in info.get("materials", []):
            chip = QLabel(m)
            chip.setObjectName("MatChip")
            chip.setStyleSheet(f"background: {theme.material_color(m).name()};")
            self.req_mats.addWidget(chip)
        self.req_mats.addStretch(1)
        tip = "\n".join(f"{k}: {v}" for k, v in (info.get("info") or {}).items())
        self.req_card.setToolTip(tip)
        self.req_card.show()

    def refresh_icons(self):
        t = theme.tokens()
        self.btn_kits.setIcon(icon("copy", t["text"], 15))
        self.btn_reset.setIcon(icon("reset", t["text"], 15))
        self.btn_clear.setIcon(icon("trash", t["danger"], 15, t["muted"]))

    def _update_empty(self):
        has = bool(self.parts)
        self.list.setVisible(has)
        self.empty.setVisible(not has)
        self.btn_kits.setEnabled(has)
        self.btn_reset.setEnabled(has)
        self.btn_clear.setEnabled(has)

    def _sel(self, row: int):
        for i, r in enumerate(self.rows):
            r.set_selected(i == row)
        if 0 <= row < len(self.parts):
            self.partSelected.emit(self.parts[row].id)

    def set_parts(self, parts: list[Part], too_big: set[str] = frozenset()):
        cur = self.list.currentRow()
        self.parts = list(parts)
        self.list.clear()
        self.rows = []
        from .owners import owner_colors
        cols = owner_colors(parts) if len({p.tag for p in parts if p.tag}) > 1 else {}
        for p in parts:
            warns = list(p.warnings)
            if p.id in too_big:
                warns.append("Maior que a placa em todas as rotações: não será encaixada. "
                             "Aumente a placa, reduza a margem ou confira a unidade do DXF.")
            row = PartRow(p, warns, self.dark, self)
            if p.tag in cols:
                row.setStyleSheet(f"QFrame#PartRow {{ border-left: 5px solid {cols[p.tag].name()}; }}")
            it = QListWidgetItem()
            it.setSizeHint(QSize(10, row.sizeHint().height() + 6))
            self.list.addItem(it)
            self.list.setItemWidget(it, row)
            self.rows.append(row)
        if 0 <= cur < len(self.rows):
            self.list.blockSignals(True)
            self.list.setCurrentRow(cur)
            self.list.blockSignals(False)
            self.rows[cur].set_selected(True)
        self._update_empty()
        self.update_summary(too_big=too_big)
        self.refresh_icons()

    def set_quantity(self, pid: str, q: int):
        for r in self.rows:
            if r.part.id == pid:
                r.spin.blockSignals(True)
                r.spin.setValue(q)
                r.spin.blockSignals(False)

    def update_summary(self, placed=None, too_big: set[str] | None = None):
        if not self.parts:
            self.summary.setText("Nenhum arquivo aberto")
            return
        total = sum(p.quantity for p in self.parts)
        txt = f"{len(self.parts)} tipo(s) · {total} peça(s) no total"
        nwarn = sum(1 for p in self.parts if p.warnings) + len(too_big or ())
        if nwarn:
            txt += f" · ⚠ {nwarn} com aviso"
        self.summary.setText(txt)

    def select_part(self, pid: str):
        for i, p in enumerate(self.parts):
            if p.id == pid:
                self.list.blockSignals(True)
                self.list.setCurrentRow(i)
                self.list.blockSignals(False)
                for j, r in enumerate(self.rows):
                    r.set_selected(i == j)
                self.list.scrollToItem(self.list.item(i))
                return
