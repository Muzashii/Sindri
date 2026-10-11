"""Painel lateral de peças: grade de cartões (miniatura, ×quantidade, medida) e bloco da peça selecionada."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QStyle, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QPushButton, QSpinBox, QToolButton, QVBoxLayout,
                               QWidget)

from ..core.models import Part
from . import theme
from .icons import icon
from .render import thumbnail


def _fmt(v: float) -> str:
    s = f"{v:.1f}".rstrip("0").rstrip(".")
    return s.replace(".", ",")


def _fmt_list(v: float) -> str:
    """Medida curta para a lista: inteiro a partir de 10 mm, um decimal abaixo disso."""
    return str(int(round(v))) if v >= 10 else _fmt(v)


class PartRow(QFrame):
    """Cartão de uma peça na grade: miniatura, nome, quantidade (×N), medida e o ✓ de "feita".

    Quantidade e rotação fixa são editadas no bloco da peça selecionada (``PartDetail``)."""
    THUMB_H = 54

    def __init__(self, part: Part, warns: list[str], dark: bool, panel: PartsPanel):
        super().__init__()
        self.warns = list(warns)
        self.setObjectName("PartRow")
        self.part = part
        self.panel = panel
        self.dark = dark
        from PySide6.QtWidgets import QSizePolicy
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(4)

        # linha de cima: material, aviso, rotação fixa e o ✓ de feita
        top = QHBoxLayout()
        top.setSpacing(4)
        if part.material:
            chip = QLabel(part.material)
            chip.setObjectName("MatChip")
            chip.setStyleSheet(f"background: {theme.material_color(part.material).name()};")
            chip.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            chip.setMinimumWidth(24)
            chip.setToolTip(part.material)
            top.addWidget(chip, 1)
        else:
            top.addStretch(1)
        if warns:
            b = QLabel("⚠")
            b.setObjectName("Badge")
            b.setToolTip("\n".join(warns))
            b.setAccessibleName("Aviso: " + "; ".join(warns))
            top.addWidget(b)
        self.lock_mark = QLabel()
        self.lock_mark.setToolTip("Rotação fixa: a peça não gira no encaixe")
        self.lock_mark.setVisible(part.rotation_locked)
        top.addWidget(self.lock_mark)
        self.done_btn = QToolButton()
        self.done_btn.setObjectName("DoneButton")
        self.done_btn.setCheckable(True)
        self.done_btn.setAccessibleName(f"Marcar {part.name} como feita")
        self.done_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.done_btn.setToolTip("Marque quando esta peça já tiver sido cortada")
        self.done_btn.toggled.connect(self._done_toggled)
        top.addWidget(self.done_btn)
        lay.addLayout(top)

        self.thumb = QLabel()
        self.thumb.setObjectName("Thumb")
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setFixedHeight(self.THUMB_H)
        self.thumb.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        lay.addWidget(self.thumb)

        mid = QHBoxLayout()
        mid.setSpacing(4)
        self.full_name = part.name.split(" (")[0]
        self.name = QLabel(self.full_name)
        self.name.setObjectName("PartName")
        self.name.setMinimumWidth(30)
        self.name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.qty = QLabel()
        self.qty.setObjectName("PartQty")
        self.qty.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        mid.addWidget(self.name, 1)
        mid.addWidget(self.qty)
        lay.addLayout(mid)
        w, h = part.size
        sub = QLabel(f"{_fmt_list(w)} × {_fmt_list(h)} mm")
        sub.setObjectName("PartSub")
        sub.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        extra = [f"{part.file_quantity}× no arquivo"] + ([f"{len(part.holes)} furo(s)"] if part.holes else [])
        sub.setToolTip(f"{_fmt(w)} × {_fmt(h)} mm · " + " · ".join(extra))
        self._sub = sub
        lay.addWidget(sub)
        self.set_quantity(part.quantity)

        tips = [part.name, f"{_fmt(w)} × {_fmt(h)} mm · área {part.area / 100:.1f} cm²",
                f"Arquivo: {part.source_file.replace(chr(92), '/').split('/')[-1]}"]
        if warns:
            tips += [""] + ["⚠ " + x for x in warns]
            self.setProperty("warn", True)
        self.setToolTip("\n".join(tips))
        self.setAccessibleName(f"{part.name}, {part.quantity} unidade(s), {_fmt(w)} por {_fmt(h)} milímetros")
        self._done_icon()
        self._refresh_pixmaps()

    def _refresh_pixmaps(self):
        t = theme.tokens()
        from .icons import pixmap
        self.lock_mark.setPixmap(pixmap("lock", t["muted"], 13))
        width = max(40, self.thumb.width() or 110)
        self.thumb.setPixmap(thumbnail(self.part, width, self.dark, self.THUMB_H, mdf=True))
        self._thumb_w = width

    def set_quantity(self, q: int):
        self.qty.setText(f"×{q}")
        self.qty.setAccessibleName(f"{q} unidade(s)")

    def set_rotation_locked(self, on: bool):
        self.lock_mark.setVisible(on)

    def _done_icon(self):
        on = self.done_btn.isChecked()
        self.done_btn.setIcon(icon("check", "#ffffff" if on else theme.tokens()["muted"], 13))
        prog = getattr(self, "_progress", None)
        if on or not prog or prog[0] == 0:
            self.done_btn.setText("")
            self.done_btn.setToolTip("Feita: marque quando esta peça já tiver sido cortada")
        else:
            self.done_btn.setText(f"{prog[0]}/{prog[1]}")
            self.done_btn.setToolTip(f"{prog[0]} de {prog[1]} cópias já estão em placas cortadas.\n"
                                     "Clique para marcar a peça inteira como feita.")

    def set_progress(self, cut: int, total: int):
        if getattr(self, "_progress", None) == (cut, total):
            return
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
        if self.property("done") == v and self.done_btn.isChecked() == v:
            return                                   # nada mudou: evita repintar o cartão
        if self.done_btn.isChecked() != v:
            self.done_btn.blockSignals(True)
            self.done_btn.setChecked(v)
            self.done_btn.blockSignals(False)
        self._apply_done(v)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if abs(self.thumb.width() - getattr(self, "_thumb_w", 0)) > 4:
            self._refresh_pixmaps()
        fm = self.name.fontMetrics()
        self.name.setText(fm.elidedText(self.full_name, Qt.ElideRight, max(30, self.name.width())))

    def set_selected(self, sel: bool):
        self.setProperty("selected", sel)
        self.style().unpolish(self)
        self.style().polish(self)


class PartDetail(QFrame):
    """Peça selecionada: quantidade (− / +), rotação fixa e avisos."""

    def __init__(self, panel: PartsPanel):
        super().__init__()
        self.setObjectName("PartDetail")
        self.panel = panel
        self.part: Part | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(8)
        self.title = QLabel()
        self.title.setObjectName("PartName")
        from PySide6.QtWidgets import QSizePolicy
        self.title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        lay.addWidget(self.title)
        self.hint = QLabel("Selecione uma peça para mudar a quantidade ou travar a rotação.")
        self.hint.setObjectName("Muted")
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint)
        self.body = QWidget()
        bl = QVBoxLayout(self.body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(8)
        row = QHBoxLayout()
        lab = QLabel("Quantidade")
        row.addWidget(lab, 1)
        stepper = QFrame()
        stepper.setObjectName("Stepper")
        sl = QHBoxLayout(stepper)
        sl.setContentsMargins(1, 1, 1, 1)
        sl.setSpacing(0)
        self.btn_minus = QToolButton()
        self.btn_minus.setObjectName("StepButton")
        self.btn_minus.setText("−")
        self.btn_minus.setAccessibleName("Diminuir quantidade")
        self.btn_minus.setAutoRepeat(True)
        self.spin = QSpinBox()
        self.spin.setRange(0, 9999)
        self.spin.setButtonSymbols(QSpinBox.NoButtons)
        self.spin.setAlignment(Qt.AlignCenter)
        self.spin.setFixedWidth(56)
        self.spin.setStyleSheet("QSpinBox { border: none; background: transparent; padding: 2px; }")
        self.spin.setToolTip("Quantidade a cortar")
        lab.setBuddy(self.spin)
        self.btn_plus = QToolButton()
        self.btn_plus.setObjectName("StepButton")
        self.btn_plus.setText("+")
        self.btn_plus.setAccessibleName("Aumentar quantidade")
        self.btn_plus.setAutoRepeat(True)
        self.btn_minus.clicked.connect(lambda: self.spin.stepDown())
        self.btn_plus.clicked.connect(lambda: self.spin.stepUp())
        for w in (self.btn_minus, self.spin, self.btn_plus):
            sl.addWidget(w)
        row.addWidget(stepper)
        bl.addLayout(row)
        self.lock = QCheckBox("Rotação fixa (veio da madeira)")
        self.lock.setToolTip("Travar rotação: a peça nunca será girada\n(útil para veio da madeira ou gravações)")
        self.lock.setAccessibleDescription("Quando marcada, a peça não gira no encaixe automático.")
        bl.addWidget(self.lock)
        self.warn = QLabel()
        self.warn.setObjectName("InlineWarning")
        self.warn.setWordWrap(True)
        bl.addWidget(self.warn)
        lay.addWidget(self.body)
        self.spin.valueChanged.connect(self._qty)
        self.lock.toggled.connect(self._locked)
        self.bind(None)

    def bind(self, row: PartRow | None):
        self._row = row
        self.part = row.part if row is not None else None
        on = self.part is not None
        self.title.setVisible(on)
        self.body.setVisible(on)
        self.hint.setVisible(not on)
        if not on:
            return
        p = self.part
        fm = self.title.fontMetrics()
        self.title.setText(fm.elidedText(row.full_name, Qt.ElideRight, max(80, self.width() - 24)))
        self.title.setToolTip(p.name)
        for w, v in ((self.spin, p.quantity), (self.lock, p.rotation_locked)):
            w.blockSignals(True)
            (w.setValue if w is self.spin else w.setChecked)(v)
            w.blockSignals(False)
        self.spin.setAccessibleName(f"Quantidade de {p.name}")
        self.lock.setAccessibleName(f"Rotação fixa de {p.name}")
        self.warn.setText("\n".join("⚠ " + x for x in row.warns))
        self.warn.setVisible(bool(row.warns))

    def _qty(self, v: int):
        if self._row is None:
            return
        self._row.set_quantity(v)
        self.panel.quantityChanged.emit(self.part.id, v)

    def _locked(self, v: bool):
        if self._row is None:
            return
        self._row.set_rotation_locked(v)
        self.panel.rotationLockChanged.emit(self.part.id, v)

    def set_editing(self, on: bool):
        for w in (self.spin, self.btn_minus, self.btn_plus, self.lock):
            w.setEnabled(on)


class PartsPanel(QWidget):
    quantityChanged = Signal(str, int)
    rotationLockChanged = Signal(str, bool)
    partSelected = Signal(str)
    doneChanged = Signal(str, bool)       # peça marcada como feita (cortada)
    requestFilter = Signal(str)           # mostrar só as peças de uma solicitação ("" = todas)
    sheetToggled = Signal(int, bool)      # placa marcada como cortada
    sheetOpenRequested = Signal(int)      # abrir só o arquivo desta placa no RDWorks
    addRequested = Signal()               # cartão "Adicionar DXF"

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 0, 12)
        frame = QFrame()
        frame.setObjectName("PanelCard")
        outer.addWidget(frame)
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(12, 14, 12, 12)
        lay.setSpacing(10)
        head = QHBoxLayout()
        title = QLabel("Peças")
        title.setObjectName("PanelHeading")
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

        # grade de 2 colunas de cartões (o último é "Adicionar DXF")
        self.list = QListWidget()
        self.list.setViewMode(QListWidget.IconMode)
        self.list.setMovement(QListWidget.Static)
        self.list.setResizeMode(QListWidget.Adjust)
        self.list.setWrapping(True)
        self.list.setSpacing(0)
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setAccessibleName("Peças do encaixe")
        self.list.currentRowChanged.connect(self._sel)
        self.list.viewport().installEventFilter(self)
        lay.addWidget(self.list, 1)
        self.detail = PartDetail(self)
        lay.addWidget(self.detail)

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
        self.sheet_open_buttons = {}
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
                cb.setObjectName("CutDone")
            cb.toggled.connect(lambda on, si=s["si"]: self.sheetToggled.emit(si, on))
            self.sheet_checks[s["si"]] = cb
            cell = QWidget()
            h = QHBoxLayout(cell)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(2)
            h.addWidget(cb, 1)
            op = QToolButton()
            op.setText("▶")
            op.setAutoRaise(True)
            op.setToolTip(f"Abrir no RDWorks só a placa {s['n']} (arquivo de corte desta placa, origem no canto "
                          "da chapa).\nFluxo: abrir → cortar → marcar como cortada → próxima.")
            op.setAccessibleName(f"Abrir placa {s['n']} no RDWorks")
            op.clicked.connect(lambda _=False, si=s["si"]: self.sheetOpenRequested.emit(si))
            self.sheet_open_buttons[s["si"]] = op
            h.addWidget(op)
            self.sheets_grid.addWidget(cell, i // cols, i % cols)

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

    def set_editing(self, on: bool):
        """Quantidades e trava de rotação só podem mudar com o encaixe parado."""
        self.detail.set_editing(on)
        self.btn_kits.setEnabled(on and bool(self.parts))
        self.btn_reset.setEnabled(on and bool(self.parts))

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
                seal = getattr(self, "_seals", {}).get(code)
                mark = f"{seal.icon} " if seal is not None and seal.status != "ok" else ""
                count = (f"{prog[0]}/{prog[1]}" if prog and prog[1] else (f"{n} pç" if n else ""))
                label = f"{done}{mark}{code} · {r.get('nome', '') or '—'}"
                fm = b.fontMetrics()
                tail = f"   {count}" if count else ""
                avail = max(120, self.req_card.width() - 40 - fm.horizontalAdvance(tail)) if self.req_card.width() > 100 \
                    else 210
                b.setText(fm.elidedText(label, Qt.ElideRight, avail) + tail)
                b.setToolTip(f"Solicitação {code} · RM {r.get('rm', '—')} · {r.get('nome', '')}\n"
                             + (f"Selo: {seal.label}\n" + "".join(f"• {x}\n" for x in seal.reasons)
                                if seal is not None else "")
                             + "Clique para mostrar só as peças desta solicitação (clique de novo para ver todas)")
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
        seal = getattr(self, "_seals", {}).get(str(info.get("code", "")))
        if seal is not None:
            extra.append(f"{seal.icon} {seal.label}")
        self.req_line2.setText("  ·  ".join(extra))
        self.req_line2.setVisible(bool(extra))
        for m in info.get("materials", []):
            chip = QLabel(m)
            chip.setObjectName("MatChip")
            chip.setStyleSheet(f"background: {theme.material_color(m).name()};")
            self.req_mats.addWidget(chip)
        self.req_mats.addStretch(1)
        tip = "\n".join(f"{k}: {v}" for k, v in (info.get("info") or {}).items())
        if seal is not None and seal.reasons:
            tip += ("\n\n" if tip else "") + f"Selo {seal.label}:\n" + "\n".join("• " + x for x in seal.reasons)
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
        self.detail.setVisible(has)
        self.empty.setVisible(not has)
        self.btn_kits.setEnabled(has)
        self.btn_reset.setEnabled(has)
        self.btn_clear.setEnabled(has)

    def _sel(self, row: int):
        for i, r in enumerate(self.rows):
            r.set_selected(i == row)
        self.detail.bind(self.rows[row] if 0 <= row < len(self.rows) else None)
        if 0 <= row < len(self.parts):
            self.partSelected.emit(self.parts[row].id)

    # ---- grade: 2 colunas que acompanham a largura do painel
    def eventFilter(self, obj, ev):
        from PySide6.QtCore import QEvent
        if obj is self.list.viewport() and ev.type() == QEvent.Resize:
            self._layout_cards()
        return False

    def _layout_cards(self):
        n = self.list.count()
        if not n:
            return
        gap = 8
        vw = self.list.viewport().width()
        # 2 colunas: no modo grade o Qt desconta a barra de rolagem mais uma vez ao quebrar as linhas,
        # então a conta reserva essa folga (senão sobra só 1 coluna)
        reserve = self.list.style().pixelMetric(QStyle.PM_ScrollBarExtent) + 2
        w = max(96, (vw - reserve - 2 * gap) // 2)
        h = max((self.list.itemWidget(self.list.item(i)).sizeHint().height()
                 for i in range(n) if self.list.itemWidget(self.list.item(i)) is not None), default=120)
        for i in range(n):
            self.list.item(i).setSizeHint(QSize(w, h))
        self.list.setGridSize(QSize(w + gap, h + gap))
        self.list.doItemsLayout()

    def set_seals(self, seals: dict):
        """Selo (OK / atenção / bloqueado) de cada solicitação, mostrado no cartão do lote."""
        self._seals = dict(seals or {})

    def set_issues(self, issues: dict[str, list[str]]):
        """Avisos de fabricabilidade por peça (parede fina, furo pequeno…), mostrados no ⚠ da peça."""
        self._issues = dict(issues or {})

    def set_parts(self, parts: list[Part], too_big: set[str] = frozenset()):
        cur = self.list.currentRow()
        self.parts = list(parts)
        self.list.clear()
        self.rows = []
        from .owners import owner_colors
        cols = owner_colors(parts) if len({p.tag for p in parts if p.tag}) > 1 else {}
        for p in parts:
            warns = list(p.warnings) + list(getattr(self, "_issues", {}).get(p.id, []))
            if p.id in too_big:
                warns.append("Maior que a placa em todas as rotações: não será encaixada. "
                             "Aumente a placa, reduza a margem ou confira a unidade do DXF.")
            row = PartRow(p, warns, self.dark, self)
            if p.tag in cols:
                row.setStyleSheet(f"QFrame#PartRow {{ border-top: 4px solid {cols[p.tag].name()}; }}")
            it = QListWidgetItem()
            it.setSizeHint(QSize(120, row.sizeHint().height()))
            self.list.addItem(it)
            self.list.setItemWidget(it, row)
            self.rows.append(row)
        if parts:
            add = QPushButton("+\nAdicionar DXF")
            add.setObjectName("AddTile")
            add.setToolTip("Adicionar mais arquivos DXF (Ctrl+Shift+O); as peças entram junto com as atuais")
            add.setAccessibleName("Adicionar DXF")
            add.clicked.connect(self.addRequested.emit)
            it = QListWidgetItem()
            it.setFlags(Qt.ItemIsEnabled)
            self.list.addItem(it)
            self.list.setItemWidget(it, add)
        self._layout_cards()
        if 0 <= cur < len(self.rows):
            self.list.blockSignals(True)
            self.list.setCurrentRow(cur)
            self.list.blockSignals(False)
            self.rows[cur].set_selected(True)
        self.detail.bind(self.rows[cur] if 0 <= cur < len(self.rows) else None)
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
        from .nowheel import protect
        protect(self)

    def set_quantity(self, pid: str, q: int):
        for r in self.rows:
            if r.part.id == pid:
                r.set_quantity(q)
        if self.detail.part is not None and self.detail.part.id == pid:
            self.detail.spin.blockSignals(True)
            self.detail.spin.setValue(q)
            self.detail.spin.blockSignals(False)

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
                self.detail.bind(self.rows[i])
                self.list.scrollToItem(self.list.item(i))
                return
