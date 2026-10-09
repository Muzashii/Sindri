"""Accessible, keyboard-operable editor for individual placed instances."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QFormLayout, QDoubleSpinBox, QComboBox, QLabel, QPushButton, QDialogButtonBox)

class PositionDialog(QDialog):
    selected = Signal(object)
    applied = Signal(object, float, float, float, int)

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.setWindowTitle("Editar posições por instância")
        self.resize(640, 520)
        root = QVBoxLayout(self)
        hint = QLabel("Escolha uma cópia na lista. Ajuste X, Y, ângulo e placa; Aplicar pode ser desfeito com Ctrl+Z após fechar.")
        hint.setWordWrap(True)
        root.addWidget(hint)
        self.list = QListWidget()
        self.list.setAccessibleName("Instâncias encaixadas")
        root.addWidget(self.list, 1)
        form = QFormLayout()
        self.x, self.y, self.angle = QDoubleSpinBox(), QDoubleSpinBox(), QDoubleSpinBox()
        for field in (self.x, self.y):
            field.setRange(-10000, 10000)
            field.setDecimals(3)
            field.setSingleStep(1)
            field.setSuffix(" mm")
        self.angle.setRange(0, 359.999)
        self.angle.setDecimals(3)
        self.angle.setSuffix(" °")
        self.plate = QComboBox()
        for name, field in (("X", self.x), ("Y", self.y), ("Ângulo", self.angle), ("Placa", self.plate)):
            field.setAccessibleName(name + " da instância")
            form.addRow(name, field)
        root.addLayout(form)
        self.feedback = QLabel()
        self.feedback.setWordWrap(True)
        self.feedback.setAccessibleName("Validade da posição")
        root.addWidget(self.feedback)
        apply = QPushButton("Aplicar posição")
        apply.clicked.connect(self.apply)
        root.addWidget(apply)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.button(QDialogButtonBox.Close).setText("Fechar")
        bb.rejected.connect(self.reject)
        root.addWidget(bb)
        self.list.currentRowChanged.connect(self.load)
        self.refresh()

    def refresh(self, key=None):
        self.list.blockSignals(True)
        self.list.clear()
        idx = self.owner.sheet_index()
        row = 0
        bad_indices = self.owner.checker.colliding(self.owner.placements) if self.owner.checker else set()
        for n, pl in enumerate(self.owner.placements):
            part = self.owner.pmap[pl.part_id]
            key_pl = (pl.part_id, pl.instance)
            bad = n in bad_indices
            item = QListWidgetItem(f"{part.name} · cópia {pl.instance + 1} · placa {idx.number.get(pl.sheet_index, pl.sheet_index + 1)}" +
                                   (" · verificar colisão, borda ou material" if bad else ""))
            item.setData(Qt.UserRole, key_pl)
            self.list.addItem(item)
            if key_pl == key:
                row = n
        self.list.blockSignals(False)
        self.list.setCurrentRow(row if self.list.count() else -1)

    def placement(self):
        it = self.list.currentItem()
        if it is None:
            return None
        key = it.data(Qt.UserRole)
        return next((pl for pl in self.owner.placements if (pl.part_id, pl.instance) == tuple(key)), None)

    def load(self, _row):
        pl = self.placement()
        if pl is None:
            return
        self.selected.emit((pl.part_id, pl.instance))
        self.x.setValue(pl.x)
        self.y.setValue(pl.y)
        self.angle.setValue(pl.rotation)
        self.angle.setEnabled(not self.owner.pmap[pl.part_id].rotation_locked)
        self.plate.clear()
        idx = self.owner.sheet_index()
        mat = self.owner.pmap[pl.part_id].material
        for si in idx.ordered:
            if si == pl.sheet_index or not idx.material.get(si) or idx.material[si] == mat:
                self.plate.addItem(f"Placa {idx.number[si]} · {idx.material.get(si) or 'sem material'}", si)
        self.plate.addItem("Nova placa", self.owner.n_sheets)
        self.plate.setCurrentIndex(max(0, self.plate.findData(pl.sheet_index)))
        self.feedback.setText("Peça com posição travada: aplicar ajuste manual altera sua posição." if pl.locked else "")

    def apply(self):
        pl = self.placement()
        if pl is None:
            return
        key = (pl.part_id, pl.instance)
        self.applied.emit(key, self.x.value(), self.y.value(), self.angle.value(), self.plate.currentData())
        self.refresh(key)
        self.feedback.setText(self.owner.status_label.text())
