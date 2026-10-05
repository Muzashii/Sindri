"""Montagem da janela: barra superior, painéis, menus, ícones, tema e avisos."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTabBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..canvas import NestCanvas
from ..dialogs import settings
from ..icons import icon, pixmap
from ..parts_panel import PartsPanel
from ..settings_panel import SettingsPanel
from ..theme import apply_theme


class UIBuildMixin:
    def _build_ui(self):
        # ---------------- barra superior (ações principais sempre visíveis)
        top = QFrame()
        top.setObjectName("TopBar")
        tl = QHBoxLayout(top)
        tl.setContentsMargins(14, 8, 14, 8)
        tl.setSpacing(8)
        from ..icons import app_icon
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
        self.btn_nest.clicked.connect(self._nest_button)
        self.btn_pause = QPushButton("Pausar")
        self.btn_pause.setToolTip("Pausa/continua a otimização (Espaço)")
        self.btn_pause.clicked.connect(self.toggle_pause)
        self.btn_stop = QPushButton("Parar")
        self.btn_stop.setToolTip("Para e mantém a melhor solução encontrada (Esc)")
        self.btn_stop.clicked.connect(lambda: self.stop_nest())
        self.btn_export = QPushButton("Exportar para RDWorks")
        self.btn_export.setObjectName("success")
        self.btn_export.setToolTip("Gera o arquivo com todas as placas + relatório e abre no RDWorks (Ctrl+E).\n"
                                   "Ctrl+Shift+E: escolher pasta e opções.")
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
        self.banner_text.setTextFormat(Qt.RichText)
        self.banner_text.linkActivated.connect(self._banner_link)
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
        self.canvas.sheetCutClicked.connect(lambda si: self.on_sheet_cut(si, si not in self.cut_sheets))
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
        self.parts_panel.requestFilter.connect(self.on_request_filter)
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
        from ..theme import tokens
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
        from ..theme import tokens
        t = tokens()
        self.banner.setProperty("kind", kind)
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        icon_name, col = {"info": ("info", t["accent"]), "ok": ("check", "#16a34a")}.get(kind, ("warn", t["warn"]))
        self.banner_icon.setPixmap(pixmap(icon_name, col, 18))
        self.banner_text.setText(text)
        self.banner.show()
        self._banner_seq = getattr(self, "_banner_seq", 0) + 1
        if kind == "info":                     # avisos informativos somem sozinhos
            seq = self._banner_seq
            QTimer.singleShot(9000, lambda: self.banner.hide() if self._banner_seq == seq else None)

    def _banner_link(self, href: str):
        from urllib.parse import unquote
        if href.startswith("open:"):
            QDesktopServices.openUrl(QUrl.fromLocalFile(unquote(href[5:])))
        elif href == "opts:":
            self.export(ask=True)
        elif href == "recover:":
            self.banner.hide()
            self.recover_autosave()
        elif href == "discard:":
            self.banner.hide()
            settings().setValue("autosave/clean", "true")

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
        act(m_file, "Exportar para RDWorks", self.export, "Ctrl+E")
        act(m_file, "Exportar com opções…", self.export_with_options, "Ctrl+Shift+E")
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
        m_nest.addSeparator()
        act(m_nest, "Marcar placa da tela como cortada (e ir para a próxima)", self.toggle_current_cut, "C")
        act(m_view, "Mostrar todas as solicitações do lote", lambda: self.filter_request_index(0), "Alt+0")
        from PySide6.QtGui import QShortcut
        self._req_shortcuts = []
        for k in range(1, 10):            # Alt+1…9: só a solicitação N do lote
            sc = QShortcut(QKeySequence(f"Alt+{k}"), self)
            sc.activated.connect(lambda k=k: self.filter_request_index(k))
            self._req_shortcuts.append(sc)

        act(m_view, "Enquadrar tudo", self.canvas.fit_all, "F")
        act(m_view, "Placa anterior", lambda: self.goto_sheet(self.canvas.current_sheet() - 1), "PgUp")
        act(m_view, "Próxima placa", lambda: self.goto_sheet(self.canvas.current_sheet() + 1), "PgDown")
        act(m_view, "Tema claro/escuro", lambda: self.set_dark(not self.dark), "Ctrl+T")
        self.a_params = act(m_view, "Painel de parâmetros", lambda: None, "Ctrl+P")
        self.a_params.setCheckable(True)
        self.a_params.setChecked(settings().value("ui/params_visible", "true") == "true")
        self.a_params.toggled.connect(self.toggle_params)
        self.settings_panel.setVisible(self.a_params.isChecked())
        m_view.addSeparator()
        self.a_labels = act(m_view, "Mostrar nº em cima das peças", lambda: None, "N")
        self.a_labels.setCheckable(True)
        self.a_labels.setChecked(settings().value("ui/labels", "true") == "true")
        self.a_labels.toggled.connect(self.toggle_labels)
        self.canvas.show_labels = self.a_labels.isChecked()

        act(m_help, "Atalhos de teclado", self.show_shortcuts, "F1")
        act(m_help, "Sobre", self.show_about)

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

    def toggle_params(self, on: bool):
        self.settings_panel.setVisible(on)
        settings().setValue("ui/params_visible", "true" if on else "false")

    def toggle_labels(self, on: bool):
        self.canvas.show_labels = on
        settings().setValue("ui/labels", "true" if on else "false")
        self.canvas.viewport().update()

    def show_shortcuts(self):
        QMessageBox.information(self, "Atalhos de teclado", (
            "Ctrl+O\tAbrir DXF\n"
            "Ctrl+Shift+O\tAdicionar DXF\n"
            "Ctrl+I\tIntranet FIAP\n"
            "Ctrl+S\tSalvar projeto\n"
            "Espaço\tEncaixar / pausar\n"
            "Esc\tParar encaixe\n"
            "Ctrl+E\tExportar (direto, opções da última vez)\n"
            "Ctrl+Shift+E\tExportar com opções\n"
            "C\tMarcar placa da tela como cortada → próxima\n"
            "Alt+1…9 / Alt+0\tSó a solicitação N do lote / todas\n"
            "N\tMostrar/ocultar nº nas peças\n"
            "Ctrl+P\tMostrar/ocultar parâmetros\n"
            "Ctrl+T\tTema claro/escuro\n"
            "Ctrl+A\tSelecionar todas as peças\n"
            "R\tGirar peça selecionada\n"
            "M\tEspelhar peça selecionada\n"
            "L\tTravar/destravar posição\n"
            "Del\tRemover peça\n"
            "Ctrl+Z / Ctrl+Y\tDesfazer / refazer\n"
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
