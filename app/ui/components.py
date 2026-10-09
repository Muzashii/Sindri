"""Componentes de apresentação compartilhados, sem regras de negócio."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QToolButton, QSizePolicy


class SectionToggle(QToolButton):
    """Cabeçalho acessível que recolhe conteúdo sem perder seus valores."""
    def __init__(self, title, content, expanded=True):
        super().__init__()
        self.setObjectName("SectionToggle")
        self.setText(title)
        self.setCheckable(True)
        self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAccessibleName(title)
        self.setAccessibleDescription("Pressione Espaço para expandir ou recolher esta seção.")
        self.setToolTip("Expandir ou recolher " + title.lower())
        self.content = content
        self.toggled.connect(self._toggle)
        self.setChecked(expanded)
        self._toggle(expanded)

    def _toggle(self, expanded):
        self.content.setVisible(expanded)
        self.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
