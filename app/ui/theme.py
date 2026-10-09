"""Tema visual (claro/escuro) com tokens de cor compartilhados pela interface e pelo canvas."""
from __future__ import annotations

from PySide6.QtGui import QColor, QPalette, QFont
from PySide6.QtWidgets import QApplication

LIGHT = {
    "accent_text": "#1d4ed8", "success_text": "#166534",
    "bg": "#edf1f6", "surface": "#ffffff", "surface2": "#f5f6f8", "surface3": "#e9ecf1",
    "border": "#d4dbe5", "control_border": "#7b8799", "focus": "#1d4ed8", "text": "#172033", "muted": "#566277", "accent": "#2563eb",
    "accent_hover": "#1d4ed8", "accent_soft": "#e3ecfd", "done_fg": "#166534", "done_bg": "#dcfce7", "success": "#15803d",
    "success_hover": "#116a32", "danger": "#b91c1c", "danger_hover": "#991b1b", "danger_soft": "#fde8e8", "warn": "#b45309",
    "warn_soft": "#fdf1dc", "canvas": "#e4e7ec", "sheet": "#fbfbfc", "sheet_border": "#b9c0cc",
    "grid": "#0000000f", "grid2": "#00000022", "part_fill": "#2563eb22", "shadow": "#00000026",
}
DARK = {
    "accent_text": "#93c5fd", "success_text": "#86efac",
    "bg": "#11161e", "surface": "#1a1d24", "surface2": "#21252e", "surface3": "#2a2f3a",
    "border": "#343d4b", "control_border": "#718096", "focus": "#93c5fd", "text": "#edf1f7", "muted": "#a5afbf", "accent": "#2563eb",
    "accent_hover": "#1d4ed8", "accent_soft": "#1e2b45", "done_fg": "#86efac", "done_bg": "#14331f", "success": "#15803d",
    "success_hover": "#15803d", "danger": "#fca5a5", "danger_hover": "#991b1b", "danger_soft": "#3a1d22", "warn": "#f59e0b",
    "warn_soft": "#3a2d14", "canvas": "#0f1115", "sheet": "#262b35", "sheet_border": "#4a5262",
    "grid": "#ffffff0d", "grid2": "#ffffff1f", "part_fill": "#3b82f633", "shadow": "#00000080",
}

NEUTRALS = {
    "neutral_50": "#f8fafc", "neutral_100": "#f1f5f9", "neutral_200": "#e2e8f0",
    "neutral_300": "#cbd5e1", "neutral_400": "#94a3b8", "neutral_500": "#64748b",
    "neutral_600": "#475569", "neutral_700": "#334155", "neutral_800": "#1e293b",
    "neutral_900": "#0f172a", "neutral_950": "#020617",
}
METRICS = {
    "space_xs": 4, "space_sm": 8, "space_md": 12, "space_lg": 16,
    "space_xl": 24, "space_2xl": 32,
    "radius_sm": 4, "radius_md": 8, "radius_lg": 12,
    "font_body": 9, "font_small": 9, "font_section": 10, "font_title": 13, "font_hero": 15,
    "panel_parts": 312, "panel_parts_min": 276, "panel_params": 320, "panel_photo": 340,
    "panel_box": 392, "panel_box_compact": 360,
    "thumb": 44, "control_height": 24,
}
for palette in (LIGHT, DARK):
    palette.update(NEUTRALS)
    palette.update(METRICS)
    palette.update(on_accent="#ffffff", danger_button="#b91c1c")

_current = dict(LIGHT)
_dark = False


MATERIAL_COLORS = ["#2563eb", "#c2410c", "#15803d", "#9333ea", "#db2777", "#0e7490", "#a16207"]


def material_color(material: str) -> QColor:
    """Cor fixa por material, igual em toda a interface. MDF: 3mm azul, 6mm laranja…
    Outros materiais (acrílico, compensado…) ganham cores próprias, nunca as do MDF."""
    import re
    if not material:
        return QColor(_current["muted"])
    low = material.lower()
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*mm", low)
    thick = m.group(1).replace(",", ".") if m else ""
    if "mdf" in low or not re.search(r"[a-zà-ú]{3,}", re.sub(r"\d+\s*mm", "", low)):
        fixed = {"3": 0, "6": 1, "2": 2, "4": 3, "5": 4, "10": 5, "1": 6, "9": 6, "15": 3}
        if thick in fixed:
            return QColor(MATERIAL_COLORS[fixed[thick]])
    others = ["#0f766e", "#be185d", "#7c3aed", "#4d7c0f", "#b45309", "#0369a1", "#a21caf", "#4d7c0f"]
    if "acr" in low:                       # acrílico: família rosa/roxa por espessura
        fam = {"2": "#db2777", "3": "#be185d", "4": "#a21caf", "5": "#7c3aed", "6": "#6d28d9"}
        if thick in fam:
            return QColor(fam[thick])
    return QColor(others[sum(map(ord, low)) % len(others)])


def metric(name: str) -> int:
    """Medida compartilhada do design system (pixels, exceto fonte em pontos)."""
    return METRICS[name]


def tokens() -> dict:
    return _current


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
    if app.style().objectName().lower() != "fusion":    # trocar o estilo é caro: só na 1ª vez
        app.setStyle("Fusion")
    f = QFont("Segoe UI")
    f.setPointSize(metric("font_body"))
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
QPushButton:focus, QToolButton:focus, QCheckBox:focus, QTabBar::tab:focus,
QListWidget:focus, QTableWidget:focus, QSlider:focus {
    border: 2px solid %(focus)s;
}
QToolButton { min-width: 24px; min-height: 24px; }
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
QFrame#Card { background: %(surface)s; border: 1px solid %(border)s; border-radius: %(radius_lg)spx; }
QFrame#VSep { background: %(border)s; }
QLabel#Logo { font-size: %(font_title)spt; font-weight: 700; color: %(text)s; }
QLabel#LogoMark { background: %(accent)s; color: white; border-radius: %(radius_md)spx; font-weight: 800;
                  font-size: %(font_section)spt; }
QLabel#SectionTitle { font-size: %(font_section)spt; font-weight: 700; color: %(text)s; letter-spacing: 0.5px; }
QLabel#Muted, QLabel#hint { color: %(muted)s; }
QLabel#SectionHead { font-size: %(font_section)spt; font-weight: 700; color: %(text)s; }
QLabel#SectionIcon { background: %(accent_soft)s; border-radius: 8px; }
QFrame#Metrics { background: %(surface2)s; border: 1px solid %(border)s; border-radius: 9px; }
QLabel#MetricValue { font-size: 13pt; font-weight: 800; color: %(text)s; }
QLabel#MetricBig { font-size: 15pt; font-weight: 800; color: %(text)s; }
QLabel#MetricCaption { color: %(muted)s; font-size: %(font_small)spt; }
QLabel#State { border-radius: 9px; padding: 3px 10px; font-weight: 700; background: %(surface3)s;
               color: %(muted)s; }
QLabel#State[kind="run"] { background: %(accent_soft)s; color: %(accent_text)s; }
QLabel#State[kind="ok"] { background: %(done_bg)s; color: %(success_text)s; }
QLabel#State[kind="warn"] { background: %(danger_soft)s; color: %(danger)s; }
QToolButton#Danger:hover { background: %(danger_soft)s; }
QLabel#FieldLabel { color: %(muted)s; }

QPushButton { background: %(surface2)s; color: %(text)s; border: 2px solid %(border)s;
              border-radius: %(radius_md)spx; padding: 7px 14px; font-weight: 600; }
QPushButton#ToolbarAction { padding: 7px 6px; }
QPushButton:hover { background: %(surface3)s; }
QPushButton:pressed { background: %(border)s; }
QPushButton:disabled { color: %(muted)s; background: %(surface2)s; border-color: %(border)s; }
QPushButton#primary { background: %(accent)s; color: white; border: 2px solid %(accent)s; }
QPushButton#primary:hover { background: %(accent_hover)s; }
QPushButton#primary:disabled { background: %(surface3)s; color: %(muted)s; border-color: %(border)s; }
QPushButton#success { background: %(success)s; color: white; border: 2px solid %(success)s; }
QPushButton#success:hover { background: %(success_hover)s; }
QPushButton#success:disabled { background: %(surface3)s; color: %(muted)s; border-color: %(border)s; }
QPushButton#ghost { background: transparent; border: 2px solid transparent; font-weight: 500; }
QPushButton#ghost:hover { background: %(surface3)s; }

QToolButton { background: transparent; color: %(text)s; border: 2px solid transparent;
              border-radius: %(radius_md)spx; padding: 5px; }
QToolButton:hover { background: %(surface3)s; }
QToolButton:checked { background: %(accent_soft)s; border-color: %(accent_text)s; }
QToolButton:disabled { color: %(muted)s; }

QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {
    background: %(surface2)s; color: %(text)s; border: 1px solid %(control_border)s; border-radius: %(radius_md)spx;
    padding: 5px 8px; min-height: 18px; selection-background-color: %(accent)s; }
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QLineEdit:hover { border-color: %(muted)s; }
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus { border-color: %(accent_text)s; }
QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled { color: %(muted)s; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView { background: %(surface)s; color: %(text)s; border: 1px solid %(border)s;
    selection-background-color: %(accent_soft)s; selection-color: %(text)s; padding: 4px; }
QSpinBox::up-button, QDoubleSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::down-button {
    width: 16px; border: none; background: transparent; }

QCheckBox { color: %(text)s; spacing: 8px; }
QCheckBox::indicator { width: 16px; height: 16px; border-radius: 4px; border: 1px solid %(muted)s;
                       background: %(surface)s; }
QCheckBox::indicator:checked { background: %(accent)s; border-color: %(accent_text)s;
    image: url(%(check_img)s); }
QCheckBox:disabled { color: %(muted)s; }

QGroupBox { background: %(surface)s; border: 1px solid %(border)s; border-radius: %(radius_lg)spx;
            margin-top: 14px; padding: 12px 10px 8px 10px; font-weight: 700; color: %(muted)s; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; top: 2px; padding: 0 2px;
                   color: %(muted)s; }
QGroupBox::indicator { width: 14px; height: 14px; border-radius: 3px; border: 1px solid %(muted)s; }
QGroupBox::indicator:checked { background: %(accent)s; border-color: %(accent_text)s; }

QListWidget { background: transparent; border: none; }
QListWidget::item { border: none; margin: 0 0 6px 0; padding: 0; background: transparent; }
QListWidget::item:selected { background: transparent; }
QFrame#PartRow { background: %(surface2)s; border: 1px solid %(border)s; border-radius: 9px; }
QFrame#PartRow[selected="true"] { border: 1.5px solid %(accent)s; background: %(accent_soft)s; }
QFrame#PartRow[warn="true"] { border-left: 3px solid %(warn)s; }
QFrame#PartRow[done="true"] { background: %(done_bg)s; border: 1.5px solid %(success_text)s; }
QFrame#PartRow[done="true"] QLabel#PartName { color: %(done_fg)s; }
QToolButton#DoneButton { border: 1px solid %(border)s; border-radius: 6px; padding: 2px 4px; }
QToolButton#DoneButton:checked { background: %(success)s; color: %(on_accent)s; border-color: %(success)s; font-weight: 700; }
QFrame#SheetsBox { background: %(surface2)s; border: 1px solid %(border)s; border-radius: 9px; }
QLabel#SheetsTitle { font-weight: 700; }
QPushButton#danger { background: %(danger_button)s; color: %(on_accent)s; border: 2px solid %(danger_button)s; font-weight: 700; }
QPushButton#danger:hover { background: %(danger_hover)s; }
QPushButton#ReqRow { text-align: left; padding: 5px 8px; border: 1px solid %(border)s; border-radius: %(radius_md)spx;
    background: %(surface)s; font-weight: 500; }
QPushButton#ReqRow:hover { background: %(accent_soft)s; }
QPushButton#ReqRow:checked { background: %(accent)s; color: white; font-weight: 700; }
QPushButton#ReqAll { text-align: center; padding: 4px; border: none; background: transparent; color: %(accent_text)s;
    font-weight: 600; }
QLabel#PartName { font-weight: 700; color: %(text)s; }
QLabel#PartSub { color: %(muted)s; font-size: %(font_small)spt; }
QFrame#RequestCard { background: %(surface)s; border: 1px solid %(border)s; border-radius: %(radius_lg)spx; }
QLabel#ReqTitle { font-size: 11pt; font-weight: 800; color: %(text)s; }
QLabel#ReqLine { color: %(text)s; }
QLabel#MatChip { border-radius: 8px; padding: 1px 8px; font-size: %(font_small)spt; font-weight: 700; color: white; }
QLabel#Badge { background: %(warn_soft)s; color: %(warn)s; border-radius: 6px; padding: 1px 6px;
               font-size: %(font_small)spt; font-weight: 700; }
QLabel#Thumb { background: %(surface)s; border: 1px solid %(border)s; border-radius: %(radius_md)spx; }

QFrame#Banner { border-radius: 8px; }
QFrame#Banner[kind="info"] { background: %(accent_soft)s; border: 1px solid %(accent)s; }
QFrame#Banner[kind="warn"] { background: %(warn_soft)s; border: 1px solid %(warn)s; }
QFrame#Banner[kind="ok"] { background: %(done_bg)s; border: 1px solid %(success_text)s; }
QLabel#BannerText { color: %(text)s; }

QTabBar { background: transparent; }
QTabBar::tab { background: transparent; color: %(muted)s; padding: 7px 16px; margin: 4px 2px;
               border-radius: %(radius_md)spx; font-weight: 600; }
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
QLabel#Chip { background: %(surface2)s; border: 1px solid %(border)s; border-radius: %(radius_lg)spx;
              padding: 2px 10px; color: %(text)s; font-weight: 600; }
QLabel#Chip[kind="warn"] { background: %(danger_soft)s; border-color: %(danger)s; color: %(danger)s; }
QProgressBar { background: %(surface3)s; border: none; border-radius: 5px; height: 10px;
               max-height: 10px; text-align: center; color: transparent; }
QProgressBar::chunk { background: %(accent)s; border-radius: 5px; }

QTableWidget { background: %(surface)s; border: 1px solid %(border)s; border-radius: 8px;
               gridline-color: %(border)s; color: %(text)s; }
QHeaderView::section { background: %(surface2)s; color: %(muted)s; border: none; padding: 6px;
                       font-weight: 700; }
QDialogButtonBox QPushButton { min-width: 90px; }

/* ---- gerador de caixas: passos numerados, botões ilustrados e segmentados ---- */
QLabel#PanelTitle { font-size: 13pt; font-weight: 800; color: %(text)s; }
QLabel#StepNum { background: %(accent)s; color: white; border-radius: 12px; font-weight: 800; }
QLabel#StepHint { color: %(muted)s; }
QToolButton#Tile { background: %(surface2)s; border: 1px solid %(border)s; border-radius: %(radius_lg)spx;
                   padding: 4px 2px 6px 2px; color: %(text)s; font-weight: 600; }
QToolButton#Tile:hover { border-color: %(muted)s; }
QToolButton#Tile:checked { background: %(accent_soft)s; border: 2px solid %(accent)s; color: %(accent_text)s;
                           font-weight: 700; }
QPushButton#Seg { border-radius: 0; padding: 6px 10px; font-weight: 600; }
QPushButton#Seg[pos="first"] { border-top-left-radius: 7px; border-bottom-left-radius: 7px; }
QPushButton#Seg[pos="last"] { border-top-right-radius: 7px; border-bottom-right-radius: 7px; border-left: none; }
QPushButton#Seg[pos="mid"] { border-left: none; }
QPushButton#Seg:checked { background: %(accent)s; color: white; border-color: %(accent_text)s; }
QPushButton#Chip { border-radius: 14px; padding: 4px 6px; font-weight: 600; }
QPushButton#Chip:checked { background: %(accent_soft)s; color: %(accent_text)s; border: 1px solid %(accent)s; }

QToolButton#SectionToggle { text-align: left; font-weight: 700; padding: 4px 6px;
    background: transparent; border: 2px solid transparent; color: %(text)s; }
QToolButton#SectionToggle:hover { background: %(surface3)s; }
QLabel#SelectionSummary { color: %(muted)s; padding: 4px 8px; background: %(surface2)s;
    border-radius: %(radius_sm)spx; }
QLabel#EmptyTitle { font-size: %(font_hero)spt; font-weight: 700; color: %(text)s; }
QCheckBox#CutDone { color: %(success_text)s; font-weight: 700; }
QLabel#InlineWarning { color: %(warn)s; }

/* O ID mantém a prioridade sobre os estilos dos controles especiais. */
QPushButton#primary:focus, QPushButton#success:focus, QPushButton#Seg:focus,
QPushButton#Chip:focus, QToolButton#Tile:focus, QToolButton#DoneButton:focus,
QToolButton#Danger:focus, QToolButton#SectionToggle:focus { border: 2px solid %(focus)s; }
"""
