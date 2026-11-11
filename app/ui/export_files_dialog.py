"""Janela do botão "Exportar": salvar o encaixe onde quiser e no formato que quiser, sem abrir o RDWorks."""
from __future__ import annotations

import os

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout)

from ..core.vector_export import FORMATS
from .prefs import settings

SCOPES = {
    "por_placa": "Um arquivo por placa",
    "todas": "Todas as placas num arquivo só (lado a lado)",
    "tela": "Só a placa da tela",
}


def file_names(base: str, fmt: str, scope: str, sheets: list[tuple[int, str]], report: bool) -> list[str]:
    """Nomes que vão ser gravados (para a prévia e para conferir se já existem)."""
    from ..core.dxf_export import sheet_file_suffix
    ext = FORMATS[fmt][0]
    if scope == "todas":
        names = [f"{base}_todas_placas{ext}"]
    else:
        names = [f"{base}{sheet_file_suffix(n, mat)}{ext}" for n, mat in sheets]
    if report and scope != "tela":
        names.append(f"{base}_relatorio.pdf")
    return names


class ExportFilesDialog(QDialog):
    def __init__(self, folder: str, base: str, sheets: list[tuple[int, str]], current: tuple[int, str] | None,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Exportar")
        self.setMinimumWidth(520)
        self.sheets, self.current = sheets, current
        st = settings()

        self.fmt = QComboBox()
        for key, (_ext, label) in FORMATS.items():
            self.fmt.addItem(label, key)
        self.fmt.setCurrentIndex(max(0, self.fmt.findData(st.value("export_files/format", "dxf2000"))))
        self.fmt.setToolTip("Todos os formatos saem na medida real (mm), com a cor de cada camada.\n"
                            "SVG, PDF e AI: arcos e círculos viram curvas exatas; textos do desenho ficam de fora.")
        self.scope = QComboBox()
        for key, label in SCOPES.items():
            if key == "tela" and current is None:
                continue
            text = label + (f" (Placa {current[0]})" if key == "tela" and current else "")
            self.scope.addItem(text, key)
        self.scope.setCurrentIndex(max(0, self.scope.findData(st.value("export_files/scope", "por_placa"))))
        self.outline = QCheckBox("Desenhar o contorno e o nº de cada placa (cinza)")
        self.outline.setChecked(st.value("export_files/outline", "true") == "true")
        self.report = QCheckBox("Incluir o relatório PDF (placas, peças, tempo)")
        self.report.setChecked(st.value("export_files/report", "false") == "true")

        self.folder = QLineEdit(folder)
        pick = QPushButton("Escolher…")
        pick.clicked.connect(self._choose)
        frow = QHBoxLayout()
        frow.addWidget(self.folder, 1)
        frow.addWidget(pick)
        self.base = QLineEdit(base)
        self.preview = QLabel()
        self.preview.setObjectName("Muted")
        self.preview.setWordWrap(True)

        form = QFormLayout()
        form.addRow("Formato", self.fmt)
        form.addRow("O que exportar", self.scope)
        form.addRow("", self.outline)
        form.addRow("", self.report)
        form.addRow("Pasta", frow)
        form.addRow("Nome", self.base)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.preview)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.button(QDialogButtonBox.Ok).setText("Exportar")
        box.button(QDialogButtonBox.Ok).setObjectName("primary")
        box.button(QDialogButtonBox.Cancel).setText("Cancelar")
        box.accepted.connect(self._accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        self.ok = box.button(QDialogButtonBox.Ok)
        for w in (self.fmt, self.scope):
            w.currentIndexChanged.connect(self._refresh)
        for w in (self.folder, self.base):
            w.textChanged.connect(self._refresh)
        for w in (self.outline, self.report):
            w.toggled.connect(self._refresh)
        self._refresh()

    def showEvent(self, e):
        super().showEvent(e)
        from .nowheel import protect
        protect(self)

    def _choose(self):
        d = QFileDialog.getExistingDirectory(self, "Pasta para exportar", self.folder.text() or os.getcwd())
        if d:
            self.folder.setText(d)

    def _sheets_for(self, scope):
        return [self.current] if scope == "tela" and self.current else self.sheets

    def _refresh(self, *_):
        scope = self.scope.currentData()
        self.outline.setVisible(scope == "todas")
        self.report.setEnabled(scope != "tela")
        base = self.base.text().strip()
        ok = bool(base) and bool(self.folder.text().strip())
        self.ok.setEnabled(ok)
        if not ok:
            self.preview.setText("Escolha a pasta e o nome.")
            return
        names = file_names(base, self.fmt.currentData(), scope, self._sheets_for(scope),
                           self.report.isChecked() and scope != "tela")
        shown = names[:4] + ([f"… e mais {len(names) - 4}"] if len(names) > 4 else [])
        self.preview.setText("Vai gravar: " + ", ".join(shown)
                             + ".\nSe algum nome já existir, acrescenta _2, _3… (nada é sobrescrito).")

    def _accept(self):
        st = settings()
        o = self.options()
        st.setValue("export_files/format", o["format"])
        st.setValue("export_files/scope", o["scope"])
        st.setValue("export_files/outline", "true" if o["outline"] else "false")
        st.setValue("export_files/report", "true" if o["report"] else "false")
        st.setValue("export_files/folder", o["folder"])
        self.accept()

    def options(self) -> dict:
        scope = self.scope.currentData()
        return {"format": self.fmt.currentData(), "scope": scope, "outline": self.outline.isChecked(),
                "report": self.report.isChecked() and scope != "tela",
                "folder": self.folder.text().strip(), "base": self.base.text().strip()}
