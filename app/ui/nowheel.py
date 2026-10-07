"""Roda do mouse não altera campos numéricos, listas de escolha nem controles deslizantes.

Ao rolar os painéis de parâmetros, a roda passava por cima de um campo e mudava o valor sem querer.
Agora a roda sobre esses controles só rola o painel em volta (os valores mudam digitando, clicando
nas setinhas ou com o teclado).
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QAbstractScrollArea, QAbstractSpinBox, QApplication, QComboBox, QSlider

BLOCKED = (QAbstractSpinBox, QComboBox, QSlider)


class NoWheelFilter(QObject):
    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Wheel and isinstance(obj, BLOCKED):
            p = obj.parentWidget()
            while p is not None and not isinstance(p, QAbstractScrollArea):
                p = p.parentWidget()
            if p is not None:                     # passa a rolagem para o painel em volta
                QApplication.sendEvent(p.viewport(), ev)
            return True
        return False


_installed: NoWheelFilter | None = None


def install(app: QApplication) -> None:
    global _installed
    if _installed is None:
        _installed = NoWheelFilter(app)
        app.installEventFilter(_installed)
