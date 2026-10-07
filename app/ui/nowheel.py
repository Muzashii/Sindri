"""Roda do mouse não altera campos numéricos, listas de escolha nem controles deslizantes.

Ao rolar os painéis de parâmetros, a roda passava por cima de um campo e mudava o valor sem querer.
Agora a roda sobre esses controles só rola o painel em volta (os valores mudam digitando, clicando
nas setinhas ou com o teclado).

O filtro é instalado só nesses controles (``protect``), não na aplicação inteira: um filtro global
passaria por todos os eventos do Qt (inclusive do navegador embutido) e deixava tudo mais lento.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QAbstractScrollArea, QAbstractSpinBox, QApplication, QComboBox, QSlider, QWidget

BLOCKED = (QAbstractSpinBox, QComboBox, QSlider)
_MARK = "_sindri_no_wheel"


class _NoWheel(QObject):
    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Wheel:
            p = obj.parentWidget()
            while p is not None and not isinstance(p, QAbstractScrollArea):
                p = p.parentWidget()
            if p is not None:                     # passa a rolagem para o painel em volta
                QApplication.sendEvent(p.viewport(), ev)
            return True
        return False


_filter: _NoWheel | None = None


def protect(root: QWidget) -> None:
    """Aplica em todos os campos/listas/controles deslizantes dentro de ``root`` (pode repetir)."""
    global _filter
    if _filter is None:
        _filter = _NoWheel(QApplication.instance())
    widgets = [root] if isinstance(root, BLOCKED) else []
    for cls in BLOCKED:
        widgets += root.findChildren(cls)
    for w in widgets:
        if not w.property(_MARK):
            w.setProperty(_MARK, True)
            w.installEventFilter(_filter)
