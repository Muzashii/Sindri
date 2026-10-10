"""Editor do banco de materiais (chapa, espaçamento, kerf, teste, proibido e parâmetros por camada)."""
from __future__ import annotations

import datetime as _dt
import os

from PySide6.QtCore import QDate, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDateEdit, QDialog, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout, QWidget)

from ..core import material_db, material_safety
from ..core.laser import check_from_entry
from ..core.operations import LABELS

LAYER_OPS = material_db.LAYER_OPS


def _dspin(lo, hi, step, dec, suffix, tip, special: str = "") -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setDecimals(dec)
    s.setSuffix(suffix)
    s.setToolTip(tip)
    if special:
        s.setSpecialValueText(special)
    return s


class MaterialsDialog(QDialog):
    def __init__(self, owner, select: str = ""):
        super().__init__(owner)
        self.owner = owner
        self.setWindowTitle("Banco de materiais")
        self.resize(860, 620)
        self._current: str | None = None
        self._ops: dict = {}
        lay = QVBoxLayout(self)

        top = QHBoxLayout()
        self.path_label = QLabel()
        self.path_label.setWordWrap(True)
        self.path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        top.addWidget(self.path_label, 1)
        b = QPushButton("Usar pasta compartilhada…")
        b.setToolTip("Escolha uma pasta da rede do laboratório: todos os PCs passam a usar o mesmo banco.")
        b.clicked.connect(self._choose_folder)
        top.addWidget(b)
        b = QPushButton("Abrir pasta")
        b.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(self.db.path))))
        top.addWidget(b)
        lay.addLayout(top)

        body = QHBoxLayout()
        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._on_select)
        left.addWidget(self.list, 1)
        row = QHBoxLayout()
        b_new = QPushButton("Novo")
        b_new.clicked.connect(self._new)
        self.b_del = QPushButton("Excluir")
        self.b_del.clicked.connect(self._delete)
        row.addWidget(b_new)
        row.addWidget(self.b_del)
        left.addLayout(row)
        lw = QWidget()
        lw.setLayout(left)
        lw.setMaximumWidth(260)
        body.addWidget(lw)

        right = QVBoxLayout()
        g = QGroupBox("Material")
        f = QFormLayout(g)
        self.name = QLineEdit()
        self.name.setToolTip("Como aparece nas solicitações (\"MDF 3mm\" e \"mdf 3 mm\" são o mesmo).")
        f.addRow("Nome", self.name)
        self.thickness = _dspin(0, 50, 0.5, 2, " mm", "Espessura do material.")
        f.addRow("Espessura", self.thickness)
        sheet = QHBoxLayout()
        self.sheet_w = _dspin(0, 5000, 10, 1, " mm", "Largura da chapa padrão. 0 = usar a do painel.", "do painel")
        self.sheet_h = _dspin(0, 5000, 10, 1, " mm", "Altura da chapa padrão. 0 = usar a do painel.", "do painel")
        sheet.addWidget(self.sheet_w)
        sheet.addWidget(QLabel("×"))
        sheet.addWidget(self.sheet_h)
        f.addRow("Chapa", sheet)
        self.margin = _dspin(-1, 200, 0.5, 1, " mm", "Margem da borda. \"do painel\" = usar a do painel.",
                             "do painel")
        f.addRow("Margem", self.margin)
        self.spacing = _dspin(0, 50, 0.5, 2, " mm",
                              "Espaçamento entre peças. Acrílico e MDF 6 mm: ≥ espessura (a ponte entre cortes "
                              "empena ou derrete). 0 = usar o do painel.", "do painel")
        f.addRow("Espaçamento", self.spacing)
        self.kerf = _dspin(0, 2, 0.01, 3, " mm", "Largura do corte medida no teste de kerf (aba Caixa).")
        f.addRow("Kerf medido", self.kerf)
        self.min_part = _dspin(0, 200, 1, 1, " mm", "Peças com as duas medidas menores que isso caem na colmeia "
                               "ou levantam com o sopro: o Sindri avisa e sugere micro-pontes.")
        f.addRow("Peça mínima", self.min_part)
        self.grain = QCheckBox("Tem veio (compensado, madeira): só 0° e 180°")
        f.addRow(self.grain)
        self.acrylic = QComboBox()
        for k, v in (("", "—"), ("cast", "Cast (fundido)"), ("extrudado", "Extrudado")):
            self.acrylic.addItem(v, k)
        self.acrylic.setToolTip("Acrílico: cast e extrudado gravam diferente.")
        f.addRow("Tipo de acrílico", self.acrylic)
        test = QHBoxLayout()
        self.tested_on = QCheckBox("Testado em")
        self.tested = QDateEdit()
        self.tested.setCalendarPopup(True)
        self.tested.setDisplayFormat("dd/MM/yyyy")
        self.tested_on.toggled.connect(self.tested.setEnabled)
        self.validated_by = QLineEdit()
        self.validated_by.setPlaceholderText("quem validou")
        test.addWidget(self.tested_on)
        test.addWidget(self.tested)
        test.addWidget(self.validated_by, 1)
        f.addRow("Grade de teste", test)
        self.age = QLabel()
        self.age.setObjectName("Muted")
        f.addRow("", self.age)
        self.forbidden = QCheckBox("Proibido no laser")
        self.forbidden_reason = QLineEdit()
        self.forbidden_reason.setPlaceholderText("motivo (aparece ao bloquear a exportação)")
        fb = QHBoxLayout()
        fb.addWidget(self.forbidden)
        fb.addWidget(self.forbidden_reason, 1)
        f.addRow(fb)
        self.notes = QLineEdit()
        f.addRow("Observações", self.notes)
        right.addWidget(g)

        gl = QGroupBox("Laser por camada")
        grid = QGridLayout(gl)
        self.op_labels = {}
        for r, op in enumerate(LAYER_OPS):
            grid.addWidget(QLabel(LABELS.get(op, op)), r, 0)
            lab = QLabel()
            lab.setObjectName("Muted")
            grid.addWidget(lab, r, 1)
            self.op_labels[op] = lab
            b = QPushButton("Editar…")
            b.clicked.connect(lambda _=False, o=op: self._edit_op(o))
            grid.addWidget(b, r, 2)
        grid.setColumnStretch(1, 1)
        right.addWidget(gl)
        self.safety = QLabel()
        self.safety.setWordWrap(True)
        self.safety.setObjectName("InlineWarning")
        right.addWidget(self.safety)
        right.addStretch(1)
        btns = QHBoxLayout()
        btns.addStretch(1)
        self.b_save = QPushButton("Salvar material")
        self.b_save.setDefault(True)
        self.b_save.clicked.connect(self.save_current)
        b_close = QPushButton("Fechar")
        b_close.clicked.connect(self._close)
        btns.addWidget(self.b_save)
        btns.addWidget(b_close)
        right.addLayout(btns)
        rw = QWidget()
        rw.setLayout(right)
        body.addWidget(rw, 1)
        lay.addLayout(body, 1)
        self.name.textChanged.connect(self._update_safety)
        self.forbidden.toggled.connect(self._update_safety)
        self._reload(select)

    # ------------------------------------------------------------------
    @property
    def db(self) -> material_db.MaterialDB:
        return self.owner.material_db()

    def _reload(self, select: str = ""):
        self.owner.flush_material_db()
        self.owner.__dict__.pop("_mdb", None)
        db = self.db
        broken = getattr(db, "_broken", False)
        self.path_label.setText(f"<b>Arquivo:</b> {db.path}" + (" <b>(com problema: só leitura)</b>" if broken else ""))
        self.b_save.setEnabled(not broken)
        self.list.blockSignals(True)
        self.list.clear()
        today = _dt.date.today()
        for m in sorted(db.materials, key=lambda m: material_db.key(m.name)):
            mark = "⛔ " if m.forbidden or material_safety.check_material(m.name).blocked else (
                "⚠ " if m.is_stale(today) else "")
            it = QListWidgetItem(mark + m.name)
            it.setData(Qt.UserRole, m.name)
            self.list.addItem(it)
        self.list.blockSignals(False)
        target = []
        found = db.find(select) if select else None
        if found is not None:
            target = [self.list.item(i) for i in range(self.list.count())
                      if self.list.item(i).data(Qt.UserRole) == found.name]
        if target:
            self.list.setCurrentItem(target[0])
        elif self.list.count():
            self.list.setCurrentRow(0)
        else:
            self._load(None)

    def _on_select(self, cur, _prev=None):
        self._load(self.db.find(cur.data(Qt.UserRole)) if cur else None)

    def _load(self, m: material_db.Material | None):
        self._current = m.name if m else None
        m = m or material_db.Material("")
        self.name.setText(m.name)
        self.thickness.setValue(float(m.thickness))
        self.sheet_w.setValue(float(m.sheet_width))
        self.sheet_h.setValue(float(m.sheet_height))
        self.margin.setValue(float(m.margin))
        self.spacing.setValue(float(m.spacing))
        self.kerf.setValue(float(m.kerf))
        self.min_part.setValue(float(m.min_part))
        self.grain.setChecked(bool(m.grain))
        self.acrylic.setCurrentIndex(max(0, self.acrylic.findData(m.acrylic_type or "")))
        self.tested_on.setChecked(bool(m.tested))
        self.tested.setEnabled(bool(m.tested))
        d = _dt.date.fromisoformat(m.tested) if m.tested else _dt.date.today()
        self.tested.setDate(QDate(d.year, d.month, d.day))
        self.validated_by.setText(m.validated_by)
        self.forbidden.setChecked(bool(m.forbidden))
        self.forbidden_reason.setText(m.forbidden_reason)
        self.notes.setText(m.notes)
        self._ops = {op: dict(o) for op, o in m.ops.items()}
        age = m.test_age_days()
        self.age.setText("Sem teste registrado: faça a grade de teste deste material." if age is None else
                         f"Teste feito há {age} dia(s)" + (" — refaça a grade (o tubo perde potência com o tempo)."
                                                           if m.is_stale() else "."))
        self._update_ops()
        self._update_safety()
        self.b_del.setEnabled(self._current is not None)

    def _update_ops(self):
        for op, lab in self.op_labels.items():
            e = self._ops.get(op) or {}
            c = check_from_entry(7, "", op, e)
            lab.setText(f"{c.mode_text} · {c.values}" if e.get("speed") else "não definido (valores do RDWorks)")

    def _update_safety(self):
        r = material_safety.check_material(self.name.text(), self.forbidden.isChecked(),
                                           self.forbidden_reason.text())
        self.safety.setVisible(r.status != material_safety.OK)
        self.safety.setText(("⛔ Bloqueado: " if r.blocked else "⚠ ") + r.reason)

    def _edit_op(self, op: str):
        from .layer_params_dialog import LayerParamsDialog
        dlg = LayerParamsDialog(self.name.text() or "Material", op, self._ops.get(op) or {}, self)
        if dlg.exec():
            self._ops[op] = {**(self._ops.get(op) or {}), **dlg.values()}
            self._update_ops()

    # ------------------------------------------------------------------
    def _values(self) -> dict:
        return dict(thickness=self.thickness.value(), sheet_width=self.sheet_w.value(),
                    sheet_height=self.sheet_h.value(), margin=self.margin.value(), spacing=self.spacing.value(),
                    kerf=self.kerf.value(), min_part=self.min_part.value(), grain=self.grain.isChecked(),
                    acrylic_type=self.acrylic.currentData(),
                    tested=self.tested.date().toString("yyyy-MM-dd") if self.tested_on.isChecked() else "",
                    validated_by=self.validated_by.text().strip(), forbidden=self.forbidden.isChecked(),
                    forbidden_reason=self.forbidden_reason.text().strip(), notes=self.notes.text().strip())

    def save_current(self) -> bool:
        name = self.name.text().strip()
        if not name:
            QMessageBox.warning(self, "Banco de materiais", "Dê um nome ao material.")
            return False
        if (self.sheet_w.value() > 0) != (self.sheet_h.value() > 0):
            QMessageBox.warning(self, "Banco de materiais", "Informe largura e altura da chapa (ou deixe as duas "
                                "\"do painel\").")
            return False
        db = self.db
        other = db.find(name)
        if other is not None and other.name != self._current:
            QMessageBox.warning(self, "Banco de materiais", f"Já existe um material chamado {other.name}.")
            return False
        vals, ops = self._values(), {k: dict(v) for k, v in self._ops.items()}
        old = self._current

        def change(m: material_db.Material):
            for k, v in vals.items():
                setattr(m, k, v)
            m.ops = ops
        try:
            if old and material_db.key(old) != material_db.key(name):
                db.materials = [m for m in db.materials if material_db.key(m.name) != material_db.key(old)]
                db.__dict__.setdefault("_deleted", set()).add(material_db.key(old))
            m = db.find(name) or db.add(material_db.Material(name))
            if old and m.name != name:
                m.name = name
            change(m)
            m.validate()
        except material_db.MaterialDBError as e:
            QMessageBox.warning(self, "Banco de materiais", str(e))
            return False
        self.owner._mdb_dirty = True
        if not self.owner.flush_material_db():
            QMessageBox.warning(self, "Banco de materiais", "Não consegui gravar o arquivo do banco de materiais.")
            return False
        self._reload(name)
        return True

    def _new(self):
        base, k = "Novo material", 2
        name = base
        while self.db.find(name):
            name = f"{base} {k}"
            k += 1
        self._load(None)
        self.name.setText(name)
        self.name.setFocus()
        self.name.selectAll()

    def _delete(self):
        if not self._current:
            return
        if QMessageBox.question(self, "Excluir material", f"Excluir {self._current} do banco de materiais?\n"
                                "(vale para todos os PCs que usam este banco)") != QMessageBox.Yes:
            return
        try:
            self.db.remove(self._current)
        except (OSError, material_db.MaterialDBError) as e:
            QMessageBox.warning(self, "Banco de materiais", str(e))
            return
        self._reload()

    def _choose_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Pasta do banco de materiais", os.path.dirname(self.db.path))
        if not d:
            return
        self.owner.flush_material_db()
        self.owner.set_material_db_path(d)
        self._reload()

    def _close(self):
        self.accept()
