"""Parâmetros completos de uma camada de laser: modo, velocidade, potência mín./máx., passadas, scan, ar."""
from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
                               QLabel, QSpinBox, QVBoxLayout)

from ..core import laser
from ..core.operations import LABELS

AIR_CHOICES = [("", "não informado"), ("ligado", "Ligado"), ("fraco", "Fraco"), ("desligado", "Desligado")]


def _spin(lo, hi, step, dec, suffix, tip, value) -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setDecimals(dec)
    s.setSuffix(suffix)
    s.setToolTip(tip)
    s.setValue(float(value or 0))
    return s


class LayerParamsDialog(QDialog):
    """Edita um dicionário de parâmetros de camada (o mesmo formato do banco de materiais)."""

    def __init__(self, title: str, op: str, entry: dict, parent=None):
        super().__init__(parent)
        self.op = op
        self.setWindowTitle(f"Laser · {title}")
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        head = QLabel(f"<b>{title}</b> · {LABELS.get(op, op)}")
        head.setWordWrap(True)
        lay.addWidget(head)
        form = QFormLayout()
        self.mode = QComboBox()
        for key, name in laser.MODE_LABELS.items():
            self.mode.addItem(name, key)
        self.mode.setCurrentIndex(max(0, self.mode.findData(entry.get("mode") or laser.DEFAULT_MODE.get(op, "corte"))))
        self.mode.setToolTip("Corte: segue a linha. Scan: varre a área (gravação de preenchimento ou foto).\n"
                             "Scan+Corte: varre e depois contorna.")
        form.addRow("Modo", self.mode)
        self.speed = _spin(0, 2000, 1, 1, " mm/s", "Velocidade. 0 = não mexer no RDWorks.", entry.get("speed"))
        form.addRow("Velocidade", self.speed)
        self.pmax = _spin(0, 100, 1, 1, " %", "Potência máxima. 0 = não mexer no RDWorks.", entry.get("power"))
        form.addRow("Potência máx.", self.pmax)
        self.pmin = _spin(0, 100, 1, 1, " %",
                          "Potência mínima: a Ruida usa nos cantos e nas acelerações. Igual à máxima queima o "
                          "canto em MDF e derrete em acrílico.\n0 = automático (65% da máxima no modo corte, "
                          "igual à máxima no scan) — ponto de partida a validar em teste.", entry.get("power_min"))
        self.pmin.setSpecialValueText("automática")
        form.addRow("Potência mín.", self.pmin)
        self.passes = QSpinBox()
        self.passes.setRange(1, 10)
        self.passes.setValue(int(entry.get("passes") or 1))
        self.passes.setToolTip("MDF 6 mm e acrílico grosso costumam cortar melhor em 2 passadas mais rápidas.")
        form.addRow("Passadas", self.passes)
        self.interval = _spin(0.01, 1.0, 0.01, 3, " mm", "Distância entre as linhas do scan. Foto em madeira: "
                              "0,08–0,1 mm (ponto de partida).", entry.get("interval") or 0.1)
        form.addRow("Intervalo do scan", self.interval)
        self.bidir = QCheckBox("Scan nos dois sentidos (bidirecional)")
        self.bidir.setChecked(bool(entry.get("bidirectional", True)))
        form.addRow(self.bidir)
        self.air = QComboBox()
        for key, name in AIR_CHOICES:
            self.air.addItem(name, key)
        self.air.setCurrentIndex(max(0, self.air.findData(entry.get("air") or "")))
        self.air.setToolTip("Sopro (air assist): ligado no corte, fraco ou desligado na gravação fina.\n"
                            "Informativo: aparece na conferência e no relatório.")
        form.addRow("Sopro (air assist)", self.air)
        lay.addLayout(form)
        note = QLabel("O Sindri grava no RDWorks a velocidade e as potências mín./máx. (tubo 1). Modo, passadas, "
                      "intervalo e sopro aparecem na conferência camada a camada para você ajustar no RDWorks.")
        note.setWordWrap(True)
        note.setObjectName("Muted")
        lay.addWidget(note)
        self.mode.currentIndexChanged.connect(self._sync)
        self._sync()
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Salvar")
        bb.button(QDialogButtonBox.Cancel).setText("Cancelar")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _sync(self):
        scan = self.mode.currentData() in ("scan", "scan_corte")
        self.interval.setEnabled(scan)
        self.bidir.setEnabled(scan)

    def values(self) -> dict:
        out = {"mode": self.mode.currentData(), "speed": round(self.speed.value(), 2),
               "power": round(self.pmax.value(), 1), "power_min": round(self.pmin.value(), 1),
               "passes": int(self.passes.value()), "air": self.air.currentData()}
        if self.mode.currentData() in ("scan", "scan_corte"):
            out.update(interval=round(self.interval.value(), 3), bidirectional=self.bidir.isChecked())
        return out
