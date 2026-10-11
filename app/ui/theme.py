"""Tema visual (claro/escuro) com tokens de cor compartilhados pela interface e pelo canvas."""
from __future__ import annotations

from PySide6.QtGui import QColor, QPalette, QFont
from PySide6.QtWidgets import QApplication

# Paleta "Fluent" (Windows 11): cinza neutro de fundo, cartões brancos com borda fina e acento azul do Windows.
LIGHT = {
    "accent_text": "#0f6cbd", "success_text": "#0e700e",
    "bg": "#f3f3f3", "surface": "#ffffff", "surface2": "#f7f7f7", "surface3": "#ededed",
    "border": "#e5e5e5", "field_border": "#d1d1d1", "control_border": "#8a8a8a", "focus": "#0f6cbd",
    "text": "#1b1b1b", "muted": "#5f5f5f", "accent": "#0f6cbd",
    "accent_hover": "#115ea3", "accent_soft": "#ebf3fc", "done_fg": "#0e700e", "done_bg": "#dff6dd", "success": "#107c10",
    "success_hover": "#0e700e", "danger": "#c42b1c", "danger_hover": "#a4262c", "danger_soft": "#fde7e9", "warn": "#9d5d00",
    "warn_soft": "#fff4ce", "canvas": "#ebebeb", "sheet": "#ffffff", "sheet_border": "#c7c7c7",
    "grid": "#0000000c", "grid2": "#0000001c", "part_fill": "#0f6cbd24", "shadow": "#0000001f",
    "mdf": "#d9bc92", "mdf_line": "#7c2d12", "metrics_bg": "#fafafa",
}
DARK = {
    "accent_text": "#60cdff", "success_text": "#6ccb5f",
    "bg": "#202020", "surface": "#2b2b2b", "surface2": "#323232", "surface3": "#3a3a3a",
    "border": "#3d3d3d", "field_border": "#4a4a4a", "control_border": "#8a8a8a", "focus": "#60cdff",
    "text": "#ffffff", "muted": "#ababab", "accent": "#0f6cbd",
    "accent_hover": "#115ea3", "accent_soft": "#1f3347", "done_fg": "#6ccb5f", "done_bg": "#1d3320", "success": "#107c10",
    "success_hover": "#0e700e", "danger": "#ff99a4", "danger_hover": "#a4262c", "danger_soft": "#442726", "warn": "#fce100",
    "warn_soft": "#433519", "canvas": "#1c1c1c", "sheet": "#2b2b2b", "sheet_border": "#4a4a4a",
    "grid": "#ffffff0d", "grid2": "#ffffff1c", "part_fill": "#60cdff2e", "shadow": "#00000080",
    "mdf": "#b89566", "mdf_line": "#3b1a08", "metrics_bg": "#272727",
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
    "font_body": 10, "font_small": 9, "font_section": 10, "font_title": 13, "font_hero": 15,
    "panel_parts": 320, "panel_parts_min": 288, "panel_params": 320, "panel_photo": 340,
    "panel_box": 392, "panel_box_compact": 360,
    "thumb": 44, "control_height": 24,
}
for palette in (LIGHT, DARK):
    palette.update(NEUTRALS)
    palette.update(METRICS)
    palette.update(on_accent="#ffffff", danger_button="#c42b1c")

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


def line_color(c: QColor, dark: bool) -> QColor:
    """Cor de uma linha do desenho na tela. No escuro, cores muito escuras (preto, azul puro da gravação)
    sumiam no fundo: ganham luminosidade mantendo o matiz. Só a visualização muda; o DXF exportado não."""
    if not dark:
        return c
    lum = sum(k * (v / 255) ** 2.2 for k, v in zip((0.2126, 0.7152, 0.0722), (c.red(), c.green(), c.blue())))
    if lum >= 0.15:
        return c
    if c.hslSaturation() < 40:                       # preto/cinza escuro
        return QColor(230, 230, 230)
    return QColor.fromHsl(c.hslHue(), c.hslSaturation(), max(c.lightness(), 175))


def apply_theme(app: QApplication, dark: bool) -> None:
    global _current, _dark
    _dark = dark
    _current = dict(DARK if dark else LIGHT)
    t = _current
    from .style import SindriStyle
    if not isinstance(app.style(), SindriStyle):          # trocar o estilo é caro: só na 1ª vez
        app.setStyle(SindriStyle())
    from PySide6.QtGui import QFontDatabase
    fams = set(QFontDatabase.families())
    f = QFont("Segoe UI Variable Text" if "Segoe UI Variable Text" in fams else "Segoe UI")
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
    # links em textos (avisos, dicas): azul legível nos dois temas, não o #0000ff padrão do Qt
    p.setColor(QPalette.Link, QColor(t["accent_text"]))
    p.setColor(QPalette.LinkVisited, QColor(t["accent_text"]))
    for role in (QPalette.Text, QPalette.ButtonText, QPalette.WindowText):
        p.setColor(QPalette.Disabled, role, QColor(t["muted"]))
    app.setPalette(p)
    from .icons import image_file
    t["check_img"] = image_file("check", "#ffffff")
    from .icons import switch_file
    t["switch_on"] = switch_file(True, "#ffffff")
    t["switch_off"] = switch_file(False, t["muted"])
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
QFrame#PanelCard { background: %(surface)s; border: 1px solid %(border)s; border-radius: %(radius_lg)spx; }
QFrame#VSep { background: %(border)s; }
QLabel#Logo { font-size: 12pt; font-weight: 700; color: %(text)s; }
QLabel#LogoMark { background: %(accent)s; color: white; border-radius: %(radius_md)spx; font-weight: 800;
                  font-size: %(font_section)spt; }
QLabel#SectionTitle { font-size: 11pt; font-weight: 700; color: %(text)s; }
QLabel#PanelHeading { font-size: 13pt; font-weight: 700; color: %(text)s; }
QLabel#Muted, QLabel#hint { color: %(muted)s; }
QLabel#SectionHead { font-size: %(font_section)spt; font-weight: 700; color: %(text)s; }
QLabel#SectionIcon { background: %(accent_soft)s; border-radius: 8px; }

/* rodapé de métricas: quatro células com rótulo em maiúsculas e número em fonte monoespaçada */
QFrame#Metrics { background: %(metrics_bg)s; border: none; border-top: 1px solid %(border)s;
                 border-bottom-left-radius: %(radius_lg)spx; border-bottom-right-radius: %(radius_lg)spx; }
QFrame#MetricCell { background: transparent; border: none; border-right: 1px solid %(border)s; }
QFrame#MetricCell[last="true"] { border-right: none; }
QLabel#MetricCaption { color: %(muted)s; font-size: 8pt; font-weight: 600; letter-spacing: 1px; }
QLabel#MetricValue { font-family: "Cascadia Mono", "Consolas", "IBM Plex Mono", "DejaVu Sans Mono", monospace;
                     font-size: 15pt; font-weight: 600; color: %(text)s; }
QLabel#MetricValue[accent="true"] { color: %(accent_text)s; }
QLabel#MetricBig { font-size: 15pt; font-weight: 800; color: %(text)s; }
QLabel#State { border-radius: 9px; padding: 3px 10px; font-weight: 600; background: %(surface3)s;
               color: %(muted)s; }
QLabel#State[kind="run"] { background: %(accent_soft)s; color: %(accent_text)s; }
QLabel#State[kind="ok"] { background: %(done_bg)s; color: %(success_text)s; }
QLabel#State[kind="warn"] { background: %(danger_soft)s; color: %(danger)s; }
QToolButton#Danger:hover { background: %(danger_soft)s; }
QLabel#FieldLabel { color: %(muted)s; }

QPushButton { background: %(surface)s; color: %(text)s; border: 1px solid %(field_border)s;
              border-radius: %(radius_md)spx; padding: 7px 14px; font-weight: 600; }
QPushButton:focus { padding: 6px 13px; }
QPushButton#ToolbarAction { padding: 7px 6px; border-color: transparent; background: transparent; }
QPushButton#ToolbarAction:focus { padding: 6px 5px; }
QPushButton:hover { background: %(surface2)s; }
QPushButton#ToolbarAction:hover { background: %(surface3)s; }
QPushButton:pressed { background: %(surface3)s; }
QPushButton:disabled { color: %(muted)s; background: %(surface2)s; border-color: %(border)s; }
QPushButton#primary { background: %(accent)s; color: white; border: 1px solid %(accent)s; }
QPushButton#primary:hover { background: %(accent_hover)s; }
QPushButton#primary:disabled { background: %(surface3)s; color: %(muted)s; border-color: %(border)s; }
QPushButton#success { background: %(success)s; color: white; border: 1px solid %(success)s; }
QPushButton#success:hover { background: %(success_hover)s; }
QPushButton#success:disabled { background: %(surface3)s; color: %(muted)s; border-color: %(border)s; }
QPushButton#ghost { background: transparent; border: 1px solid transparent; font-weight: 600;
                    color: %(accent_text)s; text-align: left; padding: 6px 8px; }
QPushButton#ghost:hover { background: %(accent_soft)s; }
QPushButton#ghost:focus { padding: 5px 7px; }

QToolButton { background: transparent; color: %(text)s; border: 2px solid transparent;
              border-radius: %(radius_md)spx; padding: 5px; }
QToolButton:hover { background: %(surface3)s; }
QToolButton:checked { background: %(accent_soft)s; border-color: %(accent_text)s; }
QToolButton#PanelToggle { color: %(muted)s; padding: 4px 10px; font-weight: 600; }
QToolButton#PanelToggle:checked { background: %(surface3)s; color: %(text)s; border-color: transparent; }
QToolButton#PanelToggle:hover { color: %(text)s; }
QToolButton#PanelToggle:focus { border-color: %(focus)s; }
QToolButton#PlateButton { background: %(surface)s; border: 1px solid %(field_border)s; padding: 5px 10px;
                          font-weight: 600; text-align: left; }
QToolButton#PlateButton:hover { background: %(surface2)s; }
QToolButton#PlateButton::menu-indicator { image: none; width: 0; }
QToolButton:disabled { color: %(muted)s; }

/* campos: borda fina e traço inferior mais escuro (contraste do controle), como no Windows 11 */
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {
    background: %(surface)s; color: %(text)s; border: 1px solid %(field_border)s;
    border-bottom: 1px solid %(control_border)s; border-radius: 6px;
    padding: 5px 8px; min-height: 20px; selection-background-color: %(accent)s; }
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QLineEdit:hover { background: %(surface2)s; }
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus {
    border-bottom: 2px solid %(accent_text)s; padding-bottom: 4px; }
QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled { color: %(muted)s; background: %(surface2)s; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView { background: %(surface)s; color: %(text)s; border: 1px solid %(border)s;
    selection-background-color: %(accent_soft)s; selection-color: %(text)s; padding: 4px; }
QSpinBox::up-button, QDoubleSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::down-button {
    width: 16px; border: none; background: transparent; }

QCheckBox { color: %(text)s; spacing: 8px; }
QCheckBox::indicator { width: 16px; height: 16px; border-radius: 4px; border: 1px solid %(control_border)s;
                       background: %(surface)s; }
QCheckBox::indicator:checked { background: %(accent)s; border-color: %(accent)s;
    image: url(%(check_img)s); }
QCheckBox:disabled { color: %(muted)s; }
/* liga/desliga (opções do encaixe): mesma caixa de seleção, desenhada como chave */
QCheckBox#Switch { spacing: 10px; }
QCheckBox#Switch::indicator { width: 40px; height: 20px; border-radius: 10px; border: 1px solid %(control_border)s;
                              background: %(surface)s; image: url(%(switch_off)s); }
QCheckBox#Switch::indicator:checked { background: %(accent)s; border-color: %(accent)s; image: url(%(switch_on)s); }
QCheckBox#Switch::indicator:disabled { border-color: %(border)s; }

QGroupBox { background: %(surface)s; border: 1px solid %(border)s; border-radius: %(radius_lg)spx;
            margin-top: 14px; padding: 12px 10px 8px 10px; font-weight: 700; color: %(muted)s; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; top: 2px; padding: 0 2px;
                   color: %(muted)s; }
QGroupBox::indicator { width: 14px; height: 14px; border-radius: 3px; border: 1px solid %(muted)s; }
QGroupBox::indicator:checked { background: %(accent)s; border-color: %(accent_text)s; }

QListWidget { background: transparent; border: none; }
QListWidget::item { border: none; margin: 0; padding: 0; background: transparent; }
QListWidget::item:selected { background: transparent; }
/* cartão de peça (grade de 2 colunas) */
QFrame#PartRow { background: %(surface)s; border: 1px solid %(border)s; border-radius: 10px; }
QFrame#PartRow:hover { border-color: %(field_border)s; }
QFrame#PartRow[selected="true"] { border: 2px solid %(accent)s; background: %(accent_soft)s; }
QFrame#PartRow[warn="true"] { border-color: %(warn)s; }
QFrame#PartRow[done="true"] { background: %(done_bg)s; border: 1px solid %(success_text)s; }
QFrame#PartRow[done="true"] QLabel#PartName { color: %(done_fg)s; }
QLabel#PartQty { font-size: 13pt; font-weight: 800; color: %(text)s; }
QToolButton#DoneButton { border: 1px solid %(field_border)s; border-radius: 6px; padding: 1px;
                         min-width: 20px; min-height: 20px; background: %(surface)s; }
QToolButton#DoneButton:checked { background: %(success)s; color: %(on_accent)s; border-color: %(success)s; font-weight: 700; }
QPushButton#AddTile { border: 1px dashed %(control_border)s; border-radius: 10px; background: transparent;
                      color: %(muted)s; font-weight: 600; padding: 8px; }
QPushButton#AddTile:hover { background: %(surface2)s; color: %(text)s; }
QPushButton#AddTile:focus { padding: 7px; }
QFrame#PartDetail { background: %(surface2)s; border: none; border-radius: 10px; }
QToolButton#StepButton { border: none; border-radius: 4px; padding: 0; min-width: 28px; min-height: 26px;
                         font-weight: 700; }
QFrame#Stepper { background: %(surface)s; border: 1px solid %(field_border)s; border-radius: 6px; }
QFrame#SheetsBox { background: %(surface2)s; border: none; border-radius: 10px; }
QLabel#SheetsTitle { font-weight: 700; }
QPushButton#danger { background: %(danger_button)s; color: %(on_accent)s; border: 1px solid %(danger_button)s; font-weight: 700; }
QPushButton#danger:hover { background: %(danger_hover)s; }
QPushButton#ReqRow { text-align: left; padding: 5px 8px; border: 1px solid %(border)s; border-radius: %(radius_md)spx;
    background: %(surface)s; font-weight: 500; }
QPushButton#ReqRow:hover { background: %(accent_soft)s; }
QPushButton#ReqRow:checked { background: %(accent)s; color: white; font-weight: 700; }
QPushButton#ReqAll { text-align: center; padding: 4px; border: none; background: transparent; color: %(accent_text)s;
    font-weight: 600; }
QLabel#PartName { font-weight: 600; color: %(text)s; }
QLabel#PartSub { color: %(muted)s; font-size: %(font_small)spt; }
QFrame#RequestCard { background: %(surface2)s; border: none; border-radius: 10px; }
QLabel#ReqTitle { font-size: 11pt; font-weight: 800; color: %(text)s; }
QLabel#ReqLine { color: %(text)s; }
QLabel#MatChip { border-radius: 8px; padding: 1px 8px; font-size: %(font_small)spt; font-weight: 700; color: white; }
QLabel#Badge { background: %(warn_soft)s; color: %(warn)s; border-radius: 6px; padding: 1px 6px;
               font-size: %(font_small)spt; font-weight: 700; }
QLabel#Thumb { background: transparent; border: none; }

QFrame#Banner { border-radius: 8px; border: none; }
QFrame#Banner[kind="info"] { background: %(accent_soft)s; }
QFrame#Banner[kind="warn"] { background: %(warn_soft)s; }
QFrame#Banner[kind="ok"] { background: %(done_bg)s; }
QLabel#BannerText { color: %(text)s; }

QTabBar { background: transparent; }
QTabBar::tab { background: transparent; color: %(muted)s; padding: 6px 14px; margin: 2px 2px;
               border-radius: 6px; font-weight: 600; }
QTabBar::tab:selected { background: %(accent_soft)s; color: %(accent_text)s; }
QTabBar::tab:hover:!selected { color: %(text)s; background: %(surface2)s; }
/* modos do programa no topo: trilho cinza, modo atual em "pílula" branca */
QFrame#ModeTrack { background: %(surface3)s; border-radius: 10px; }
QTabBar#ModeTabs::tab { margin: 0 1px; padding: 6px 16px; border-radius: 7px; color: %(muted)s; }
QTabBar#ModeTabs::tab:selected { background: %(surface)s; color: %(text)s; }
QTabBar#ModeTabs::tab:hover:!selected { background: transparent; color: %(text)s; }

QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: %(surface3)s; border-radius: 4px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: %(control_border)s; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: %(surface3)s; border-radius: 4px; min-width: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QSplitter::handle { background: transparent; width: 12px; }

QStatusBar { background: %(surface)s; color: %(muted)s; border-top: 1px solid %(border)s; }
QStatusBar::item { border: none; }
QLabel#Chip { background: %(surface2)s; border: 1px solid %(border)s; border-radius: %(radius_lg)spx;
              padding: 2px 10px; color: %(text)s; font-weight: 600; }
QLabel#Chip[kind="warn"] { background: %(danger_soft)s; border-color: %(danger)s; color: %(danger)s; }
QProgressBar { background: %(surface3)s; border: none; border-radius: 3px; height: 6px;
               max-height: 6px; text-align: center; color: transparent; }
QProgressBar::chunk { background: %(accent)s; border-radius: 3px; }

QTableWidget { background: %(surface)s; border: 1px solid %(border)s; border-radius: 8px;
               gridline-color: %(border)s; color: %(text)s; }
QHeaderView::section { background: %(surface2)s; color: %(muted)s; border: none; padding: 6px;
                       font-weight: 700; }
QDialogButtonBox QPushButton { min-width: 90px; }

/* ---- gerador de caixas: passos numerados, botões ilustrados e segmentados ---- */
QLabel#PanelTitle { font-size: 13pt; font-weight: 800; color: %(text)s; }
QLabel#StepNum { background: %(accent)s; color: white; border-radius: 12px; font-weight: 800; }
QLabel#StepHint { color: %(muted)s; }
QToolButton#Tile { background: %(surface)s; border: 1px solid %(border)s; border-radius: %(radius_lg)spx;
                   padding: 4px 2px 6px 2px; color: %(text)s; font-weight: 600; }
QToolButton#Tile:hover { border-color: %(field_border)s; background: %(surface2)s; }
QToolButton#Tile:checked { background: %(accent_soft)s; border: 2px solid %(accent)s; color: %(accent_text)s;
                           font-weight: 700; }
QFrame#SegTrack { background: %(surface3)s; border-radius: 10px; }
QPushButton#Seg { background: transparent; color: %(muted)s; border: 2px solid transparent; border-radius: 8px;
                  padding: 5px 10px; font-weight: 600; }
QPushButton#Seg:hover:!checked { color: %(text)s; }
QPushButton#Seg:checked { background: %(surface)s; color: %(text)s; border-color: %(border)s; }
QPushButton#Seg:focus { border-color: %(focus)s; }
QPushButton#Chip { border-radius: 14px; padding: 4px 6px; font-weight: 600; }
QPushButton#Chip:checked { background: %(accent_soft)s; color: %(accent_text)s; border: 1px solid %(accent)s; }

/* seções de parâmetros: título em maiúsculas, discreto, que recolhe a seção */
QToolButton#SectionToggle { text-align: left; font-weight: 600; padding: 4px 4px; font-size: 9pt;
    background: transparent; border: 2px solid transparent; color: %(muted)s; letter-spacing: 0.6px; }
QToolButton#SectionToggle:hover { color: %(text)s; background: transparent; }
QLabel#SelectionSummary { color: %(muted)s; padding: 4px 8px; background: %(surface2)s;
    border-radius: %(radius_sm)spx; }
QLabel#EmptyTitle { font-size: %(font_hero)spt; font-weight: 700; color: %(text)s; }
QCheckBox#CutDone { color: %(success_text)s; font-weight: 700; }
QLabel#InlineWarning { color: %(warn)s; }

/* O ID mantém a prioridade sobre os estilos dos controles especiais. */
QPushButton#primary:focus, QPushButton#success:focus, QPushButton#Seg:focus,
QPushButton#Chip:focus, QToolButton#Tile:focus, QToolButton#DoneButton:focus,
QToolButton#Danger:focus, QToolButton#SectionToggle:focus, QPushButton#AddTile:focus { border: 2px solid %(focus)s; }
"""
