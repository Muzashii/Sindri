"""Componentes de apresentação compartilhados, sem regras de negócio."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QToolButton, QSizePolicy


class ElidedLabel(QLabel):
    """Texto de uma linha que ocupa o espaço livre e corta com "…" quando não cabe (o texto inteiro
    fica na dica; o nome acessível é anunciado por quem atualiza o texto). Não força a largura mínima de quem o contém."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(0)
        self.setText(text)

    def full_text(self) -> str:
        return self._full

    def setText(self, text: str):  # noqa: N802 (API do Qt)
        self._full = text or ""
        self.setToolTip(self._full)        # nome acessível: quem anuncia é accessibility.announce
        self._elide()

    def _elide(self):
        width = max(0, self.contentsRect().width())
        shown = self.fontMetrics().elidedText(self._full, Qt.ElideRight, width) if width > 0 else self._full
        super().setText(shown)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        hint.setWidth(0)
        return hint


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
