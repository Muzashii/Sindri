"""Associate labels with Qt controls; no HTML/ARIA assumptions."""
from PySide6.QtWidgets import (QWidget, QFormLayout, QLabel, QAbstractButton, QLineEdit,
                              QAbstractSpinBox, QComboBox, QSlider, QTabBar)
from PySide6.QtGui import QAccessible, QAccessibleEvent, QTextDocument


def configure(root):
    for form in root.findChildren(QFormLayout):
        for row in range(form.rowCount()):
            label_item = form.itemAt(row, QFormLayout.LabelRole)
            field_item = form.itemAt(row, QFormLayout.FieldRole)
            label = label_item.widget() if label_item else None
            field = field_item.widget() if field_item else None
            if isinstance(label, QLabel) and isinstance(field, QWidget):
                label.setBuddy(field)
                if not field.accessibleName():
                    field.setAccessibleName(label.text())
    for field in root.findChildren(QWidget):
        if isinstance(field, (QAbstractButton, QLineEdit, QAbstractSpinBox, QComboBox, QSlider, QTabBar)):
            if not field.accessibleName() and field.toolTip():
                field.setAccessibleName(field.toolTip().split("\n")[0])


def announce(label, text):
    document = QTextDocument()
    document.setHtml(text)
    text = document.toPlainText()
    if label.accessibleName() == text:
        return
    label.setAccessibleName(text)
    if QAccessible.isActive():
        QAccessible.updateAccessibility(QAccessibleEvent(label, QAccessible.Event.NameChanged))
