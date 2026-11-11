"""Cor do arquivo → operação de laser (corte, vinco, gravação), conferida pelo técnico."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QHeaderView, QLabel, QTableWidget,
                               QTableWidgetItem, QVBoxLayout)

from ..core import operations as ops
from ..core.geometry import aci_to_rgb
from ..core.laser import color_name


def swatch(aci: int, size: int = 14) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    pa = QPainter(pm)
    pa.setRenderHint(QPainter.Antialiasing)
    pa.setBrush(QColor(*aci_to_rgb(int(aci))))
    pa.setPen(QColor(120, 120, 120))
    pa.drawEllipse(1, 1, size - 2, size - 2)
    pa.end()
    return pm


class ColorOpsDialog(QDialog):
    """Uma linha por (material, cor do arquivo) com a operação escolhida."""

    def __init__(self, parts, chosen: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Cores do arquivo → operação do laser")
        self.setMinimumWidth(640)
        lay = QVBoxLayout(self)
        info = QLabel("O arquivo tem mais de uma cor. Confira o que cada cor é: o <b>contorno externo</b> da peça "
                      "é sempre corte; a cor escolhida como <b>gravação</b> ou <b>vinco</b> sai numa camada própria "
                      "e <b>não</b> é cortada.")
        info.setWordWrap(True)
        lay.addWidget(info)
        colors = ops.file_colors(parts)
        current = ops.merge_color_ops(parts, chosen)
        self.keys = sorted(colors, key=lambda k: (k[0], -colors[k]["outer"], k[1]))
        self.table = QTableWidget(len(self.keys), 4)
        self.table.setHorizontalHeaderLabels(["Material", "Cor", "Onde aparece", "Operação"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.combos: list[QComboBox] = []
        for r, k in enumerate(self.keys):
            mat, aci = k
            info = colors[k]
            self.table.setItem(r, 0, QTableWidgetItem(mat or "sem material"))
            it = QTableWidgetItem(QIcon(swatch(aci)), color_name(aci))
            self.table.setItem(r, 1, it)
            where = f"{info['count']} linha(s)"
            if info["outer"]:
                where += f", {info['outer']} no contorno externo"
            if info["layers"]:
                where += " · camada " + ", ".join(info["layers"][:3])
            self.table.setItem(r, 2, QTableWidgetItem(where))
            cb = QComboBox()
            for op in ops.COLOR_OPS:
                cb.addItem(ops.LABELS[op], op)
            cb.setCurrentIndex(max(0, cb.findData(current.get(k, ops.CUT))))
            cb.setToolTip("Corte atravessa a chapa. Vinco e gravação vetorial passam com pouca potência. "
                          "Gravação raster preenche a área (modo scan).")
            self.table.setCellWidget(r, 3, cb)
            self.combos.append(cb)
        self.table.resizeColumnsToContents()
        lay.addWidget(self.table)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Confirmar")
        bb.button(QDialogButtonBox.Cancel).setText("Cancelar")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def result_ops(self) -> dict:
        return {k: cb.currentData() for k, cb in zip(self.keys, self.combos)}
