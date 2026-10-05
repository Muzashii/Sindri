"""Diálogos: exportação para o RDWorks e gerenciamento de presets de placa."""
from __future__ import annotations

import json
import os

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

DEFAULT_PRESETS = [
    {"name": "MDF 3mm 600×400", "w": 600, "h": 400},
    {"name": "MDF 3mm 900×600", "w": 900, "h": 600},
    {"name": "Acrílico 3mm 600×400", "w": 600, "h": 400},
    {"name": "Acrílico 1000×600", "w": 1000, "h": 600},
]


def settings() -> QSettings:
    return QSettings("LabMaker", "DXFNest")


def load_presets() -> list[dict]:
    raw = settings().value("presets", "")
    try:
        data = json.loads(raw) if raw else None
        if isinstance(data, list) and data:
            return [{"name": str(d["name"]), "w": float(d["w"]), "h": float(d["h"])} for d in data]
    except Exception:
        pass
    return [dict(d) for d in DEFAULT_PRESETS]


def save_presets(presets: list[dict]):
    settings().setValue("presets", json.dumps(presets, ensure_ascii=False))


class PresetsDialog(QDialog):
    def __init__(self, presets: list[dict], current_w: float, current_h: float, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Placas salvas")
        self.resize(460, 340)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Edite os nomes e tamanhos (mm). As placas aparecem no menu do topo."))
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Nome", "Largura", "Altura"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        lay.addWidget(self.table)
        for p in presets:
            self._add(p["name"], p["w"], p["h"])
        row = QHBoxLayout()
        add = QPushButton("Adicionar a placa atual")
        add.clicked.connect(lambda: self._add(f"Placa {current_w:g}×{current_h:g}", current_w, current_h))
        rem = QPushButton("Remover selecionada")
        rem.clicked.connect(self._remove)
        row.addWidget(add)
        row.addWidget(rem)
        row.addStretch(1)
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Cancel).setText("Cancelar")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _add(self, name, w, h):
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(str(name)))
        for c, v in ((1, w), (2, h)):
            s = QDoubleSpinBox()
            s.setRange(10, 5000)
            s.setDecimals(1)
            s.setSuffix(" mm")
            s.setValue(float(v))
            self.table.setCellWidget(r, c, s)

    def _remove(self):
        r = self.table.currentRow()
        if r >= 0:
            self.table.removeRow(r)

    def presets(self) -> list[dict]:
        out = []
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            name = it.text().strip() if it else ""
            if not name:
                continue
            out.append({"name": name, "w": self.table.cellWidget(r, 1).value(),
                        "h": self.table.cellWidget(r, 2).value()})
        return out


class ExportDialog(QDialog):
    def __init__(self, folder: str, base: str, n_sheets: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Exportar para RDWorks")
        self.setMinimumWidth(480)
        st = settings()
        lay = QVBoxLayout(self)
        form = QFormLayout()
        frow = QHBoxLayout()
        self.folder = QLineEdit(folder)
        btn = QPushButton("Escolher…")
        btn.clicked.connect(self._choose)
        frow.addWidget(self.folder, 1)
        frow.addWidget(btn)
        form.addRow("Pasta", frow)
        self.base = QLineEdit(base)
        self.base.setToolTip("Gera nome_todas_placas.dxf (todas as placas organizadas) e nome_relatorio.pdf")
        form.addRow("Nome", self.base)
        self.version = QComboBox()
        self.version.addItem("R2000 (recomendado)", "R2000")
        self.version.addItem("R12 (compatibilidade máxima)", "R12")
        self.version.setToolTip("Versão do DXF. Se o RDWorks não abrir corretamente, tente a outra.")
        self.version.setCurrentIndex(1 if st.value("export/version", "R2000") == "R12" else 0)
        form.addRow("Versão do DXF", self.version)
        lay.addLayout(form)
        self.outline = QCheckBox("Contorno e nº de cada placa (“PLACA 1”, “PLACA 2”…) na camada PLACA, cinza")
        self.outline.setToolTip("Mostra no RDWorks qual placa é qual. Na camada cinza (PLACA), marque saída = NÃO "
                                "antes de cortar, para o laser não passar por ela!")
        self.outline.setChecked(st.value("export/outline2", "true") == "true")
        self.inner = QCheckBox("Cortar contornos internos antes dos externos")
        self.inner.setToolTip("Evita que a peça se solte e se mova antes de os furos serem cortados.")
        self.inner.setChecked(st.value("export/inner", "true") == "true")
        self.path = QCheckBox("Ordenar peças pelo caminho mais curto")
        self.path.setChecked(st.value("export/path", "true") == "true")
        self.open_rd = QCheckBox("Abrir no RDWorks depois de exportar")
        self.open_rd.setToolTip("Abre no RDWorks o arquivo com todas as placas organizadas lado a lado "
                                "(todos os materiais, na ordem Placa 1, 2, 3… do relatório e do checklist).")
        self.open_rd.setChecked(st.value("export/open_rdworks", "true") == "true")
        info = QLabel("Saem 2 arquivos: <b>todas as placas</b> organizadas num DXF (o que vai para o RDWorks) "
                      "e o <b>relatório PDF</b> com o desenho de cada placa, de quem é cada peça e a "
                      "lista para marcar o que já foi cortado.")
        info.setWordWrap(True)
        info.setObjectName("Muted")
        lay.addWidget(info)
        for c in (self.outline, self.inner, self.path, self.open_rd):
            lay.addWidget(c)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Exportar")
        bb.button(QDialogButtonBox.Cancel).setText("Cancelar")
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _choose(self):
        d = QFileDialog.getExistingDirectory(self, "Pasta de destino", self.folder.text())
        if d:
            self.folder.setText(d)

    def _accept(self):
        st = settings()
        st.setValue("export/version", self.version.currentData())
        st.setValue("export/outline2", "true" if self.outline.isChecked() else "false")
        st.setValue("export/inner", "true" if self.inner.isChecked() else "false")
        st.setValue("export/path", "true" if self.path.isChecked() else "false")
        st.setValue("export/open_rdworks", "true" if self.open_rd.isChecked() else "false")
        if not self.base.text().strip():
            self.base.setText("projeto")
        self.accept()

    def options(self) -> dict:
        return {"folder": self.folder.text().strip() or os.getcwd(),
                "base": self.base.text().strip() or "projeto",
                "version": self.version.currentData(),
                "outline": self.outline.isChecked(),
                "inner": self.inner.isChecked(), "path": self.path.isChecked(),
                "open_rdworks": self.open_rd.isChecked()}


class CleanupDialog(QDialog):
    """Escolher o que apagar: solicitações baixadas, relatórios e arquivos de corte exportados."""

    def __init__(self, groups: list[tuple[str, str, list[str], bool]], parent=None):
        """groups: [(chave, título, arquivos, marcado por padrão)]"""
        from ..core.cleanup import human, total_size
        super().__init__(parent)
        self.setWindowTitle("Limpar arquivos do Sindri")
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        intro = QLabel("Os arquivos escolhidos vão para a <b>Lixeira</b> (dá para recuperar de lá). "
                       "Projetos salvos (.sindri) que usam solicitações apagadas não vão mais abrir.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.checks: dict[str, QCheckBox] = {}
        self.files: dict[str, list[str]] = {}
        for key, title, files, default in groups:
            cb = QCheckBox(f"{title}  —  {len(files)} arquivo(s), {human(total_size(files))}")
            cb.setChecked(default and bool(files))
            cb.setEnabled(bool(files))
            if files:
                cb.setToolTip("\n".join(files[:25]) + ("\n…" if len(files) > 25 else ""))
            self.checks[key] = cb
            self.files[key] = files
            lay.addWidget(cb)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok = bb.button(QDialogButtonBox.Ok)
        self.ok.setText("Mover para a Lixeira")
        bb.button(QDialogButtonBox.Cancel).setText("Cancelar")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        for cb in self.checks.values():
            cb.toggled.connect(self._update)
        self._update()

    def _update(self, *_):
        self.ok.setEnabled(any(cb.isChecked() for cb in self.checks.values()))

    def chosen(self) -> list[str]:
        out = []
        for k, cb in self.checks.items():
            if cb.isChecked():
                out += self.files[k]
        return out
