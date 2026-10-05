"""Janela principal do Sindri."""
from __future__ import annotations

import copy
import json
import os
from typing import Optional

from PySide6.QtCore import QSettings, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QKeySequence
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
                               QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton,
                               QSplitter, QStackedWidget, QStatusBar, QTabBar, QTabWidget, QToolBar, QToolButton,
                               QVBoxLayout, QWidget, QInputDialog, QSizePolicy)

from ..core.collision import CollisionChecker
from ..core.dxf_export import export_all_sheets
from ..core.rdworks import files_to_open, find_rdworks, launch
from ..core.models import ImportReport, NestParams, NestResult, Part, Placement
from ..core.optimizer import shapes_from_parts
from ..core.part_builder import import_files
from ..core.placement import Decoder
from ..core.project import ProjectError, load_project, save_project
from ..core.validate import validate_layout
from ..workers.nest_worker import NestWorker
from .canvas import NestCanvas, sheet_offset
from .dialogs import CleanupDialog, ExportDialog, PresetsDialog, load_presets, save_presets, settings
from .parts_panel import PartsPanel
from .owners import owner_colors, sheet_numbers
from .render import clear_graphics_cache
from .report import export_pdf
from .settings_panel import SettingsPanel
from .theme import apply_theme
from .icons import icon, pixmap

APP_NAME = "Sindri"
MAX_UNDO = 100


def fmt_pct(v: float) -> str:
    return f"{100 * v:.1f}".replace(".", ",") + "%"


def _now_txt() -> str:
    import datetime as _dt
    return f"{_dt.datetime.now():%d/%m/%Y %H:%M}"


def _str_list(v) -> list[str]:
    """QSettings devolve str quando a lista tem 1 item (Windows) — normaliza para lista."""
    if isinstance(v, str):
        return [v] if v else []
    return [x for x in (v or []) if isinstance(x, str)]


def theme_tokens():
    from .theme import tokens
    return tokens()


def theme_accent():
    from PySide6.QtGui import QColor
    return QColor("#2563eb")


class MainWindow(QMainWindow):
    def __init__(self, workers: Optional[int] = None):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        from .icons import app_icon
        self.setWindowIcon(app_icon())
        self.resize(1400, 860)
        self.setAcceptDrops(True)
        self.workers = workers

        self.files: list[str] = []
        self.report: Optional[ImportReport] = None
        self.parts: list[Part] = []
        self.pmap: dict[str, Part] = {}
        self.placements: list[Placement] = []
        self.n_sheets = 0
        self.unplaced: list[tuple[str, int]] = []
        self.too_big: set[str] = set()
        self.undo_stack: list = []
        self.redo_stack: list = []
        self.worker: Optional[NestWorker] = None
        self.checker: Optional[CollisionChecker] = None
        self.project_path: Optional[str] = None
        self.dirty = False
        self.dark = settings().value("ui/dark", "false") == "true"
        self.file_multipliers: dict[str, int] = {}
        self.file_materials: dict[str, str] = {}
        self.file_tags: dict[str, str] = {}
        self.cut_sheets: set[int] = set()        # placas já cortadas (checklist)
        self.delivered: set[str] = set()         # (projetos antigos)
        self.done_parts: set[str] = set()        # peças marcadas como feitas (cortadas)
        self.request_label: Optional[str] = None
        self.request_info: Optional[dict] = None
        self.generation = 0
        self.evaluated = 0
        self._layout_shown_once = False

        self._build_ui()
        self._build_actions()
        params = NestParams()
        raw = settings().value("ui/last_params", "")
        if raw:
            try:
                params = NestParams.from_json(json.loads(raw))
            except Exception:
                pass
        self.settings_panel.set_params(params)
        self.params = params
        self._refresh_presets()
        self.set_dark(self.dark)
        self._update_buttons()
        self._drag_timer = QTimer(self)
        self._drag_timer.setSingleShot(True)
        self._drag_timer.setInterval(30)
        self._drag_timer.timeout.connect(self._check_drag_collisions)

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        # ---------------- barra superior (ações principais sempre visíveis)
        top = QFrame()
        top.setObjectName("TopBar")
        tl = QHBoxLayout(top)
        tl.setContentsMargins(14, 8, 14, 8)
        tl.setSpacing(8)
        from .icons import app_icon
        mark = QLabel()
        mark.setFixedSize(30, 30)
        mark.setPixmap(app_icon().pixmap(30, 30))
        logo = QLabel("Sindri")
        logo.setObjectName("Logo")
        tl.addWidget(mark)
        tl.addWidget(logo)
        tl.addSpacing(10)
        tl.addWidget(self._vsep())
        self.btn_open = QPushButton("Abrir DXF")
        self.btn_open.setToolTip("Abrir um ou vários arquivos DXF (Ctrl+O). Você também pode arrastá-los para a janela.")
        self.btn_open.clicked.connect(lambda: self.open_dxf_dialog())
        self.btn_save = QPushButton("Salvar")
        self.btn_save.setToolTip("Salva arquivos, parâmetros e o encaixe atual em um projeto .sindri (Ctrl+S)")
        self.btn_save.clicked.connect(lambda: self.save_project())
        self.btn_intranet = QPushButton("Intranet FIAP")
        self.btn_intranet.setToolTip("Baixar os arquivos de uma Solicitação Maker direto da intranet (Ctrl+I)")
        self.btn_intranet.clicked.connect(lambda: self.open_intranet())
        tl.addWidget(self.btn_open)
        tl.addWidget(self.btn_intranet)
        tl.addWidget(self.btn_save)
        tl.addWidget(self._vsep())
        lbl = QLabel("Placa")
        lbl.setObjectName("Muted")
        tl.addWidget(lbl)
        self.preset_combo = QComboBox()
        self.preset_combo.setMinimumWidth(230)
        self.preset_combo.setToolTip("Tamanho da placa de material. Edite a lista na engrenagem.")
        self.preset_combo.activated.connect(self._preset_chosen)
        tl.addWidget(self.preset_combo)
        self.btn_gear = QToolButton()
        self.btn_gear.setToolTip("Gerenciar placas salvas")
        self.btn_gear.clicked.connect(self.edit_presets)
        tl.addWidget(self.btn_gear)
        tl.addStretch(1)
        self.btn_nest = QPushButton("Encaixar")
        self.btn_nest.setObjectName("primary")
        self.btn_nest.setMinimumWidth(130)
        self.btn_nest.setToolTip("Organiza as peças automaticamente (Espaço). Peças travadas ficam onde estão.")
        self.btn_nest.clicked.connect(self.start_or_pause)
        self.btn_pause = QPushButton("Pausar")
        self.btn_pause.setToolTip("Pausa/continua a otimização (Espaço)")
        self.btn_pause.clicked.connect(self.toggle_pause)
        self.btn_stop = QPushButton("Parar")
        self.btn_stop.setToolTip("Para e mantém a melhor solução encontrada (Esc)")
        self.btn_stop.clicked.connect(lambda: self.stop_nest())
        self.btn_export = QPushButton("Exportar para RDWorks")
        self.btn_export.setObjectName("success")
        self.btn_export.setToolTip("Salva um DXF por placa, pronto para abrir no RDWorks (Ctrl+E)")
        self.btn_export.clicked.connect(self.export)
        for w in (self.btn_nest, self.btn_pause, self.btn_stop):
            tl.addWidget(w)
        tl.addWidget(self._vsep())
        tl.addWidget(self.btn_export)
        tl.addWidget(self._vsep())
        self.btn_theme = QToolButton()
        self.btn_theme.setToolTip("Alternar tema claro/escuro (Ctrl+T)")
        self.btn_theme.clicked.connect(lambda: self.set_dark(not self.dark))
        tl.addWidget(self.btn_theme)

        # ---------------- painel de peças
        self.parts_panel = PartsPanel()
        self.parts_panel.quantityChanged.connect(self.on_quantity_changed)
        self.parts_panel.rotationLockChanged.connect(self.on_rotation_lock_changed)
        self.parts_panel.partSelected.connect(self.on_part_selected)
        self.parts_panel.btn_kits.clicked.connect(self.multiply_kits)
        self.parts_panel.btn_reset.clicked.connect(self.reset_quantities)
        self.parts_panel.btn_clear.clicked.connect(lambda: self.clear_all())

        # ---------------- área central (cartão com abas, canvas e navegação)
        outer = QWidget()
        ol = QVBoxLayout(outer)
        ol.setContentsMargins(0, 12, 0, 12)
        card = QFrame()
        card.setObjectName("Card")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(10, 8, 10, 8)
        cl.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(4)
        self.tabs = QTabBar()
        self.tabs.setDrawBase(False)
        self.tabs.addTab("Arquivo original")
        self.tabs.addTab("Encaixe")
        self.tabs.setTabToolTip(0, "Como as peças estão no DXF (contornos abertos em vermelho)")
        self.tabs.setTabToolTip(1, "Resultado do encaixe nas placas")
        self.tabs.currentChanged.connect(self._tab_changed)
        head.addWidget(self.tabs)
        head.addStretch(1)
        self.btn_rot = self._tool("Girar peça selecionada (R)", self.rotate_selected)
        self.btn_lock = self._tool("Travar/destravar posição da peça selecionada (L)", self.toggle_lock_selected)
        self.btn_del = self._tool("Remover peça selecionada (Del)", self.delete_selected)
        for b in (self.btn_rot, self.btn_lock, self.btn_del):
            head.addWidget(b)
        head.addWidget(self._vsep())
        self.btn_prev = self._tool("Placa anterior (PgUp)", lambda: self.goto_sheet(self.canvas.current_sheet() - 1))
        self.sheet_label = QLabel("")
        self.sheet_label.setObjectName("Muted")
        self.btn_next = self._tool("Próxima placa (PgDn)", lambda: self.goto_sheet(self.canvas.current_sheet() + 1))
        self.btn_fit = self._tool("Enquadrar tudo (F)", lambda: self.canvas.fit_all())
        head.addWidget(self.btn_prev)
        head.addWidget(self.sheet_label)
        head.addWidget(self.btn_next)
        head.addWidget(self.btn_fit)
        cl.addLayout(head)

        self.banner = QFrame()
        self.banner.setObjectName("Banner")
        bl = QHBoxLayout(self.banner)
        bl.setContentsMargins(10, 6, 6, 6)
        self.banner_icon = QLabel()
        self.banner_text = QLabel()
        self.banner_text.setObjectName("BannerText")
        self.banner_text.setWordWrap(True)
        self.banner_close = QToolButton()
        self.banner_close.clicked.connect(self.banner.hide)
        bl.addWidget(self.banner_icon)
        bl.addWidget(self.banner_text, 1)
        bl.addWidget(self.banner_close)
        self.banner.hide()
        cl.addWidget(self.banner)

        self.stack = QStackedWidget()
        self.empty_state = self._build_empty_state()
        self.canvas = NestCanvas()
        self.canvas.itemsDragging.connect(lambda it: self._drag_timer.start())
        self.canvas.itemsReleased.connect(self.on_items_released)
        self.canvas.contextMenuForItems.connect(self.show_item_menu)
        self.canvas.scene().selectionChanged.connect(self._canvas_selection)
        self.canvas.setFrameShape(QFrame.NoFrame)
        self.stack.addWidget(self.empty_state)
        self.stack.addWidget(self.canvas)
        cl.addWidget(self.stack, 1)
        cl.addWidget(self._build_metrics())
        ol.addWidget(card, 1)

        # ---------------- parâmetros
        self.settings_panel = SettingsPanel()
        self.settings_panel.paramsChanged.connect(self.on_params_changed)
        self.settings_panel.reimportNeeded.connect(self.reimport)

        split = QSplitter(Qt.Horizontal)
        self.parts_panel.doneChanged.connect(self.on_part_done)
        self.parts_panel.sheetToggled.connect(self.on_sheet_cut)
        split.addWidget(self.parts_panel)
        split.addWidget(outer)
        split.addWidget(self.settings_panel)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setStretchFactor(2, 0)
        split.setSizes([330, 760, 350])
        self.settings_panel.setMinimumWidth(330)
        self.parts_panel.setMinimumWidth(280)
        split.setChildrenCollapsible(False)

        root = QWidget()
        rl = QVBoxLayout(root)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        rl.addWidget(top)
        rl.addWidget(split, 1)
        self.setCentralWidget(root)

        # ---------------- barra de status com indicadores
        sb = QStatusBar()
        sb.setSizeGripEnabled(False)
        self.setStatusBar(sb)
        hint = QLabel("Roda: zoom  ·  botão do meio / Alt+arrastar: mover vista  ·  R girar  ·  "
                      "L travar  ·  Del remover  ·  F1 atalhos")
        hint.setObjectName("Muted")
        sb.addPermanentWidget(hint)
        self._refresh_icons()

    def _build_metrics(self) -> QFrame:
        box = QFrame()
        box.setObjectName("Metrics")
        h = QHBoxLayout(box)
        h.setContentsMargins(14, 8, 14, 8)
        h.setSpacing(18)

        def metric(caption: str, big: bool = False):
            v = QVBoxLayout()
            v.setSpacing(0)
            val = QLabel("—")
            val.setObjectName("MetricBig" if big else "MetricValue")
            cap = QLabel(caption)
            cap.setObjectName("MetricCaption")
            v.addWidget(val)
            v.addWidget(cap)
            h.addLayout(v)
            return val

        self.chip_util = metric("aproveitamento", big=True)
        self.chip_util.setToolTip("Área das peças ÷ área das placas usadas")
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setMinimumWidth(120)
        self.progress.setToolTip("Aproveitamento: área das peças ÷ área das placas usadas")
        h.addWidget(self.progress, 1)
        h.addWidget(self._vsep())
        self.chip_sheets = metric("placas")
        self.chip_parts = metric("peças encaixadas")
        self.chip_gen = metric("soluções testadas")
        h.addWidget(self._vsep())
        self.status_label = QLabel("Pronto")
        self.status_label.setObjectName("State")
        h.addWidget(self.status_label)
        return box

    def _report_requests(self) -> list[dict]:  # noqa: D401
        i = self.request_info
        if not i:
            return []
        return list(i.get("requests", [])) if i.get("batch") else [i]

    def _report_header(self) -> list[str]:
        i = self.request_info
        if not i:
            return []
        if i.get("batch"):
            lines = [f"Lote com {len(i.get('requests', []))} solicitações · Materiais: "
                     f"{', '.join(i.get('materials', []))}"]
            for r in i.get("requests", []):
                lines.append(f"  nº {r.get('code', '')} · RM {r.get('rm', '')} · {r.get('nome', '')}"
                             + (f" · {r['projeto']}" if r.get("projeto") else ""))
            return lines
        return [f"Solicitação nº {i.get('code', '')} · RM {i.get('rm', '')} · {i.get('nome', '')}",
                "  ·  ".join(x for x in (f"Projeto: {i['projeto']}" if i.get("projeto") else "",
                                         f"Professor: {i['professor']}" if i.get("professor") else "",
                                         f"Materiais: {', '.join(i.get('materials', []))}") if x)]

    def _vsep(self) -> QFrame:
        f = QFrame()
        f.setObjectName("VSep")
        f.setFixedSize(1, 24)
        return f

    def _tool(self, tip: str, slot) -> QToolButton:
        b = QToolButton()
        b.setToolTip(tip)
        b.setIconSize(QSize(17, 17))
        b.clicked.connect(lambda *_: slot())
        return b

    def _chip(self, text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("Chip")
        return lab

    def _build_empty_state(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addStretch(1)
        self.empty_icon = QLabel()
        self.empty_icon.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.empty_icon)
        t1 = QLabel("Arraste arquivos DXF para cá")
        t1.setAlignment(Qt.AlignCenter)
        t1.setStyleSheet("font-size: 15pt; font-weight: 700;")
        t2 = QLabel("As peças são detectadas e agrupadas automaticamente. "
                    "Depois escolha a placa e clique em Encaixar.")
        t2.setObjectName("Muted")
        t2.setAlignment(Qt.AlignCenter)
        t2.setWordWrap(True)
        lay.addWidget(t1)
        lay.addWidget(t2)
        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_empty_open = QPushButton("Escolher arquivo…")
        self.btn_empty_open.setObjectName("primary")
        self.btn_empty_open.clicked.connect(lambda: self.open_dxf_dialog())
        row.addWidget(self.btn_empty_open)
        row.addStretch(1)
        lay.addSpacing(8)
        lay.addLayout(row)
        lay.addStretch(2)
        return w

    def _refresh_icons(self):
        from .theme import tokens
        t = tokens()
        c, m = t["text"], t["muted"]
        self.btn_open.setIcon(icon("open", c, 16, m))
        self.btn_save.setIcon(icon("save", c, 16, m))
        self.btn_intranet.setIcon(icon("export", c, 16, m))
        self.btn_gear.setIcon(icon("settings", m, 17))
        self.btn_nest.setIcon(icon("play", "#ffffff", 14, m))
        paused = self.worker is not None and self.worker.pause_event.is_set()
        self.btn_pause.setIcon(icon("play" if paused else "pause", c, 14, m))
        self.btn_stop.setIcon(icon("stop", c, 14, m))
        self.btn_export.setIcon(icon("export", "#ffffff", 16, m))
        self.btn_theme.setIcon(icon("sun" if self.dark else "moon", c, 18))
        self.btn_rot.setIcon(icon("rotate", c, 17, m))
        self.btn_lock.setIcon(icon("lock", c, 17, m))
        self.btn_del.setIcon(icon("trash", c, 17, m))
        self.btn_prev.setIcon(icon("left", c, 17, m))
        self.btn_next.setIcon(icon("right", c, 17, m))
        self.btn_fit.setIcon(icon("fit", c, 17, m))
        self.banner_close.setIcon(icon("close", m, 14))
        self.empty_icon.setPixmap(pixmap("layers", t["accent"], 64, 1.5))
        self.parts_panel.refresh_icons()
        self.settings_panel.refresh_icons()

    def show_banner(self, text: str, kind: str = "info"):
        from .theme import tokens
        t = tokens()
        self.banner.setProperty("kind", kind)
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        self.banner_icon.setPixmap(pixmap("info" if kind == "info" else "warn",
                                          t["accent"] if kind == "info" else t["warn"], 18))
        self.banner_text.setText(text)
        self.banner.show()

    def _build_actions(self):
        mb = self.menuBar()
        m_file = mb.addMenu("&Arquivo")
        m_edit = mb.addMenu("&Editar")
        m_nest = mb.addMenu("E&ncaixe")
        m_view = mb.addMenu("E&xibir")
        m_help = mb.addMenu("Aj&uda")

        def act(menu, text, slot, shortcut=None, tip=None):
            a = QAction(text, self)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
                a.setShortcutContext(Qt.WindowShortcut)
            if tip:
                a.setStatusTip(tip)
            a.triggered.connect(slot)
            menu.addAction(a)
            self.addAction(a)
            return a

        act(m_file, "Abrir DXF…", self.open_dxf_dialog, "Ctrl+O")
        act(m_file, "Baixar solicitação da intranet…", lambda: self.open_intranet(), "Ctrl+I")
        act(m_file, "Limpar tudo", lambda: self.clear_all(), "Ctrl+Shift+Del")
        act(m_file, "Adicionar DXF…", lambda: self.open_dxf_dialog(add=True), "Ctrl+Shift+O")
        m_file.addSeparator()
        act(m_file, "Abrir projeto…", self.open_project_dialog, "Ctrl+Shift+P")
        act(m_file, "Salvar projeto", self.save_project, "Ctrl+S")
        act(m_file, "Salvar projeto como…", lambda: self.save_project(ask=True), "Ctrl+Shift+S")
        m_file.addSeparator()
        act(m_file, "Exportar para RDWorks…", self.export, "Ctrl+E")
        m_file.addSeparator()
        act(m_file, "Recuperar último trabalho (salvo automaticamente)", self.recover_autosave)
        act(m_file, "Limpar arquivos baixados e relatórios…", self.cleanup_files)
        m_file.addSeparator()
        act(m_file, "Sair", self.close, "Ctrl+Q")

        self.a_undo = act(m_edit, "Desfazer", self.undo, "Ctrl+Z")
        self.a_redo = act(m_edit, "Refazer", self.redo, "Ctrl+Y")
        m_edit.addSeparator()
        act(m_edit, "Girar peça selecionada", self.rotate_selected, "R")
        act(m_edit, "Espelhar peça selecionada", self.mirror_selected, "M")
        act(m_edit, "Travar/destravar posição", self.toggle_lock_selected, "L")
        act(m_edit, "Remover peça selecionada", self.delete_selected, "Del")
        act(m_edit, "Selecionar todas", self.select_all, "Ctrl+A")

        act(m_nest, "Iniciar/pausar encaixe", self.start_or_pause, "Space")
        act(m_nest, "Parar", self.stop_nest, "Esc")
        act(m_nest, "Destravar todas as peças", self.unlock_all)

        act(m_view, "Enquadrar tudo", self.canvas.fit_all, "F")
        act(m_view, "Placa anterior", lambda: self.goto_sheet(self.canvas.current_sheet() - 1), "PgUp")
        act(m_view, "Próxima placa", lambda: self.goto_sheet(self.canvas.current_sheet() + 1), "PgDown")
        act(m_view, "Tema claro/escuro", lambda: self.set_dark(not self.dark), "Ctrl+T")
        self.a_labels = act(m_view, "Mostrar nº em cima das peças", lambda: None, "N")
        self.a_labels.setCheckable(True)
        self.a_labels.setChecked(settings().value("ui/labels", "true") == "true")
        self.a_labels.toggled.connect(self.toggle_labels)
        self.canvas.show_labels = self.a_labels.isChecked()

        act(m_help, "Atalhos de teclado", self.show_shortcuts, "F1")
        act(m_help, "Sobre", self.show_about)

    # ------------------------------------------------------------------ tema/presets
    def set_dark(self, dark: bool):
        self.dark = dark
        apply_theme(QApplication.instance(), dark)
        settings().setValue("ui/dark", "true" if dark else "false")
        self._refresh_icons()
        self.canvas.set_dark(dark)
        self.parts_panel.dark = dark
        if self.parts:
            self.parts_panel.set_parts(self.parts, self.too_big)
        self._redraw(keep_view=True)

    def _refresh_presets(self):
        self.presets = load_presets()
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        for p in self.presets:
            self.preset_combo.addItem(f"{p['name']}  ({p['w']:g}×{p['h']:g})", p)
        self.preset_combo.addItem("Personalizada…", None)
        self.preset_combo.blockSignals(False)
        self._sync_preset_combo()

    def _sync_preset_combo(self):
        p = self.settings_panel.params()
        idx = -1
        for i, pr in enumerate(self.presets):
            if abs(pr["w"] - p.sheet_width) < 1e-6 and abs(pr["h"] - p.sheet_height) < 1e-6:
                idx = i
                if self.preset_combo.currentIndex() == i:
                    break
        last = self.preset_combo.count() - 1
        self.preset_combo.blockSignals(True)
        if idx >= 0:
            if self.preset_combo.currentIndex() < 0 or self.preset_combo.currentData() is None or \
                    abs(self.preset_combo.currentData()["w"] - p.sheet_width) > 1e-6 or \
                    abs(self.preset_combo.currentData()["h"] - p.sheet_height) > 1e-6:
                self.preset_combo.setCurrentIndex(idx)
        else:
            self.preset_combo.setItemText(last, f"Personalizada ({p.sheet_width:g}×{p.sheet_height:g})")
            self.preset_combo.setCurrentIndex(last)
        self.preset_combo.blockSignals(False)

    def _preset_chosen(self, i: int):
        data = self.preset_combo.itemData(i)
        if data is None:
            self.settings_panel.w.setFocus()
            self.settings_panel.w.selectAll()
            self.statusBar().showMessage("Digite a largura e a altura da placa no painel à direita.", 5000)
            return
        self.settings_panel.w.blockSignals(True)
        self.settings_panel.w.setValue(data["w"])
        self.settings_panel.w.blockSignals(False)
        self.settings_panel.h.setValue(data["h"])
        self.on_params_changed()

    def edit_presets(self):
        p = self.settings_panel.params()
        dlg = PresetsDialog(self.presets, p.sheet_width, p.sheet_height, self)
        if dlg.exec():
            save_presets(dlg.presets())
            self._refresh_presets()

    # ------------------------------------------------------------------ arquivos
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            if any(u.toLocalFile().lower().endswith((".dxf", ".dxfnest", ".sindri")) for u in e.mimeData().urls()):
                e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls()]
        projs = [p for p in paths if p.lower().endswith((".dxfnest", ".sindri"))]
        dxfs = [p for p in paths if p.lower().endswith(".dxf")]
        if projs:
            self.open_project(projs[0])
        elif dxfs:
            self.load_files(dxfs, add=bool(self.files))

    def open_dxf_dialog(self, add: bool = False):
        start = settings().value("ui/last_dir", os.path.expanduser("~"))
        paths, _ = QFileDialog.getOpenFileNames(self, "Abrir DXF", start, "Desenhos DXF (*.dxf);;Todos (*.*)")
        if paths:
            settings().setValue("ui/last_dir", os.path.dirname(paths[0]))
            self.load_files(paths, add=add)

    def load_files(self, paths: list[str], add: bool = False, multipliers: Optional[dict] = None,
                   request_label: Optional[str] = None, materials: Optional[dict] = None,
                   request_info: Optional[dict] = None, tags: Optional[dict] = None) -> bool:
        if self.worker is not None:
            self.stop_nest(wait=True)
        if not self.confirm_discard(add):
            return False
        old = (dict(self.file_multipliers), dict(self.file_materials), dict(self.file_tags), set(self.cut_sheets),
               set(self.done_parts), self.request_label, self.request_info)
        if not add:
            self.settings_panel.reset_file_options()   # arquivo novo: unidade automática, todas as camadas
            self.file_multipliers = {}
            self.file_materials = {}
            self.file_tags = {}
            self.cut_sheets, self.done_parts = set(), set()
            self.request_label = request_label
            self.request_info = request_info
            self.parts_panel.set_request(request_info)
        if multipliers:
            self.file_multipliers.update(multipliers)
        if materials:
            self.file_materials.update(materials)
        if tags:
            self.file_tags.update(tags)
        files = list(self.files) + [p for p in paths if p not in self.files] if add else list(paths)
        if self._import(files, keep_quantities=add) is False:
            # leitura falhou: volta ao estado anterior (as peças antigas continuam na tela)
            (self.file_multipliers, self.file_materials, self.file_tags, self.cut_sheets, self.done_parts,
             self.request_label, self.request_info) = old
            self.parts_panel.set_request(self.request_info)
            self._refresh_cut_panel()
            return False
        return True

    def _import(self, files: list[str], keep_quantities: bool = False):
        p = self.settings_panel.params()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            rep = import_files(files, p.join_tolerance, p.curve_tolerance, **p.import_kwargs(),
                               multipliers=self.file_multipliers, file_materials=self.file_materials,
                               file_tags=self.file_tags)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Erro ao abrir", f"Não foi possível ler os arquivos.\n\n{e}")
            return False
        finally:
            if QApplication.overrideCursor():
                QApplication.restoreOverrideCursor()
        old_q = {(pt.id, round(pt.area, 1)): pt.quantity for pt in self.parts} if keep_quantities else {}
        clear_graphics_cache()
        self.report = rep
        self.files = rep.files
        self.parts = rep.parts
        for pt in self.parts:
            q = old_q.get((pt.id, round(pt.area, 1)))
            if q is not None:
                pt.quantity = q
        self.pmap = {pt.id: pt for pt in self.parts}
        self.placements = []
        self.n_sheets = 0
        self.unplaced = []
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.dirty = True
        self._rebuild_checker()
        self.parts_panel.set_parts(self.parts, self.too_big)
        self.settings_panel.set_layers(rep.extra.get("layers", {}))
        self.banner.hide()
        self.tabs.blockSignals(True)
        self.tabs.setCurrentIndex(0)
        self.tabs.blockSignals(False)
        self.canvas.show_preview(rep.preview)
        names = ", ".join(os.path.basename(f) for f in self.files)
        self.setWindowTitle(f"{APP_NAME} — {names}" if names else APP_NAME)
        self._refresh_cut_panel()
        self._update_status()
        self._update_buttons()
        if self._ask_fix_units(rep):
            return
        if rep.warnings:
            self._show_warnings(rep.warnings)
        if not self.parts:
            self.statusBar().showMessage("Nenhuma peça encontrada nos arquivos.", 8000)
        else:
            total = sum(p.quantity for p in self.parts)
            self.statusBar().showMessage(
                f"{len(self.parts)} tipo(s) de peça, {total} no total. Escolha a placa e clique em Encaixar.", 10000)

    def _ask_fix_units(self, rep) -> bool:
        """Se o tamanho do desenho for absurdo na unidade declarada, corrige sozinho. True = reimportou."""
        from ..core.dxf_import import UNIT_NAMES
        sus = rep.extra.get("suspicious_units") or []
        if not sus or self.settings_panel.params().units_override >= 0:
            return False
        path, declared, suggested = sus[0]
        self.settings_panel.set_units(suggested)
        self._import(self.files, keep_quantities=False)
        self.show_banner(
            f"O arquivo “{os.path.basename(path)}” declara estar em "
            f"{UNIT_NAMES.get(declared, 'unidade indefinida')}, mas assim as peças ficariam com tamanho "
            f"absurdo. Usei <b>{UNIT_NAMES[suggested]}</b>. Se estiver errado, mude em "
            "Parâmetros › Arquivo DXF › Unidade.", "warn")
        return True

    def _save_params(self):
        settings().setValue("ui/last_params", json.dumps(self.settings_panel.params().to_json()))
        settings().setValue("autosave/clean", "true")      # fechou normalmente (salvou ou descartou)

    def open_intranet(self):
        """Abre a intranet, baixa a solicitação escolhida e carrega no encaixe (placas por material)."""
        from .intranet import ALL_MATERIALS, IntranetDialog, webengine_available
        from ..core.intranet import file_materials, file_multipliers, request_summary
        ok, err = webengine_available()
        if not ok:
            QMessageBox.critical(self, "Intranet", "O navegador embutido não está disponível nesta instalação."
                                 f"\n\n{err}")
            return
        if self.worker is not None:
            self.stop_nest(wait=True)
        dlg = getattr(self, "_intranet_dlg", None)
        if dlg is None:
            dlg = IntranetDialog(self)
            self._intranet_dlg = dlg
        dlg.chosen_material = None
        dlg.batch_result = None
        dlg.failed_files = []
        if not dlg.exec() or not dlg.chosen_material:
            return
        failed = list(getattr(dlg, "failed_files", []) or [])
        if dlg.batch_result:
            self._load_batch(dlg.batch_result, failed)
            return
        if dlg.detail is None:
            return
        d, choice = dlg.detail, dlg.chosen_material
        mats = d.materials()
        chosen = list(mats) if choice == ALL_MATERIALS else [choice]
        files = [f for m in chosen for f in mats.get(m, []) if f.local_path and os.path.isfile(f.local_path)]
        if not files:
            return
        info = request_summary(d, chosen)
        rm = info.get("rm") or ""
        label = f"{d.code}_RM{rm}" if rm else f"{d.code}"
        if not self.load_files([f.local_path for f in files], multipliers=file_multipliers(files),
                               request_label=label, materials=file_materials(files), request_info=info):
            return
        others = [f"{m} ({len(fs)} arquivo(s))" for m, fs in mats.items() if m not in chosen]
        txt = (f"Solicitação <b>{d.code}</b> · RM <b>{rm}</b> · {d.student} — "
               + ", ".join(f"<b>{m}</b>" for m in chosen)
               + ". Quantidades da intranet aplicadas; cada material ganha placas próprias.")
        if others:
            txt += " Não carregado: " + ", ".join(others) + "."
        if failed:
            self.show_banner(txt + f" <b>ATENÇÃO: {len(failed)} arquivo(s) não baixaram:</b> "
                             + ", ".join(failed[:6]) + ("…" if len(failed) > 6 else ""), "warn")
        elif not self.banner.isVisible():
            self.show_banner(txt, "info")
        self.setWindowTitle(f"{APP_NAME} — Solicitação {d.code} · RM {rm}")

    def _load_batch(self, items: list, failed: Optional[list] = None):
        """Várias solicitações encaixadas juntas (cada material com suas placas)."""
        from ..core.intranet import batch_label, batch_summary, file_materials, file_multipliers, file_tags
        items = [(d, [f for f in fs if f.local_path and os.path.isfile(f.local_path)]) for d, fs in items]
        missing = [str(d.code) for d, fs in items if not fs]
        items = [(d, fs) for d, fs in items if fs]
        if not items:
            return
        files = [f for _, fs in items for f in fs]
        info = batch_summary(items)
        if not self.load_files([f.local_path for f in files], multipliers=file_multipliers(files),
                               request_label=batch_label([d.code for d, _ in items]),
                               materials=file_materials(files), request_info=info, tags=file_tags(items)):
            return
        codes = ", ".join(str(d.code) for d, _ in items)
        txt = (f"Lote com <b>{len(items)}</b> solicitações ({codes}) — "
               + ", ".join(f"<b>{m}</b>" for m in info["materials"])
               + ". As peças estão juntas nas placas; o nome de cada peça começa com o nº da solicitação.")
        warn = []
        if missing:
            warn.append(f"solicitação(ões) sem nenhum arquivo baixado, NÃO incluídas: {', '.join(missing)}")
        if failed:
            warn.append(f"{len(failed)} arquivo(s) não baixaram: " + ", ".join(failed[:6])
                        + ("…" if len(failed) > 6 else ""))
        if warn:
            txt += " <b>ATENÇÃO:</b> " + "; ".join(warn) + "."
        self.show_banner(txt, "warn" if warn else "info")
        self.setWindowTitle(f"{APP_NAME} — Lote {codes}")

    def cleanup_files(self):
        """Apaga (Lixeira) as solicitações baixadas da intranet e os arquivos exportados."""
        from PySide6.QtCore import QFile
        from ..core.cleanup import (CUT_PATTERNS, REPORT_PATTERNS, downloaded_files, exported_files, human,
                                    remove_empty_dirs, total_size)
        from ..core.intranet import default_base_folder
        st = settings()
        base = st.value("intranet/folder", default_base_folder())
        hist = _str_list(st.value("export/history", []))
        dirs = [st.value("export/last_dir", "")]        # pasta da última exportação (+ o histórico)
        log = os.path.join(os.path.dirname(base), "intranet_log.txt")
        groups = [
            ("down", "Arquivos das solicitações baixadas da intranet", downloaded_files(base), True),
            ("rep", "Relatórios PDF exportados", exported_files(hist, dirs, REPORT_PATTERNS), True),
            ("cut", "Arquivos de corte exportados (…_todas_placas.dxf)", exported_files(hist, dirs, CUT_PATTERNS),
             False),
        ]
        if os.path.isfile(log):
            groups[0][2].append(log)
        if not any(g[2] for g in groups):
            QMessageBox.information(self, "Limpar arquivos", "Não há arquivos baixados nem relatórios para apagar.")
            return
        dlg = CleanupDialog(groups, self)
        if not dlg.exec():
            return
        files = dlg.chosen()
        loaded = {os.path.abspath(f) for f in self.files}
        if loaded & {os.path.abspath(f) for f in files}:
            if self.worker is not None:
                self.stop_nest(wait=True)
            if not self.confirm_discard():   # oferece salvar o encaixe/checklist antes
                return
            self.dirty = False
            self.clear_all(ask=False)        # as peças abertas vêm de arquivos que serão apagados
        size = total_size(files)
        failed = []
        for f in files:
            if not QFile.moveToTrash(f):
                try:
                    os.remove(f)
                except OSError:
                    failed.append(f)
        remove_empty_dirs(base)
        st.setValue("export/history", [h for h in hist if os.path.isfile(h)])
        if failed:
            QMessageBox.warning(self, "Limpar arquivos",
                                f"{len(files) - len(failed)} arquivo(s) apagados. Não consegui apagar "
                                f"{len(failed)} (talvez abertos em outro programa):\n\n" +
                                "\n".join(os.path.basename(f) for f in failed[:15]))
        else:
            self.statusBar().showMessage(f"{len(files)} arquivo(s) ({human(size)}) enviados para a Lixeira.", 8000)

    def clear_all(self, ask: bool = True):
        """Remove todos os arquivos, peças e o encaixe (volta para a tela inicial)."""
        if not self.parts and not self.files:
            return
        if ask:
            r = QMessageBox.question(self, "Limpar tudo",
                                     "Remover todas as peças e o encaixe atual?\n"
                                     "(os arquivos DXF no disco não são alterados)",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r != QMessageBox.Yes:
                return
        if self.worker is not None:
            self.stop_nest(wait=True)
        clear_graphics_cache()
        self.files, self.report, self.parts, self.pmap = [], None, [], {}
        self.file_multipliers, self.request_label = {}, None
        self.file_materials, self.request_info = {}, None
        self.file_tags = {}
        self.parts_panel.set_request(None)
        self.placements, self.n_sheets, self.unplaced = [], 0, []
        self.cut_sheets, self.done_parts = set(), set()
        self._refresh_cut_panel()
        self.too_big = set()
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.checker = None
        self.project_path = None
        self.dirty = False
        self.generation = 0
        self.settings_panel.reset_file_options()
        self.settings_panel.set_layers({})
        self.parts_panel.set_parts([])
        self.banner.hide()
        self.canvas.show_empty_hint()
        self.tabs.blockSignals(True)
        self.tabs.setCurrentIndex(0)
        self.tabs.blockSignals(False)
        self.setWindowTitle(APP_NAME)
        self._update_status()
        self._update_buttons()
        self.statusBar().showMessage("Tudo limpo. Abra ou arraste um novo DXF.", 6000)

    def reimport(self):
        if self.files:
            self._import(self.files, keep_quantities=True)

    def _show_warnings(self, warnings: list[str]):
        important = [w for w in warnings if "assumindo" in w or "não" in w.lower() or "aberto" in w
                     or "corromp" in w or "ignorad" in w or "duplicad" in w]
        if not important:
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Avisos da importação")
        box.setText("O arquivo foi aberto, mas há pontos de atenção:")
        box.setInformativeText("\n".join("• " + w for w in important[:12]) +
                               ("\n…" if len(important) > 12 else ""))
        box.exec()

    def confirm_discard(self, add: bool = False) -> bool:
        if add or not self.placements or not self.dirty:
            return True
        r = QMessageBox.question(self, "Descartar encaixe?",
                                 "O encaixe atual não foi salvo. Deseja continuar e descartá-lo?",
                                 QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        return r == QMessageBox.Yes

    # ------------------------------------------------------------------ projeto
    def save_project(self, ask: bool = False):
        if not self.files:
            QMessageBox.information(self, "Salvar projeto", "Abra um DXF primeiro.")
            return
        path = self.project_path
        if ask or not path:
            base = os.path.splitext(self.files[0])[0] + ".sindri"
            path, _ = QFileDialog.getSaveFileName(self, "Salvar projeto", base, "Projeto Sindri (*.sindri)")
            if not path:
                return
            if not path.lower().endswith((".sindri", ".dxfnest")):
                path += ".sindri"
        try:
            self._write_project(path)
        except OSError as e:
            QMessageBox.critical(self, "Erro ao salvar", f"Não foi possível salvar o projeto:\n{e}")
            return
        self.project_path = path
        self.dirty = False
        self.statusBar().showMessage(f"Projeto salvo em {path}", 6000)

    def _write_project(self, path: str):
        res = NestResult(self.placements, self.n_sheets, self._utilization(), 0.0, self.unplaced)
        save_project(path, self.files, self.settings_panel.params(), self.parts, res,
                     multipliers=self.file_multipliers, label=self.request_label,
                     materials=self.file_materials, request=self.request_info, tags=self.file_tags,
                     checklist={"cut": sorted(self.cut_sheets), "done": sorted(self.done_parts)})

    # ------------------------------------------------------------------ salvamento automático
    @staticmethod
    def autosave_path() -> str:
        from ..core.intranet import default_base_folder
        return os.path.join(os.path.dirname(default_base_folder()), "ultimo_trabalho.sindri")

    def schedule_autosave(self):
        if not hasattr(self, "_autosave_timer"):
            self._autosave_timer = QTimer(self)
            self._autosave_timer.setSingleShot(True)
            self._autosave_timer.timeout.connect(self.autosave)
        self._autosave_timer.start(2000)

    def autosave(self):
        """Guarda o trabalho atual (encaixe + checklist) para recuperar se o programa fechar sem salvar."""
        if not self.files or not self.placements or self.worker is not None:
            return
        try:
            path = self.autosave_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            self._write_project(path)
            settings().setValue("autosave/clean", "false")
            settings().setValue("autosave/when", _now_txt())
        except Exception:
            pass

    def recover_autosave(self, ask: bool = True):
        path = self.autosave_path()
        if not os.path.isfile(path):
            QMessageBox.information(self, "Recuperar", "Não há trabalho salvo automaticamente.")
            return
        self.open_project(path)
        if self.project_path == path:
            self.project_path = None          # "Salvar" pergunta onde guardar de verdade
            self.dirty = True
            self.statusBar().showMessage("Último trabalho recuperado. Use Salvar para guardar como projeto.", 8000)

    def check_autosave_on_start(self):
        if settings().value("autosave/clean", "true") == "true" or not os.path.isfile(self.autosave_path()):
            return
        when = settings().value("autosave/when", "")
        r = QMessageBox.question(self, "Recuperar trabalho",
                                 "O Sindri foi fechado sem salvar o último trabalho"
                                 + (f" ({when})" if when else "") + ".\nRecuperar o encaixe e o checklist de corte?",
                                 QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        settings().setValue("autosave/clean", "true")
        if r == QMessageBox.Yes:
            self.recover_autosave()

    def open_project_dialog(self):
        start = settings().value("ui/last_dir", os.path.expanduser("~"))
        path, _ = QFileDialog.getOpenFileName(self, "Abrir projeto", start, "Projeto Sindri (*.sindri *.dxfnest)")
        if path:
            self.open_project(path)

    def open_project(self, path: str):
        if self.worker is not None:
            self.stop_nest(wait=True)
        if not self.confirm_discard():
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            proj = load_project(path)
        except ProjectError as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Erro ao abrir projeto", str(e))
            return
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Erro ao abrir projeto", f"O projeto não pôde ser aberto:\n{e}")
            return
        QApplication.restoreOverrideCursor()
        clear_graphics_cache()
        self.settings_panel.set_params(proj.params)
        self.params = proj.params
        self._sync_preset_combo()
        self.report = proj.report
        self.files = proj.files
        self.file_multipliers = dict(proj.multipliers)
        self.file_materials = dict(proj.materials)
        self.file_tags = dict(getattr(proj, "tags", {}) or {})
        ck = getattr(proj, "checklist", None) or {}
        self.cut_sheets = set(int(x) for x in ck.get("cut", []))
        self.done_parts = set(str(x) for x in ck.get("done", []))
        self.request_label = proj.label
        self.request_info = proj.request
        self.parts_panel.set_request(proj.request)
        self.parts = proj.parts
        self.pmap = {p.id: p for p in self.parts}
        self.placements = list(proj.result.placements) if proj.result else []
        self.n_sheets = max([pl.sheet_index + 1 for pl in self.placements], default=0)
        self.unplaced = list(proj.result.unplaced) if proj.result else []
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.project_path = path
        self.dirty = False
        self._rebuild_checker()
        self.parts_panel.set_parts(self.parts, self.too_big)
        self.settings_panel.set_layers((proj.report.extra or {}).get("layers", {}) if proj.report else {})
        self.setWindowTitle(f"{APP_NAME} — {os.path.basename(path)}")
        self._refresh_cut_panel()
        if self.placements:
            self.tabs.blockSignals(True)
            self.tabs.setCurrentIndex(1)
            self.tabs.blockSignals(False)
            self._redraw()
        else:
            self.canvas.show_preview(self.report.preview)
        self._update_status()
        self._update_buttons()
        if proj.warnings:
            QMessageBox.warning(self, "Projeto aberto com avisos", "\n".join(proj.warnings))

    # ------------------------------------------------------------------ parâmetros/peças
    def on_params_changed(self):
        new = self.settings_panel.params()
        old = self.params
        self.params = new
        self._sync_preset_combo()
        settings().setValue("ui/last_params", json.dumps(new.to_json()))
        if self.files and abs(new.curve_tolerance - old.curve_tolerance) > 1e-9 and self.worker is None:
            # a discretização das peças depende da tolerância de curva: reprocessar
            self._import(self.files, keep_quantities=True)
            return
        if self.parts:
            self._rebuild_checker()
            self.parts_panel.set_parts(self.parts, self.too_big)
            if self.placements:
                self._redraw(keep_view=(old.sheet_width == new.sheet_width and old.sheet_height == new.sheet_height))
                if self.worker is None:
                    self.statusBar().showMessage("Parâmetros alterados — clique em Encaixar para refazer o encaixe.", 6000)
        self._update_status()

    def _rebuild_checker(self):
        p = self.settings_panel.params()
        self.checker = CollisionChecker(self.parts, p) if self.parts else None
        self.too_big = set()
        if self.parts:
            dec = Decoder(shapes_from_parts(self.parts, p), p)
            mo = (False, True) if p.allow_mirror else (False,)
            for pt in self.parts:
                rots = [0.0] if pt.rotation_locked else p.rotations()
                if not dec.fits_sheet(pt.id, rots, mo):
                    self.too_big.add(pt.id)

    def on_quantity_changed(self, pid: str, q: int):
        part = self.pmap.get(pid)
        if part is None:
            return
        self._push_undo()
        part.quantity = q
        before = len(self.placements)
        self.placements = [pl for pl in self.placements if not (pl.part_id == pid and pl.instance >= q)]
        self.dirty = True
        if len(self.placements) != before:
            self._redraw(keep_view=True)
        self.parts_panel.update_summary()
        self._update_status()

    def on_rotation_lock_changed(self, pid: str, locked: bool):
        part = self.pmap.get(pid)
        if part:
            part.rotation_locked = locked
            self._rebuild_checker()
            self.dirty = True

    def multiply_kits(self):
        if not self.parts:
            return
        n, ok = QInputDialog.getInt(self, "Quantidade de kits",
                                    "Quantos kits completos? (as quantidades do arquivo serão multiplicadas)",
                                    1, 1, 999)
        if ok:
            self._push_undo()
            for p in self.parts:
                p.quantity = p.file_quantity * n
                self.parts_panel.set_quantity(p.id, p.quantity)
            self.parts_panel.update_summary()
            self._update_status()
            self.dirty = True

    def reset_quantities(self):
        self._push_undo()
        for p in self.parts:
            p.quantity = p.file_quantity
            self.parts_panel.set_quantity(p.id, p.quantity)
        self.placements = [pl for pl in self.placements if pl.instance < self.pmap[pl.part_id].quantity]
        self._redraw(keep_view=True)
        self.parts_panel.update_summary()
        self._update_status()

    def on_part_selected(self, pid: str):
        if self.canvas.mode != "layout":
            return
        self.canvas.scene().blockSignals(True)
        self.canvas.scene().clearSelection()
        for it in self.canvas.part_items:
            if it.part.id == pid:
                it.setSelected(True)
        self.canvas.scene().blockSignals(False)

    def _canvas_selection(self):
        sel = self.canvas.selected_items()
        if len(sel) == 1:
            self.parts_panel.select_part(sel[0].part.id)

    # ------------------------------------------------------------------ desenho/estado
    def _tab_changed(self, i: int):
        if i == 0:
            if self.report:
                self.canvas.show_preview(self.report.preview)
            else:
                self.canvas.show_empty_hint()
        else:
            self._redraw()
        self._update_buttons()

    def _redraw(self, keep_view: bool = False):
        if self.tabs.currentIndex() != 1:
            return
        if not self.parts:
            self.canvas.show_empty_hint()
            return
        n = max(1, self.n_sheets, max([pl.sheet_index + 1 for pl in self.placements], default=0))
        self.n_sheets = max(self.n_sheets, max([pl.sheet_index + 1 for pl in self.placements], default=0))
        self.canvas.owner_colors = owner_colors(self.parts)
        self.canvas.cut_sheets = self.cut_sheets
        self.canvas.done_parts = self.done_parts
        self.canvas.show_layout(self.pmap, self.placements, self.settings_panel.params(), n, keep_view)
        self.canvas.set_editable(self.worker is None)
        self._refresh_cut_panel()
        self._mark_collisions()
        self._update_sheet_label()

    # ------------------------------------------------------------------ checklist de corte
    def _refresh_cut_panel(self):
        """Atualiza, na aba Peças, as caixinhas das placas e as peças feitas (e a cor no desenho)."""
        from ..core.dxf_export import sheet_material
        sheets = []
        if self.placements:
            nums = sheet_numbers(self.pmap, self.placements)
            for si, n in sorted(nums.items(), key=lambda kv: kv[1]):
                sheets.append({"si": si, "n": n, "material": sheet_material(self.pmap, self.placements, si),
                               "count": sum(1 for pl in self.placements if pl.sheet_index == si)})
        self.parts_panel.set_sheets(sheets, self.cut_sheets)
        self.parts_panel.set_done(self.done_parts)
        tags = {p.tag for p in self.parts if p.tag}
        done_tags = {t for t in tags if all(p.id in self.done_parts for p in self.parts if p.tag == t)}
        if self.request_info:
            self.parts_panel.set_request(self.request_info, done_tags)
        self.canvas.done_parts = self.done_parts
        if self.canvas.mode == "layout":
            for it in self.canvas.part_items:
                it.update()

    def on_part_done(self, pid: str, on: bool):
        (self.done_parts.add if on else self.done_parts.discard)(pid)
        self.dirty = True
        self._refresh_cut_panel()
        self.schedule_autosave()
        if on and self.parts and all(p.id in self.done_parts for p in self.parts):
            self.statusBar().showMessage("Todas as peças feitas! 🎉", 8000)

    def on_sheet_cut(self, si: int, on: bool):
        """Placa cortada: as peças que só aparecem em placas cortadas ficam como feitas."""
        (self.cut_sheets.add if on else self.cut_sheets.discard)(si)
        on_sheet = {pl.part_id for pl in self.placements if pl.sheet_index == si}
        for pid in on_sheet:
            sheets_of = {pl.sheet_index for pl in self.placements if pl.part_id == pid}
            if on and sheets_of <= self.cut_sheets:
                self.done_parts.add(pid)
            elif not on:
                self.done_parts.discard(pid)
        self.dirty = True
        if self.canvas.mode == "layout":
            self.canvas.set_cut(self.cut_sheets)
        self._refresh_cut_panel()
        self.schedule_autosave()
        nums = sheet_numbers(self.pmap, self.placements)
        if on and len(self.cut_sheets) >= len(nums):
            self.statusBar().showMessage("Todas as placas cortadas! 🎉", 8000)

    def reset_checklist(self):
        self.cut_sheets.clear()
        self.done_parts.clear()
        if self.canvas.mode == "layout":
            self.canvas.set_cut(self.cut_sheets)
        self._refresh_cut_panel()

    def _show_sheet(self, si: int):
        if self.tabs.currentIndex() != 1:
            self.tabs.setCurrentIndex(1)
        self.goto_sheet(si)

    def toggle_labels(self, on: bool):
        self.canvas.show_labels = on
        settings().setValue("ui/labels", "true" if on else "false")
        self.canvas.viewport().update()

    def _mark_collisions(self) -> int:
        if not self.checker or not self.canvas.part_items:
            return 0
        pls = [it.placement for it in self.canvas.part_items]
        bad = self.checker.colliding(pls)
        for i, it in enumerate(self.canvas.part_items):
            c = i in bad
            if it.colliding != c:
                it.colliding = c
                it.update()
        return len(bad)

    def _update_sheet_label(self):
        n = max(1, self.n_sheets)
        cur = min(self.canvas.current_sheet(), n - 1) + 1 if self.canvas.mode == "layout" else 0
        if self.canvas.mode == "layout":
            self.sheet_label.setText(f"  Placa {cur} de {n}  ")
        else:
            self.sheet_label.setText("")
        on = self.canvas.mode == "layout" and n > 1
        self.btn_prev.setEnabled(on)
        self.btn_next.setEnabled(on)

    def goto_sheet(self, i: int):
        if self.canvas.mode != "layout":
            return
        n = max(1, self.n_sheets)
        i = max(0, min(n - 1, i))
        self.canvas.focus_sheet(i)
        self.sheet_label.setText(f"  Placa {i + 1} de {n}  ")

    def _utilization(self) -> float:
        if not self.placements:
            return 0.0
        p = self.settings_panel.params()
        n = max(pl.sheet_index for pl in self.placements) + 1
        used = {pl.sheet_index for pl in self.placements}
        area = sum(self.pmap[pl.part_id].outer.area - sum(h.area for h in self.pmap[pl.part_id].holes)
                   for pl in self.placements)
        return area / (len(used) * p.sheet_width * p.sheet_height) if used else 0.0

    def _update_status(self):
        total = sum(p.quantity for p in self.parts)
        placed = len(self.placements)
        u = self._utilization()
        self.progress.setValue(int(round(u * 1000)))
        self.chip_util.setText(fmt_pct(u) if placed else "—")
        sheets = len({pl.sheet_index for pl in self.placements})
        self.chip_sheets.setText(str(sheets) if placed else "—")
        from ..core.dxf_export import sheet_groups
        groups = sheet_groups(self.pmap, self.placements) if placed else []
        if len(groups) > 1 or (groups and groups[0][0]):
            self.chip_sheets.setText(" + ".join(str(len(sis)) for _, sis in groups) if len(groups) > 1
                                     else str(sheets))
            self.chip_sheets.setToolTip("\n".join(f"{m or 'sem material'}: {len(sis)} placa(s)" for m, sis in groups))
        self.chip_parts.setText(f"{placed}/{total}" if total else "—")
        ev = getattr(self, "evaluated", 0)
        self.chip_gen.setText(f"{ev:,}".replace(",", ".") if (self.worker is not None or ev) else "—")
        missing = total - placed
        if self.worker is not None:
            paused = self.worker.pause_event.is_set()
            kind, txt = "run", ("Pausado" if paused else f"Otimizando… geração {self.generation}")
        elif not self.parts:
            kind, txt = "", "Sem arquivo"
        elif self.too_big:
            kind, txt = "warn", f"⚠ {len(self.too_big)} tipo(s) maior(es) que a placa"
        elif self.placements and missing > 0:
            kind, txt = "warn", f"⚠ {missing} sem lugar"
        elif self.placements:
            kind, txt = "ok", "✓ Pronto para exportar"
        else:
            kind, txt = "", "Clique em Encaixar"
        self.status_label.setProperty("kind", kind)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        self.status_label.setText(txt)

    def _update_buttons(self):
        running = self.worker is not None
        has_parts = bool(self.parts)
        self.btn_nest.setEnabled(has_parts and not running)
        self.btn_nest.setText("Encaixar" if not self.placements else "Encaixar de novo")
        self.btn_pause.setEnabled(running)
        self.btn_stop.setEnabled(running)
        self.btn_export.setEnabled(bool(self.placements) and not running)
        self.btn_save.setEnabled(has_parts)
        self.parts_panel.btn_clear.setEnabled(has_parts)
        self.a_undo.setEnabled(bool(self.undo_stack) and not running)
        self.a_redo.setEnabled(bool(self.redo_stack) and not running)
        paused = running and self.worker.pause_event.is_set()
        self.btn_pause.setText("Continuar" if paused else "Pausar")
        from .theme import tokens
        self.btn_pause.setIcon(icon("play" if paused else "pause", tokens()["text"], 14, tokens()["muted"]))
        editing = self.canvas.mode == "layout" and not running and bool(self.placements)
        for b in (self.btn_rot, self.btn_lock, self.btn_del):
            b.setEnabled(editing)
        self.stack.setCurrentIndex(1 if self.parts else 0)
        self._update_sheet_label()

    # ------------------------------------------------------------------ desfazer
    def _snapshot(self):
        return (copy.deepcopy(self.placements), self.n_sheets, {p.id: p.quantity for p in self.parts},
                list(self.unplaced), set(self.cut_sheets))

    def _restore(self, snap):
        pls, n, qty, unplaced = snap[:4]
        if len(snap) > 4:
            self.cut_sheets = set(snap[4])
        self.placements = copy.deepcopy(pls)
        self.n_sheets = n
        self.unplaced = list(unplaced)
        for p in self.parts:
            if p.id in qty:
                p.quantity = qty[p.id]
                self.parts_panel.set_quantity(p.id, p.quantity)
        self.parts_panel.update_summary()
        if self.placements and self.tabs.currentIndex() != 1:
            self.tabs.setCurrentIndex(1)
        else:
            self._redraw(keep_view=True)
        self._update_status()
        self._update_buttons()

    def _push_undo(self):
        self.undo_stack.append(self._snapshot())
        if len(self.undo_stack) > MAX_UNDO:
            self.undo_stack.pop(0)
        self.redo_stack.clear()
        self._update_buttons()

    def undo(self):
        if self.worker is not None or not self.undo_stack:
            return
        self.redo_stack.append(self._snapshot())
        self._restore(self.undo_stack.pop())
        self.dirty = True

    def redo(self):
        if self.worker is not None or not self.redo_stack:
            return
        self.undo_stack.append(self._snapshot())
        self._restore(self.redo_stack.pop())
        self.dirty = True

    # ------------------------------------------------------------------ encaixe
    def start_or_pause(self):
        if self.worker is not None:
            self.toggle_pause()
            return
        self.start_nest()

    def start_nest(self):
        if not self.parts or self.worker is not None:
            return
        if sum(pt.quantity for pt in self.parts) == 0:
            QMessageBox.information(self, "Encaixar", "Todas as quantidades estão em zero.")
            return
        keep_cut = False
        if self.cut_sheets and self.placements:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Question)
            box.setWindowTitle("Encaixar de novo")
            box.setText(f"{len(self.cut_sheets)} placa(s) já estão marcadas como cortadas.")
            box.setInformativeText("“Só o que falta” mantém as placas cortadas como estão e reorganiza apenas "
                                   "as outras peças (sem usar as placas já cortadas).\n"
                                   "“Tudo de novo” reorganiza tudo e desmarca o checklist das placas.")
            b_rest = box.addButton("Só o que falta", QMessageBox.AcceptRole)
            b_all = box.addButton("Tudo de novo", QMessageBox.DestructiveRole)
            box.addButton("Cancelar", QMessageBox.RejectRole)
            box.setDefaultButton(b_rest)
            box.exec()
            if box.clickedButton() == b_rest:
                keep_cut = True
            elif box.clickedButton() != b_all:
                return
        p = self.settings_panel.params()
        self.params = p
        self._cut_keys = set()
        if keep_cut:
            # peças das placas cortadas ficam travadas onde estão; essas placas não recebem peças novas
            self._cut_keys = {(pl.part_id, pl.instance) for pl in self.placements if pl.sheet_index in self.cut_sheets}
            for pl in self.placements:
                if (pl.part_id, pl.instance) in self._cut_keys:
                    pl.locked = True
            p = copy.copy(p)
            p.closed_sheets = sorted(self.cut_sheets)
        locked = [pl for pl in self.placements if pl.locked and pl.instance < self.pmap[pl.part_id].quantity]
        if self.placements:
            self._push_undo()
        self.placements = list(locked)
        self.n_sheets = max([pl.sheet_index + 1 for pl in locked], default=0)
        self.generation = 0
        self.evaluated = 0
        self._layout_shown_once = False
        self.worker = NestWorker(self.parts, p, locked, workers=self.workers)
        self.worker.bestFound.connect(self.on_best)
        self.worker.progress.connect(self.on_progress)
        self.worker.failed.connect(self.on_failed)
        self.worker.finished.connect(lambda w=self.worker: self.on_finished(w))
        self.worker.start()
        self.tabs.blockSignals(True)
        self.tabs.setCurrentIndex(1)
        self.tabs.blockSignals(False)
        self._redraw()
        self.canvas.set_editable(False)
        self.statusBar().showMessage("Calculando… a melhor solução aparece e vai melhorando. Clique em Parar quando estiver bom.")
        self._update_buttons()
        self._update_status()

    def on_best(self, res: NestResult):
        if self.worker is None:
            return
        self.placements = list(res.placements)
        self.unplaced = list(res.unplaced)
        keys = getattr(self, "_cut_keys", set())
        if keys:                         # "só o que falta": as placas cortadas continuam marcadas
            self.cut_sheets = {pl.sheet_index for pl in self.placements if (pl.part_id, pl.instance) in keys}
        else:
            self.cut_sheets.clear()      # encaixe novo: as placas mudam (entregas continuam valendo)
        self.n_sheets = max(res.sheets_used, max([pl.sheet_index + 1 for pl in self.placements], default=0))
        self._redraw(keep_view=self._layout_shown_once)
        self._layout_shown_once = True
        self._update_status()

    def on_progress(self, info: dict):
        self.generation = info.get("generation", 0)
        self.evaluated = info.get("evaluated", 0)
        self._update_status()

    def on_failed(self, msg: str):
        QMessageBox.critical(self, "Erro no encaixe",
                             "Ocorreu um erro inesperado durante o encaixe. A melhor solução até agora foi mantida.\n\n"
                             + msg.split("\n\n")[0])

    def on_finished(self, w=None):
        if w is not None and w is not self.worker:
            return                        # sinal atrasado de um encaixe antigo
        self.worker = None
        self.dirty = True
        self.canvas.set_editable(True)
        self.schedule_autosave()
        self._update_buttons()
        self._update_status()
        missing = sum(p.quantity for p in self.parts) - len(self.placements)
        msg = "Encaixe concluído." if missing <= 0 else f"Encaixe concluído — {missing} peça(s) não couberam."
        self.statusBar().showMessage(msg + " Ao cortar, marque as placas e as peças feitas na lista de Peças.",
                                     12000)

    def toggle_pause(self):
        if self.worker is None:
            return
        paused = not self.worker.pause_event.is_set()
        self.worker.set_paused(paused)
        self._update_buttons()
        self._update_status()

    def stop_nest(self, wait: bool = False):
        if self.worker is None:
            return
        self.worker.stop()
        self.statusBar().showMessage("Parando… mantendo a melhor solução.", 3000)
        if wait:
            if not self.worker.wait(30000):
                # ainda terminando o cálculo: guarda a referência até a thread acabar (senão o Qt aborta)
                old = self.worker
                self._old_workers = [x for x in getattr(self, "_old_workers", []) if x.isRunning()] + [old]
            self.on_finished()

    def unlock_all(self):
        if any(pl.locked for pl in self.placements):
            self._push_undo()
            for pl in self.placements:
                pl.locked = False
            self._redraw(keep_view=True)

    # ------------------------------------------------------------------ edição manual
    def _check_drag_collisions(self):
        if not self.checker:
            return
        pls = []
        for it in self.canvas.part_items:
            pl = copy.copy(it.placement)
            if it.isSelected():
                s = self.canvas.sheet_at(it.pos().x())
                pl.sheet_index = s
                pl.x = it.pos().x() - sheet_offset(self.canvas.params, s)
                pl.y = it.pos().y()
            pls.append(pl)
        bad = self.checker.colliding(pls)
        for i, it in enumerate(self.canvas.part_items):
            c = i in bad
            if c != it.colliding:
                it.colliding = c
                it.update()

    def on_items_released(self):
        moved = []
        for it in self.canvas.selected_items():
            s = self.canvas.sheet_at(it.pos().x())
            s = min(s, max(self.n_sheets, 1))  # no máximo uma placa nova
            x = it.pos().x() - sheet_offset(self.canvas.params, s)
            y = it.pos().y()
            pl = it.placement
            if s != pl.sheet_index or abs(x - pl.x) > 1e-6 or abs(y - pl.y) > 1e-6:
                moved.append((it, s, x, y))
        if not moved:
            return
        self._push_undo()
        grow = False
        for it, s, x, y in moved:
            it.placement.sheet_index = s
            it.placement.x = x
            it.placement.y = y
            if s >= self.n_sheets:
                grow = True
        self._compact_sheets()
        self.dirty = True
        self.schedule_autosave()
        if grow or any(it.placement.sheet_index != s for it, s, _, _ in moved):
            self._redraw(keep_view=True)
        else:
            for it, *_ in moved:
                it.sync_from_placement()
            self._mark_collisions()
        self._update_status()

    def _compact_sheets(self):
        """Remove placas vazias e renumera na ordem por material (a mesma do arquivo 'todas as placas',
        do relatório e do checklist), levando junto as marcações de 'cortada'."""
        nums = sheet_numbers(self.pmap, self.placements)          # índice -> nº (1..N) por material
        remap = {s: n - 1 for s, n in nums.items()}
        for pl in self.placements:
            pl.sheet_index = remap.get(pl.sheet_index, pl.sheet_index)
        self.cut_sheets = {remap[s] for s in self.cut_sheets if s in remap}
        self.n_sheets = len(nums)

    def _selected_placements(self) -> list[Placement]:
        if self.worker is not None or self.canvas.mode != "layout":
            return []
        return [it.placement for it in self.canvas.selected_items()]

    def rotate_selected(self):
        sel = self._selected_placements()
        if not sel:
            return
        p = self.settings_panel.params()
        steps = len(p.rotations())
        step = 90.0 if steps <= 4 else 360.0 / steps
        self._push_undo()
        for pl in sel:
            if self.pmap[pl.part_id].rotation_locked:
                self.statusBar().showMessage("Esta peça está com rotação travada.", 4000)
                continue
            pl.rotation = round((pl.rotation + step) % 360.0, 4)
        self.dirty = True
        for it in self.canvas.selected_items():
            it.sync_from_placement()
        self._mark_collisions()

    def mirror_selected(self):
        sel = self._selected_placements()
        if not sel:
            return
        if not self.settings_panel.params().allow_mirror:
            self.statusBar().showMessage("Ative “Permitir espelhar peças” nos parâmetros para espelhar.", 5000)
            return
        self._push_undo()
        for pl in sel:
            pl.mirrored = not pl.mirrored
        self.dirty = True
        self._redraw(keep_view=True)

    def toggle_lock_selected(self):
        sel = self._selected_placements()
        if not sel:
            return
        self._push_undo()
        new = not all(pl.locked for pl in sel)
        for pl in sel:
            pl.locked = new
        for it in self.canvas.selected_items():
            it.update()
        self.statusBar().showMessage(
            "Peça(s) travada(s): ficarão no lugar no próximo encaixe." if new else "Peça(s) destravada(s).", 5000)

    def delete_selected(self):
        sel = self._selected_placements()
        if not sel:
            return
        self._push_undo()
        for pl in sorted(sel, key=lambda q: -q.instance):
            self.placements.remove(pl)
            part = self.pmap[pl.part_id]
            part.quantity = max(0, part.quantity - 1)
            for other in self.placements:
                if other.part_id == pl.part_id and other.instance > pl.instance:
                    other.instance -= 1
            self.parts_panel.set_quantity(part.id, part.quantity)
        self._compact_sheets()
        self.dirty = True
        self._redraw(keep_view=True)
        self.parts_panel.update_summary()
        self._update_status()

    def select_all(self):
        for it in self.canvas.part_items:
            it.setSelected(True)

    def move_selected_to_sheet(self, target: int):
        sel = self._selected_placements()
        if not sel:
            return
        self._push_undo()
        for pl in sel:
            pl.sheet_index = target
        self._compact_sheets()
        self.dirty = True
        self._redraw(keep_view=True)
        self._update_status()

    def show_item_menu(self, items, pos):
        if self.worker is not None:
            return
        m = QMenu(self)
        m.addAction("Girar (R)", self.rotate_selected)
        a = m.addAction("Espelhar (M)", self.mirror_selected)
        a.setEnabled(self.settings_panel.params().allow_mirror)
        locked = all(it.placement.locked for it in items)
        m.addAction("Destravar posição (L)" if locked else "Travar posição (L)", self.toggle_lock_selected)
        sub = m.addMenu("Mover para placa")
        from ..core.dxf_export import sheet_material
        cur = {it.placement.sheet_index for it in items}
        sel_mats = {it.part.material for it in items}
        for i in range(max(1, self.n_sheets)):
            sm = sheet_material(self.pmap, self.placements, i)
            act = sub.addAction(f"Placa {i + 1}" + (f" · {sm}" if sm else ""),
                                lambda i=i: self.move_selected_to_sheet(i))
            act.setEnabled(not (cur == {i}) and (not sm or sel_mats <= {sm}))
        sub.addAction("Nova placa", lambda: self.move_selected_to_sheet(self.n_sheets))
        m.addSeparator()
        m.addAction("Remover (Del)", self.delete_selected)
        m.exec(pos)

    # ------------------------------------------------------------------ exportação
    def export(self):
        if not self.placements or self.worker is not None:
            return
        p = self.settings_panel.params()
        if self._mark_collisions():
            r = QMessageBox.warning(self, "Peças em conflito",
                                    "Há peças sobrepostas ou fora da placa (em vermelho).\n"
                                    "Exportar mesmo assim?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r != QMessageBox.Yes:
                return
        else:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                issues = validate_layout(self.pmap, self.placements, p)
            finally:
                QApplication.restoreOverrideCursor()
            if issues:
                r = QMessageBox.warning(self, "Verificação final",
                                        "A verificação com a geometria exata encontrou problemas:\n\n" +
                                        "\n".join("• " + i for i in issues[:10]) + "\n\nExportar mesmo assim?",
                                        QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if r != QMessageBox.Yes:
                    return
        missing = sum(pt.quantity for pt in self.parts) - len(self.placements)
        if missing > 0:
            r = QMessageBox.question(self, "Peças sem lugar",
                                     f"{missing} peça(s) não estão no encaixe. Exportar assim mesmo?",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if r != QMessageBox.Yes:
                return
        folder = settings().value("export/last_dir", os.path.dirname(self.files[0]) if self.files else os.getcwd())
        base = self.request_label or os.path.splitext(os.path.basename(self.project_path or self.files[0]))[0]
        dlg = ExportDialog(folder, base, len({pl.sheet_index for pl in self.placements}), self)
        if not dlg.exec():
            return
        o = dlg.options()
        targets = [os.path.join(o["folder"], f"{o['base']}_todas_placas.dxf"),
                   os.path.join(o["folder"], f"{o['base']}_relatorio.pdf")]
        exist = [t for t in targets if os.path.exists(t)]
        if exist:
            r = QMessageBox.question(self, "Substituir arquivos?",
                                     "Já existem na pasta:\n\n" + "\n".join(os.path.basename(t) for t in exist) +
                                     "\n\nSubstituir? (marcações feitas no PDF antigo serão perdidas)",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r != QMessageBox.Yes:
                return
        settings().setValue("export/last_dir", o["folder"])
        self._compact_sheets()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            # só dois arquivos: todas as placas organizadas num DXF (abre no RDWorks) + relatório
            files = [export_all_sheets(self.parts, self.placements, p, o["folder"], o["base"], o["version"],
                                       sheet_outline=o["outline"], inner_first=o["inner"], sort_path=o["path"])]
            res = NestResult(self.placements, self.n_sheets, self._utilization(), 0.0, self.unplaced)
            pdf = os.path.join(o["folder"], f"{o['base']}_relatorio.pdf")
            export_pdf(pdf, o["base"], self.pmap, res, p, header=self._report_header(),
                       requests=self._report_requests())
            files.append(pdf)
            hist = _str_list(settings().value("export/history", []))
            settings().setValue("export/history", (hist + [f for f in files if f not in hist])[-500:])
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Erro ao exportar", f"Não foi possível salvar os arquivos:\n{e}")
            return
        QApplication.restoreOverrideCursor()
        opened = self._open_in_rdworks(files) if o.get("open_rdworks") else ""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle("Exportado")
        box.setText(f"{len(files)} arquivo(s) salvos em:\n{o['folder']}" + (f"\n\n{opened}" if opened else ""))
        box.setDetailedText("\n".join(os.path.basename(f) for f in files))
        if o["outline"]:
            box.setInformativeText("IMPORTANTE: no RDWorks, na camada cinza (contorno e nº das placas), coloque "
                                   "saída = NÃO para o laser não passar por ela.")
        open_btn = box.addButton("Abrir pasta", QMessageBox.ActionRole)
        box.addButton("OK", QMessageBox.AcceptRole)
        box.exec()
        if box.clickedButton() == open_btn:
            QDesktopServices.openUrl(QUrl.fromLocalFile(o["folder"]))

    def _open_in_rdworks(self, files: list[str]) -> str:
        """Abre o DXF exportado no RDWorks. Devolve uma frase para a mensagem final."""
        targets = files_to_open(files)
        if not targets:
            return ""
        st = settings()
        exe = find_rdworks(st.value("rdworks/exe", "") or None)
        if not exe:
            QMessageBox.information(self, "Onde está o RDWorks?",
                                    "Não encontrei o RDWorks neste computador.\n"
                                    "Mostre onde está o RDWorksV8.exe (só precisa fazer isso uma vez).")
            exe, _ = QFileDialog.getOpenFileName(self, "Localizar o RDWorks",
                                                 os.environ.get("ProgramFiles(x86)", "C:\\"),
                                                 "Programa (*.exe)")
            if not exe:
                return "O RDWorks não foi aberto (programa não localizado)."
        st.setValue("rdworks/exe", exe)
        path = targets[0]
        QApplication.clipboard().setText(os.path.abspath(path))
        try:
            launch(exe, path)
        except OSError as e:
            if not os.path.isfile(exe):
                st.remove("rdworks/exe")
            return f"Não foi possível abrir o RDWorks: {e}"
        msg = (f"Abrindo {os.path.basename(path)} no RDWorks "
               "(o Windows pode pedir permissão — o RDWorks roda como administrador).")
        msg += ("\nSe ele abrir vazio: Arquivo › Importar (Ctrl+I), Ctrl+V e Enter "
                "— o caminho do arquivo já está copiado.")
        return msg

    # ------------------------------------------------------------------ ajuda
    def show_shortcuts(self):
        QMessageBox.information(self, "Atalhos de teclado", (
            "Ctrl+O\tAbrir DXF\n"
            "Ctrl+S\tSalvar projeto\n"
            "Espaço\tIniciar / pausar encaixe\n"
            "Esc\tParar encaixe\n"
            "R\tGirar peça selecionada\n"
            "M\tEspelhar peça selecionada\n"
            "L\tTravar/destravar posição\n"
            "Del\tRemover peça\n"
            "Ctrl+Z / Ctrl+Y\tDesfazer / refazer\n"
            "Ctrl+E\tExportar para RDWorks\n"
            "F\tEnquadrar tudo\n"
            "PgUp / PgDn\tPlaca anterior / próxima\n\n"
            "Mouse: roda = zoom · botão do meio (ou Alt+arrastar) = mover a vista ·\n"
            "arrastar peça = mover (fica vermelha se colidir) · botão direito = mais opções"))

    def show_about(self):
        QMessageBox.about(self, "Sobre o Sindri", (
            "<b>Sindri</b> — encaixe automático de peças para corte a laser.<br><br>"
            "Lê DXF, organiza as peças com No-Fit Polygon + algoritmo genético (mesma abordagem do "
            "SVGnest/Deepnest) e exporta DXF pronto para o RDWorks, preservando arcos, círculos, "
            "camadas e cores."))

    # ------------------------------------------------------------------
    def closeEvent(self, e):
        if self.worker is not None:
            self.stop_nest(wait=True)
        if self.dirty and self.placements:
            r = QMessageBox.question(self, "Sair", "Salvar o projeto antes de sair?",
                                     QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                                     QMessageBox.Save)
            if r == QMessageBox.Cancel:
                e.ignore()
                return
            if r == QMessageBox.Save:
                self.save_project()
                if self.dirty:
                    e.ignore()
                    return
        settings().setValue("ui/last_params", json.dumps(self.settings_panel.params().to_json()))
        dlg = getattr(self, "_intranet_dlg", None)
        if dlg is not None:  # a página do navegador precisa sair antes do perfil
            dlg.page.deleteLater()
            dlg.deleteLater()
            self._intranet_dlg = None
            QApplication.processEvents()
        e.accept()
