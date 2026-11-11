"""Diálogo "Grade de teste": gera a grade velocidade × potência de um material, abre no RDWorks já com os
valores de cada cor e, depois do corte, grava no banco de materiais o quadrado que ficou bom."""
from __future__ import annotations

import os

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from ..core import testgrid
from ..core.laser import aci_rgb
from .nowheel import protect


def grids_folder() -> str:
    from ..core.intranet import default_base_folder
    return os.path.join(os.path.dirname(default_base_folder()), "Grades")


class GridPreview(QWidget):
    """Desenho simples do primeiro arquivo da grade (quadrados na cor de cada camada)."""

    def __init__(self):
        super().__init__()
        self.plan: testgrid.GridPlan | None = None
        self.setMinimumSize(260, 180)

    def set_plan(self, plan):
        self.plan = plan
        self.update()

    def paintEvent(self, _e):
        if not self.plan or not self.plan.sheets:
            return
        s = self.plan.sheets[0]
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        k = min((self.width() - 8) / max(s.width, 1), (self.height() - 8) / max(s.height, 1))
        ox, oy = 4.0, 4.0 + s.height * k
        g = testgrid.sheet_geometry(s, self.plan)
        pen = QPen(QColor(*aci_rgb(s.label_aci)))
        pen.setWidthF(1.0)
        p.setPen(pen)
        for pts in g["labels"]:
            for a, b in zip(pts, pts[1:]):
                p.drawLine(int(ox + a[0] * k), int(oy - a[1] * k), int(ox + b[0] * k), int(oy - b[1] * k))
        for c, pts in g["squares"]:
            col = QColor(*aci_rgb(c.aci))
            p.setPen(QPen(col, 1.5))
            fill = QColor(col)
            fill.setAlpha(110 if self.plan.mode == "gravacao" else 30)
            p.setBrush(fill)
            x0, y0 = pts[0]
            p.drawRect(QRectF(ox + x0 * k, oy - (y0 + self.plan.size) * k, self.plan.size * k, self.plan.size * k))
        p.end()


class TestGridDialog(QDialog):
    def __init__(self, owner, material: str = ""):
        super().__init__(owner)
        self.owner = owner
        self.setWindowTitle("Grade de teste — velocidade × potência")
        self.plan: testgrid.GridPlan | None = None
        self.files: list[str] = []

        self.material = QComboBox()
        self.material.setEditable(True)
        names = [m.name for m in owner.material_db().materials]
        self.material.addItems(names)
        if material:
            self.material.setCurrentText(material)
        self.mode = QComboBox()
        self.mode.addItem("Corte (qual quadrado solta limpo)", "corte")
        self.mode.addItem("Gravação (qual tom fica bom)", "gravacao")

        def dbl(lo, hi, val, dec=1, suffix=""):
            w = QDoubleSpinBox()
            w.setRange(lo, hi)
            w.setDecimals(dec)
            w.setValue(val)
            if suffix:
                w.setSuffix(suffix)
            protect(w)
            return w

        def num(lo, hi, val):
            w = QSpinBox()
            w.setRange(lo, hi)
            w.setValue(val)
            protect(w)
            return w

        self.v_lo, self.v_hi, self.v_n = dbl(1, 1000, 10, 0, " mm/s"), dbl(1, 1000, 30, 0, " mm/s"), num(1, 15, 3)
        self.p_lo, self.p_hi, self.p_n = dbl(1, 100, 40, 0, " %"), dbl(1, 100, 80, 0, " %"), num(1, 15, 5)
        self.size = dbl(4, 40, 10, 1, " mm")
        self.gap = dbl(1, 20, 3, 1, " mm")
        self.lab_v, self.lab_p = dbl(10, 1000, 300, 0, " mm/s"), dbl(1, 100, 12, 0, " %")

        form = QFormLayout()
        form.addRow("Material", self.material)
        form.addRow("O que testar", self.mode)
        for label, a, b, n in (("Velocidade", self.v_lo, self.v_hi, self.v_n), ("Potência", self.p_lo, self.p_hi, self.p_n)):
            row = QHBoxLayout()
            row.addWidget(QLabel("de"))
            row.addWidget(a)
            row.addWidget(QLabel("a"))
            row.addWidget(b)
            row.addWidget(QLabel("em"))
            row.addWidget(n)
            row.addWidget(QLabel("passos"))
            form.addRow(label, row)
        row = QHBoxLayout()
        row.addWidget(self.size)
        row.addWidget(QLabel("espaço"))
        row.addWidget(self.gap)
        form.addRow("Quadrado", row)
        row = QHBoxLayout()
        row.addWidget(self.lab_v)
        row.addWidget(self.lab_p)
        form.addRow("Números (gravação leve)", row)

        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setObjectName("Muted")
        self.preview = GridPreview()
        self.b_make = QPushButton("Gerar e abrir no RDWorks")
        self.b_make.setObjectName("primary")
        self.b_make.clicked.connect(self.generate)
        self.b_next = QPushButton("Abrir o próximo arquivo")
        self.b_next.clicked.connect(self.open_next)
        self.b_next.setVisible(False)
        self._next = 1

        res = QGroupBox("Depois de cortar: qual quadrado ficou bom?")
        rf = QFormLayout(res)
        self.pick_v, self.pick_p = QComboBox(), QComboBox()
        self.who = QLineEdit()
        self.who.setPlaceholderText("seu nome")
        rf.addRow("Velocidade", self.pick_v)
        rf.addRow("Potência", self.pick_p)
        rf.addRow("Quem testou", self.who)
        self.b_save = QPushButton("Gravar no banco de materiais")
        self.b_save.clicked.connect(self.save_result)
        rf.addRow(self.b_save)
        self.result = QLabel()
        self.result.setWordWrap(True)

        left = QVBoxLayout()
        left.addLayout(form)
        left.addWidget(self.summary)
        btns = QHBoxLayout()
        btns.addWidget(self.b_make)
        btns.addWidget(self.b_next)
        btns.addStretch(1)
        left.addLayout(btns)
        left.addWidget(res)
        left.addWidget(self.result)
        left.addStretch(1)
        lay = QHBoxLayout(self)
        lay.addLayout(left, 3)
        lay.addWidget(self.preview, 2)

        for w in (self.v_lo, self.v_hi, self.p_lo, self.p_hi, self.size, self.gap):
            w.valueChanged.connect(self.refresh)
        for w in (self.v_n, self.p_n):
            w.valueChanged.connect(self.refresh)
        self.mode.currentIndexChanged.connect(self.refresh)
        self.material.currentTextChanged.connect(self.refresh)
        self.refresh()

    # ------------------------------------------------------------------
    def _make_plan(self):
        speeds = testgrid.steps(self.v_lo.value(), self.v_hi.value(), self.v_n.value(), 0)
        powers = testgrid.steps(self.p_lo.value(), self.p_hi.value(), self.p_n.value(), 0)
        pal = None
        try:
            pal = self.owner._laser_palette()
        except Exception:                               # sem RDWorks: paleta padrão
            pal = None
        return testgrid.plan_grid(self.material.currentText().strip() or "Material", speeds, powers,
                                  self.mode.currentData(), self.size.value(), self.gap.value(), palette=pal)

    def refresh(self, *_):
        try:
            self.plan = self._make_plan()
        except ValueError as e:
            self.plan = None
            self.summary.setText(str(e))
            self.b_make.setEnabled(False)
            return
        self.b_make.setEnabled(True)
        n = len(self.plan.cells)
        s0 = self.plan.sheets[0]
        files = len(self.plan.sheets)
        txt = f"{n} combinação(ões) · {s0.width:.0f} × {s0.height:.0f} mm"
        if files > 1:
            txt += (f" · {files} arquivos (o RDWorks separa os valores por cor e cabem "
                    f"{len(testgrid.cell_colors())} cores por arquivo)")
        self.summary.setText(txt)
        self.preview.set_plan(self.plan)
        for box, vals, fmt in ((self.pick_v, sorted({c.speed for c in self.plan.cells}), "{:g} mm/s"),
                               (self.pick_p, sorted({c.power_max for c in self.plan.cells}), "{:g} %")):
            box.clear()
            for v in vals:
                box.addItem(fmt.format(v), v)

    def generate(self):
        if not self.plan:
            return
        self.files = testgrid.write_plan(self.plan, grids_folder())
        self._next = 0
        self.open_next()

    def open_next(self):
        if not self.plan or self._next >= len(self.files):
            return
        sheet = self.plan.sheets[self._next]
        path = self.files[self._next]
        values = testgrid.sheet_values(sheet, (self.lab_v.value(), self.lab_p.value()))
        msg = self.owner._open_in_rdworks([path], None, values) or ""
        self._next += 1
        left = len(self.files) - self._next
        self.b_next.setVisible(left > 0)
        self.b_next.setText(f"Abrir o próximo arquivo ({left} restante(s))")
        self.result.setText(f"Arquivo salvo em {path}." + (f"\n{msg}" if msg else ""))

    def chosen_cell(self):
        if not self.plan:
            return None
        v, p = self.pick_v.currentData(), self.pick_p.currentData()
        return next((c for c in self.plan.cells if c.speed == v and c.power_max == p), None)

    def save_result(self):
        cell = self.chosen_cell()
        if cell is None:
            self.result.setText("Escolha uma velocidade e uma potência que estão na grade.")
            return
        db = self.owner.material_db()
        m = testgrid.record_result(db, self.material.currentText().strip(), cell, self.plan.mode,
                                   self.who.text().strip())
        db.save()
        self.owner.__dict__.pop("_mdb", None)
        try:
            self.owner._refresh_laser_panel(force=True)
        except Exception:                               # painel ainda não montado
            pass
        what = "corte" if self.plan.mode == "corte" else "gravação vetorial"
        self.result.setText(f"Gravado em “{m.name}” ({what}): {cell.speed:g} mm/s, {cell.power_min:g}–"
                            f"{cell.power_max:g}%, teste de {m.tested}.")
