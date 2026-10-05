"""Painel lateral de peças: cartões com miniatura, medidas, quantidade e trava de rotação."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QPushButton, QSpinBox, QToolButton, QVBoxLayout,
                               QWidget)

from ..core.models import Part
from . import theme
from .icons import icon
from .render import thumbnail


def _fmt(v: float) -> str:
    s = f"{v:.1f}".rstrip("0").rstrip(".")
    return s.replace(".", ",")


class PartRow(QFrame):
    def __init__(self, part: Part, warns: list[str], dark: bool, panel: PartsPanel):
        super().__init__()
        self.setObjectName("PartRow")
        self.part = part
        theme.tokens()
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
        # linha 1: nome (cortado com "…" se não couber; o nome inteiro fica na dica)
        self.full_name = part.name.split(" (")[0]
        self.name = QLabel(self.full_name)
        self.name.setObjectName("PartName")
        self.name.setMinimumWidth(40)
        from PySide6.QtWidgets import QSizePolicy
        self.name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        info.addWidget(self.name)
        # linha 2: material + aviso
        top = QHBoxLayout()
        top.setSpacing(6)
        if part.material:
            chip = QLabel(part.material)
            chip.setObjectName("MatChip")
            chip.setStyleSheet(f"background: {theme.material_color(part.material).name()};")
            chip.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
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
        extra = [f"{part.file_quantity}× no arquivo"] + ([f"{len(part.holes)} furo(s)"] if part.holes else [])
        sub2 = QLabel(" · ".join(extra))
        sub2.setObjectName("PartSub")
        sub2.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
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
        self.done_btn = QToolButton()
        self.done_btn.setObjectName("DoneButton")
        self.done_btn.setCheckable(True)
        self.done_btn.setText(" Feito")
        self.done_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.done_btn.setFixedWidth(70)
        self.done_btn.setToolTip("Marque quando esta peça já tiver sido cortada")
        self.done_btn.toggled.connect(self._done_toggled)
        ctl.addWidget(self.done_btn)
        lay.addLayout(ctl)
        self._done_icon()

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

    def _done_icon(self):
        on = self.done_btn.isChecked()
        self.done_btn.setIcon(icon("check", "#ffffff" if on else theme.tokens()["muted"], 14))
        prog = getattr(self, "_progress", None)
        if on or not prog or prog[0] == 0:
            self.done_btn.setText(" Feito")
            self.done_btn.setToolTip("Marque quando esta peça já tiver sido cortada")
        else:
            self.done_btn.setText(f" {prog[0]}/{prog[1]}")
            self.done_btn.setToolTip(f"{prog[0]} de {prog[1]} cópias já estão em placas cortadas.\n"
                                     "Clique para marcar a peça inteira como feita.")

    def set_progress(self, cut: int, total: int):
        self._progress = (cut, total)
        self._done_icon()

    def _done_toggled(self, v: bool):
        self._apply_done(v)
        self.panel.doneChanged.emit(self.part.id, v)

    def _apply_done(self, v: bool):
        self._done_icon()
        self.setProperty("done", v)
        self.style().unpolish(self)
        self.style().polish(self)
        for w in self.findChildren(QLabel):
            w.style().unpolish(w)
            w.style().polish(w)

    def set_done(self, v: bool):
        if self.done_btn.isChecked() != v:
            self.done_btn.blockSignals(True)
            self.done_btn.setChecked(v)
            self.done_btn.blockSignals(False)
        self._apply_done(v)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        fm = self.name.fontMetrics()
        self.name.setText(fm.elidedText(self.full_name, Qt.ElideRight, max(30, self.name.width())))

    def set_selected(self, sel: bool):
        self.setProperty("selected", sel)
        self.style().unpolish(self)
        self.style().polish(self)


class PartsPanel(QWidget):
    quantityChanged = Signal(str, int)
    rotationLockChanged = Signal(str, bool)
    partSelected = Signal(str)
    doneChanged = Signal(str, bool)       # peça marcada como feita (cortada)
    requestFilter = Signal(str)           # mostrar só as peças de uma solicitação ("" = todas)
    sheetToggled = Signal(int, bool)      # placa marcada como cortada

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
        self.req_rows = QVBoxLayout()           # lote: uma linha clicável por solicitação
        self.req_rows.setSpacing(3)
        rc.addLayout(self.req_rows)
        self.filter_tag = ""
        rc.addWidget(self.req_line2)
        rc.addLayout(self.req_mats)
        self.req_card.hide()
        lay.addWidget(self.req_card)
        # placas cortadas (aparece depois do encaixe)
        self.sheets_box = QFrame()
        self.sheets_box.setObjectName("SheetsBox")
        sb = QVBoxLayout(self.sheets_box)
        sb.setContentsMargins(10, 8, 10, 8)
        sb.setSpacing(4)
        self.sheets_title = QLabel("Placas cortadas")
        self.sheets_title.setObjectName("SheetsTitle")
        sb.addWidget(self.sheets_title)
        self.sheets_grid = QGridLayout()
        self.sheets_grid.setHorizontalSpacing(10)
        self.sheets_grid.setVerticalSpacing(2)
        sb.addLayout(self.sheets_grid)
        self.sheets_box.hide()
        lay.addWidget(self.sheets_box)
        self.sheet_checks: dict[int, QCheckBox] = {}
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

    def set_sheets(self, sheets: list[dict], cut: set):
        """Caixinhas 'Placa 1 · MDF 3mm' para marcar as placas já cortadas."""
        while self.sheets_grid.count():
            w = self.sheets_grid.takeAt(0).widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self.sheet_checks = {}
        self.sheets_box.setVisible(bool(sheets))
        if not sheets:
            return
        done = sum(1 for s in sheets if s["si"] in cut)
        self.sheets_title.setText(f"Placas cortadas  ·  {done} de {len(sheets)}")
        cols = 2 if len(sheets) > 1 else 1
        for i, s in enumerate(sheets):
            mat = s.get("material") or ""
            txt = f"Placa {s['n']}" + (f" · {mat}" if mat else "")
            cb = QCheckBox()
            cb.setText(cb.fontMetrics().elidedText(txt, Qt.ElideRight, 128 if cols == 2 else 260))
            cb.setToolTip(f"Placa {s['n']}" + (f" · {mat}" if mat else "") + f" · {s['count']} peças\n"
                          "Marque quando terminar de cortar: as peças que só estão em placas cortadas "
                          "ficam como feitas.")
            cb.setChecked(s["si"] in cut)
            if s["si"] in cut:
                cb.setStyleSheet("color: #16a34a; font-weight: 700;")
            cb.toggled.connect(lambda on, si=s["si"]: self.sheetToggled.emit(si, on))
            self.sheet_checks[s["si"]] = cb
            self.sheets_grid.addWidget(cb, i // cols, i % cols)

    def set_filter(self, tag: str):
        """Mostra na lista só as peças da solicitação escolhida e avisa a janela (desenho)."""
        self.filter_tag = tag
        try:
            if getattr(self, "_all_btn", None) is not None:
                self._all_btn.setVisible(bool(tag))
        except RuntimeError:
            pass
        for code, b in getattr(self, "_req_buttons", {}).items():
            try:
                b.setChecked(code == tag)
            except RuntimeError:
                pass
        self._apply_filter()
        self.requestFilter.emit(tag)

    def _apply_filter(self):
        for i, r in enumerate(self.rows):
            it = self.list.item(i)
            if it is not None:
                it.setHidden(bool(self.filter_tag) and r.part.tag != self.filter_tag)

    def set_done(self, done: set, progress: dict | None = None):
        self._done = set(done)
        self._progress = dict(progress or {})
        for r in self.rows:
            r.set_progress(*self._progress.get(r.part.id, (0, 0)))
            r.set_done(r.part.id in done)
        n = sum(1 for p in self.parts if p.id in done)
        self._done_count = n
        self.update_summary()

    def set_request(self, info: dict | None, done_tags: set | None = None, progress: dict | None = None):
        """Cartão com nº da solicitação, RM, aluno e projeto (arquivos vindos da intranet)."""
        for lay in (self.req_mats, self.req_rows):
            while lay.count():
                w = lay.takeAt(0).widget()
                if w:
                    w.hide()
                    w.setParent(None)
                    w.deleteLater()
        if not info:
            self.req_card.hide()
            if self.filter_tag:
                self.set_filter("")
            return
        if info.get("batch"):
            from .owners import OWNER_COLORS
            reqs = info.get("requests", [])
            codes = sorted(str(r.get("code", "")) for r in reqs)
            cols = {c: OWNER_COLORS[i % len(OWNER_COLORS)] for i, c in enumerate(codes)}
            self.req_title.setText(f"Lote · {len(reqs)} solicitações")
            self.req_line1.setText("Clique numa solicitação para ver só as peças dela:")
            self.req_line1.setObjectName("Muted")
            dt = done_tags or set()
            self._req_buttons = {}
            for r in reqs:
                code = str(r.get("code", ""))
                n = sum(p.quantity for p in self.parts if p.tag == code)
                prog = (progress or {}).get(code)
                b = QPushButton()
                b.setObjectName("ReqRow")
                b.setCheckable(True)
                b.setChecked(code == self.filter_tag)
                b.setCursor(Qt.PointingHandCursor)
                done = "✓ " if code in dt else ""
                count = (f"{prog[0]}/{prog[1]}" if prog and prog[1] else (f"{n} pç" if n else ""))
                label = f"{done}{code} · {r.get('nome', '') or '—'}"
                fm = b.fontMetrics()
                tail = f"   {count}" if count else ""
                avail = max(120, self.req_card.width() - 40 - fm.horizontalAdvance(tail)) if self.req_card.width() > 100 \
                    else 210
                b.setText(fm.elidedText(label, Qt.ElideRight, avail) + tail)
                b.setToolTip(f"Solicitação {code} · RM {r.get('rm', '—')} · {r.get('nome', '')}\n"
                             "Clique para mostrar só as peças desta solicitação (clique de novo para ver todas)")
                b.setStyleSheet(f"QPushButton#ReqRow {{ border-left: 6px solid {cols.get(code, '#888')}; }}")
                b.clicked.connect(lambda _=False, c=code: self.set_filter("" if self.filter_tag == c else c))
                self._req_buttons[code] = b
                self.req_rows.addWidget(b)
            allb = QPushButton("Mostrar todas as solicitações")
            allb.setObjectName("ReqAll")
            allb.setCursor(Qt.PointingHandCursor)
            allb.clicked.connect(lambda: self.set_filter(""))
            allb.setVisible(bool(self.filter_tag))
            self._all_btn = allb
            self.req_rows.addWidget(allb)
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
        self.req_line1.setObjectName("ReqLine")
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
        done = getattr(self, "_done", set())
        prog = getattr(self, "_progress", {})
        for r in self.rows:
            r.set_progress(*prog.get(r.part.id, (0, 0)))
            if r.part.id in done:
                r.set_done(True)
        self._done_count = sum(1 for p in self.parts if p.id in done)
        if self.filter_tag and not any(p.tag == self.filter_tag for p in self.parts):
            self.filter_tag = ""
        self._apply_filter()
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
        nd = getattr(self, "_done_count", 0)
        if nd:
            txt += f" · ✓ {nd} de {len(self.parts)} feita(s)"
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
