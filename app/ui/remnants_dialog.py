"""Cadastro de retalhos: retângulo, contorno importado de DXF ou sobra de placa cortada."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout)

from ..core.remnants import RemnantError, from_dxf, rectangle


class RemnantsDialog(QDialog):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.setWindowTitle("Retalhos")
        self.resize(780, 480)
        lay = QVBoxLayout(self)
        info = QLabel("Retalhos cadastrados entram no encaixe <b>antes</b> de abrir chapa nova do mesmo material. "
                      "Ao marcar como cortada uma placa que era retalho, ele sai da lista; a sobra de qualquer "
                      "placa cortada pode virar retalho novo pelo aviso que aparece.")
        info.setWordWrap(True)
        lay.addWidget(info)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Material", "Nome", "Tamanho", "Área", "Origem", "Situação"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        lay.addWidget(self.table, 1)

        form = QFormLayout()
        self.material = QComboBox()
        self.material.setEditable(True)
        names = list(dict.fromkeys([pt.material for pt in owner.parts if pt.material] + owner.material_db().names()))
        self.material.addItems(names)
        form.addRow("Material", self.material)
        size = QHBoxLayout()
        self.w = QDoubleSpinBox()
        self.h = QDoubleSpinBox()
        for s in (self.w, self.h):
            s.setRange(0, 5000)
            s.setDecimals(1)
            s.setSuffix(" mm")
            s.setValue(300)
        size.addWidget(self.w)
        size.addWidget(QLabel("×"))
        size.addWidget(self.h)
        form.addRow("Retângulo", size)
        self.name = QLineEdit()
        self.name.setPlaceholderText("ex.: prateleira 2, canto da janela")
        form.addRow("Nome", self.name)
        lay.addLayout(form)
        row = QHBoxLayout()
        b = QPushButton("Adicionar retângulo")
        b.clicked.connect(self.add_rectangle)
        row.addWidget(b)
        b = QPushButton("Importar contorno de DXF…")
        b.setToolTip("O maior contorno fechado do DXF vira o retalho; contornos fechados dentro dele são buracos.")
        b.clicked.connect(self.add_dxf)
        row.addWidget(b)
        row.addStretch(1)
        self.b_toggle = QPushButton("Marcar como usado / disponível")
        self.b_toggle.clicked.connect(self.toggle_used)
        row.addWidget(self.b_toggle)
        self.b_del = QPushButton("Excluir")
        self.b_del.clicked.connect(self.delete)
        row.addWidget(self.b_del)
        b = QPushButton("Fechar")
        b.clicked.connect(self.accept)
        row.addWidget(b)
        lay.addLayout(row)
        self.refresh()

    def refresh(self):
        self.store = self.owner.remnant_store()
        rs = sorted(self.store.remnants, key=lambda r: (bool(r.used), r.material, -r.area))
        self.table.setRowCount(len(rs))
        for i, r in enumerate(rs):
            w, h = r.size
            vals = [r.material or "sem material", r.name, f"{w:.0f} × {h:.0f} mm", f"{r.area / 1e4:.1f} dm²",
                    r.source, f"usado em {r.used}" if r.used else "disponível"]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setData(Qt.UserRole, r.id)
                if r.used:
                    it.setForeground(Qt.gray)
                self.table.setItem(i, c, it)
        self.table.resizeColumnsToContents()

    def _selected(self) -> str | None:
        it = self.table.item(self.table.currentRow(), 0) if self.table.currentRow() >= 0 else None
        return it.data(Qt.UserRole) if it else None

    def add_rectangle(self):
        try:
            r = rectangle(self.material.currentText().strip(), self.w.value(), self.h.value(),
                          self.name.text().strip())
            self.store.add(r)
        except (RemnantError, OSError) as e:
            QMessageBox.warning(self, "Retalhos", str(e))
            return
        self.refresh()

    def add_dxf(self, path: str = ""):
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Contorno do retalho", "", "DXF (*.dxf)")
            if not path:
                return
        try:
            r = from_dxf(path, self.material.currentText().strip(), self.name.text().strip())
            self.store.add(r)
        except Exception as e:                       # DXF ruim não derruba o programa
            QMessageBox.warning(self, "Retalhos", f"Não consegui usar este DXF como retalho:\n{e}")
            return
        self.refresh()

    def toggle_used(self):
        rid = self._selected()
        r = self.store.find(rid) if rid else None
        if r is None:
            return
        self.store.mark_used(rid, not r.used)
        self.refresh()

    def delete(self):
        rid = self._selected()
        if not rid:
            return
        self.store.remove(rid)
        self.refresh()
