"""Tema visual (claro/escuro) com tokens de cor compartilhados pela interface e pelo canvas."""
from __future__ import annotations

from PySide6.QtGui import QColor, QPalette, QFont
from PySide6.QtWidgets import QApplication

LIGHT = {
    "bg": "#eef0f4", "surface": "#ffffff", "surface2": "#f5f6f8", "surface3": "#e9ecf1",
    "border": "#dde1e7", "text": "#1d2330", "muted": "#6b7383", "accent": "#2563eb",
    "accent_hover": "#1d4ed8", "accent_soft": "#e3ecfd", "success": "#15803d",
    "success_hover": "#116a32", "danger": "#dc2626", "danger_soft": "#fde8e8", "warn": "#b45309",
    "warn_soft": "#fdf1dc", "canvas": "#e4e7ec", "sheet": "#fbfbfc", "sheet_border": "#b9c0cc",
    "grid": "#0000000f", "grid2": "#00000022", "part_fill": "#2563eb22", "shadow": "#00000026",
}
DARK = {
    "bg": "#121419", "surface": "#1a1d24", "surface2": "#21252e", "surface3": "#2a2f3a",
    "border": "#2c313c", "text": "#e5e7eb", "muted": "#8b93a3", "accent": "#3b82f6",
    "accent_hover": "#2f6fdb", "accent_soft": "#1e2b45", "success": "#16a34a",
    "success_hover": "#15803d", "danger": "#ef4444", "danger_soft": "#3a1d22", "warn": "#f59e0b",
    "warn_soft": "#3a2d14", "canvas": "#0f1115", "sheet": "#262b35", "sheet_border": "#4a5262",
    "grid": "#ffffff0d", "grid2": "#ffffff1f", "part_fill": "#3b82f633", "shadow": "#00000080",
}

_current = dict(LIGHT)
_dark = False


MATERIAL_COLORS = ["#2563eb", "#ea580c", "#16a34a", "#9333ea", "#db2777", "#0891b2", "#ca8a04"]


def material_color(material: str) -> QColor:
    """Cor fixa por material (3mm azul, 6mm laranja…), igual em toda a interface."""
    import re
    if not material:
        return QColor(_current["muted"])
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*mm", material.lower())
    fixed = {"3": 0, "6": 1, "2": 2, "4": 3, "5": 4, "10": 5, "1": 6}
    if m and m.group(1) in fixed:
        idx = fixed[m.group(1)]
    else:
        idx = sum(map(ord, material)) % len(MATERIAL_COLORS)
    return QColor(MATERIAL_COLORS[idx])


def tokens() -> dict:
    return _current


def is_dark() -> bool:
    return _dark


def qcolor(name: str) -> QColor:
    """Cor de um token; aceita #rrggbbaa."""
    v = _current[name]
    if len(v) == 9:
        c = QColor(v[:7])
        c.setAlpha(int(v[7:], 16))
        return c
    return QColor(v)


def apply_theme(app: QApplication, dark: bool) -> None:
    global _current, _dark
    _dark = dark
    _current = dict(DARK if dark else LIGHT)
    t = _current
    app.setStyle("Fusion")
    f = QFont("Segoe UI")
    f.setPointSize(9)
    app.setFont(f)
    p = QPalette()
    p.setColor(QPalette.Window, QColor(t["bg"]))
    p.setColor(QPalette.WindowText, QColor(t["text"]))
    p.setColor(QPalette.Base, QColor(t["surface"]))
    p.setColor(QPalette.AlternateBase, QColor(t["surface2"]))
    p.setColor(QPalette.Text, QColor(t["text"]))
    p.setColor(QPalette.Button, QColor(t["surface2"]))
    p.setColor(QPalette.ButtonText, QColor(t["text"]))
    p.setColor(QPalette.Highlight, QColor(t["accent"]))
    p.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.ToolTipBase, QColor(t["surface"]))
    p.setColor(QPalette.ToolTipText, QColor(t["text"]))
    p.setColor(QPalette.PlaceholderText, QColor(t["muted"]))
    p.setColor(QPalette.Mid, QColor(t["border"]))
    for role in (QPalette.Text, QPalette.ButtonText, QPalette.WindowText):
        p.setColor(QPalette.Disabled, role, QColor(t["muted"]))
    app.setPalette(p)
    from .icons import image_file
    t["check_img"] = image_file("check", "#ffffff")
    app.setStyleSheet(STYLE % t)


STYLE = """
* { outline: none; }
QMainWindow, QDialog { background: %(bg)s; }
QToolTip { background: %(surface)s; color: %(text)s; border: 1px solid %(border)s; padding: 6px 8px;
           border-radius: 6px; }

QMenuBar { background: %(surface)s; color: %(text)s; border-bottom: 1px solid %(border)s; }
QMenuBar::item { padding: 5px 10px; background: transparent; border-radius: 4px; }
QMenuBar::item:selected { background: %(surface3)s; }
QMenu { background: %(surface)s; color: %(text)s; border: 1px solid %(border)s; padding: 4px;
        border-radius: 8px; }
QMenu::item { padding: 6px 22px 6px 14px; border-radius: 5px; }
QMenu::item:selected { background: %(accent_soft)s; color: %(text)s; }
QMenu::item:disabled { color: %(muted)s; }
QMenu::separator { height: 1px; background: %(border)s; margin: 4px 6px; }

QFrame#TopBar { background: %(surface)s; border-bottom: 1px solid %(border)s; }
QFrame#Card { background: %(surface)s; border: 1px solid %(border)s; border-radius: 10px; }
QFrame#VSep { background: %(border)s; }
QLabel#Logo { font-size: 14pt; font-weight: 700; color: %(text)s; }
QLabel#LogoMark { background: %(accent)s; color: white; border-radius: 7px; font-weight: 800;
                  font-size: 10pt; }
QLabel#SectionTitle { font-size: 10pt; font-weight: 700; color: %(text)s; letter-spacing: 0.5px; }
QLabel#Muted, QLabel#hint { color: %(muted)s; }
QLabel#SectionHead { font-size: 10pt; font-weight: 700; color: %(text)s; }
QLabel#SectionIcon { background: %(accent_soft)s; border-radius: 8px; }
QFrame#Metrics { background: %(surface2)s; border: 1px solid %(border)s; border-radius: 9px; }
QLabel#MetricValue { font-size: 13pt; font-weight: 800; color: %(text)s; }
QLabel#MetricBig { font-size: 15pt; font-weight: 800; color: %(success)s; }
QLabel#MetricCaption { color: %(muted)s; font-size: 8pt; }
QLabel#State { border-radius: 9px; padding: 3px 10px; font-weight: 700; background: %(surface3)s;
               color: %(muted)s; }
QLabel#State[kind="run"] { background: %(accent_soft)s; color: %(accent)s; }
QLabel#State[kind="ok"] { background: %(surface3)s; color: %(success)s; }
QLabel#State[kind="warn"] { background: %(danger_soft)s; color: %(danger)s; }
QToolButton#Danger:hover { background: %(danger_soft)s; }
QLabel#FieldLabel { color: %(muted)s; }

QPushButton { background: %(surface2)s; color: %(text)s; border: 1px solid %(border)s;
              border-radius: 7px; padding: 7px 14px; font-weight: 600; }
QPushButton:hover { background: %(surface3)s; }
QPushButton:pressed { background: %(border)s; }
QPushButton:disabled { color: %(muted)s; background: %(surface2)s; border-color: %(border)s; }
QPushButton#primary { background: %(accent)s; color: white; border: 1px solid %(accent)s; }
QPushButton#primary:hover { background: %(accent_hover)s; }
QPushButton#primary:disabled { background: %(surface3)s; color: %(muted)s; border-color: %(border)s; }
QPushButton#success { background: %(success)s; color: white; border: 1px solid %(success)s; }
QPushButton#success:hover { background: %(success_hover)s; }
QPushButton#success:disabled { background: %(surface3)s; color: %(muted)s; border-color: %(border)s; }
QPushButton#ghost { background: transparent; border: 1px solid transparent; font-weight: 500; }
QPushButton#ghost:hover { background: %(surface3)s; }

QToolButton { background: transparent; color: %(text)s; border: 1px solid transparent;
              border-radius: 7px; padding: 5px; }
QToolButton:hover { background: %(surface3)s; }
QToolButton:checked { background: %(accent_soft)s; border-color: %(accent)s; }
QToolButton:disabled { color: %(muted)s; }

QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {
    background: %(surface2)s; color: %(text)s; border: 1px solid %(border)s; border-radius: 7px;
    padding: 5px 8px; min-height: 18px; selection-background-color: %(accent)s; }
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QLineEdit:hover { border-color: %(muted)s; }
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus { border-color: %(accent)s; }
QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled { color: %(muted)s; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView { background: %(surface)s; color: %(text)s; border: 1px solid %(border)s;
    selection-background-color: %(accent_soft)s; selection-color: %(text)s; padding: 4px; }
QSpinBox::up-button, QDoubleSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::down-button {
    width: 16px; border: none; background: transparent; }

QCheckBox { color: %(text)s; spacing: 8px; }
QCheckBox::indicator { width: 16px; height: 16px; border-radius: 4px; border: 1px solid %(muted)s;
                       background: %(surface)s; }
QCheckBox::indicator:checked { background: %(accent)s; border-color: %(accent)s;
    image: url(%(check_img)s); }
QCheckBox:disabled { color: %(muted)s; }

QGroupBox { background: %(surface)s; border: 1px solid %(border)s; border-radius: 10px;
            margin-top: 14px; padding: 12px 10px 8px 10px; font-weight: 700; color: %(muted)s; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; top: 2px; padding: 0 2px;
                   color: %(muted)s; }
QGroupBox::indicator { width: 14px; height: 14px; border-radius: 3px; border: 1px solid %(muted)s; }
QGroupBox::indicator:checked { background: %(accent)s; border-color: %(accent)s; }

QListWidget { background: transparent; border: none; }
QListWidget::item { border: none; margin: 0 0 6px 0; padding: 0; background: transparent; }
QListWidget::item:selected { background: transparent; }
QFrame#PartRow { background: %(surface2)s; border: 1px solid %(border)s; border-radius: 9px; }
QFrame#PartRow[selected="true"] { border: 1.5px solid %(accent)s; background: %(accent_soft)s; }
QFrame#PartRow[warn="true"] { border-left: 3px solid %(warn)s; }
QLabel#PartName { font-weight: 700; color: %(text)s; }
QLabel#PartSub { color: %(muted)s; font-size: 8pt; }
QFrame#RequestCard { background: %(accent_soft)s; border: 1px solid %(accent)s; border-radius: 10px; }
QLabel#ReqTitle { font-size: 11pt; font-weight: 800; color: %(text)s; }
QLabel#ReqLine { color: %(text)s; }
QLabel#MatChip { border-radius: 8px; padding: 1px 8px; font-size: 8pt; font-weight: 700; color: white; }
QLabel#Badge { background: %(warn_soft)s; color: %(warn)s; border-radius: 6px; padding: 1px 6px;
               font-size: 8pt; font-weight: 700; }
QLabel#Thumb { background: %(surface)s; border: 1px solid %(border)s; border-radius: 7px; }

QFrame#Banner { border-radius: 8px; }
QFrame#Banner[kind="info"] { background: %(accent_soft)s; border: 1px solid %(accent)s; }
QFrame#Banner[kind="warn"] { background: %(warn_soft)s; border: 1px solid %(warn)s; }
QLabel#BannerText { color: %(text)s; }

QTabBar { background: transparent; }
QTabBar::tab { background: transparent; color: %(muted)s; padding: 7px 16px; margin: 4px 2px;
               border-radius: 7px; font-weight: 600; }
QTabBar::tab:selected { background: %(surface3)s; color: %(text)s; }
QTabBar::tab:hover:!selected { color: %(text)s; }

QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: %(surface3)s; border-radius: 4px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: %(muted)s; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: %(surface3)s; border-radius: 4px; min-width: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QSplitter::handle { background: transparent; width: 8px; }

QStatusBar { background: %(surface)s; color: %(muted)s; border-top: 1px solid %(border)s; }
QStatusBar::item { border: none; }
QLabel#Chip { background: %(surface2)s; border: 1px solid %(border)s; border-radius: 10px;
              padding: 2px 10px; color: %(text)s; font-weight: 600; }
QLabel#Chip[kind="warn"] { background: %(danger_soft)s; border-color: %(danger)s; color: %(danger)s; }
QProgressBar { background: %(surface3)s; border: none; border-radius: 5px; height: 10px;
               max-height: 10px; text-align: center; color: transparent; }
QProgressBar::chunk { background: %(success)s; border-radius: 5px; }

QTableWidget { background: %(surface)s; border: 1px solid %(border)s; border-radius: 8px;
               gridline-color: %(border)s; color: %(text)s; }
QHeaderView::section { background: %(surface2)s; color: %(muted)s; border: none; padding: 6px;
                       font-weight: 700; }
QDialogButtonBox QPushButton { min-width: 90px; }
"""
