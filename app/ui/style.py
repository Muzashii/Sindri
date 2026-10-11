"""Estilo base da aplicação (Fusion) com ajustes que o stylesheet do Qt não alcança.

Campos numéricos (QSpinBox/QDoubleSpinBox):
- o ``padding`` do stylesheet não chega ao editor interno; o texto ficava colado na borda.
  Aqui o editor recebe a mesma margem esquerda dos outros campos;
- números no formato brasileiro (vírgula decimal), como no resto da interface ("59,4 × 150,4 mm");
- quem digita ponto (teclado US, teclado numérico) continua funcionando: o ponto vira vírgula
  dentro do campo, em vez de ser lido como separador de milhar.

O ajuste é feito em ``polish`` (uma vez por campo, quando ganha o estilo), sem filtro global de eventos.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QLocale, QObject, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QAbstractSpinBox, QApplication, QDoubleSpinBox, QProxyStyle, QStyleFactory

LOCALE = QLocale(QLocale.Portuguese, QLocale.Brazil)
TEXT_MARGIN = 8          # = padding horizontal de QComboBox/QLineEdit no tema
_MARK = "_sindri_numeric"


class _DotToComma(QObject):
    """Ponto digitado num campo decimal vira a vírgula do locale (não separador de milhar)."""

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.KeyPress and ev.text() == "." and not ev.modifiers() & Qt.ControlModifier:
            QApplication.sendEvent(obj, QKeyEvent(QEvent.KeyPress, Qt.Key_Comma, ev.modifiers(), ","))
            return True
        return False


_dot_filter: _DotToComma | None = None


def prepare_numeric(w: QAbstractSpinBox) -> None:
    """Aplica margem, locale e conversão do ponto (pode repetir sem efeito)."""
    global _dot_filter
    if w.property(_MARK):
        return
    w.setProperty(_MARK, True)
    w.lineEdit().setTextMargins(TEXT_MARGIN, 0, 0, 0)
    w.setLocale(LOCALE)
    if isinstance(w, QDoubleSpinBox):
        if _dot_filter is None:
            _dot_filter = _DotToComma(QApplication.instance())
        w.lineEdit().installEventFilter(_dot_filter)


class SindriStyle(QProxyStyle):
    def __init__(self):
        super().__init__(QStyleFactory.create("Fusion"))
        self.setObjectName("fusion")           # apply_theme não troca o estilo de novo

    def polish(self, arg):
        if isinstance(arg, QAbstractSpinBox):
            prepare_numeric(arg)
        return super().polish(arg)
