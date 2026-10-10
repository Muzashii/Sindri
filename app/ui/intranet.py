"""Janela "Intranet FIAP" do Sindri.

Fluxo:
1. O navegador embutido abre a página de Solicitações Maker (o usuário faz login uma vez;
   a sessão fica salva — o programa nunca vê a senha).
2. Clicar numa solicitação só VISUALIZA (lê o modal da página): aluno, RM, projeto, arquivos.
3. Os arquivos só são baixados ao clicar em "Enviar para a placa" (tudo ou um material).
"""
from __future__ import annotations

import json
import os
import uuid
from typing import Optional

from PySide6.QtCore import QStandardPaths, Qt, QTimer, QUrl
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QAbstractItemView, QApplication, QComboBox, QDialog, QFileDialog,
                               QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QScrollArea, QSplitter, QStackedWidget,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..core.intranet import (ALL_STATUS, JS_CRAWL_RESET, JS_CRAWL_STATE, crawl_script, date_key,
                             merge_rows, status_options, INTRANET_URL, JS_DETAIL, JS_LIST, JS_OPEN, RequestDetail,
                             default_base_folder, parse_detail, should_go_to_requests, target_path)
from . import theme
from .dialogs import settings
from .icons import icon, pixmap

_PROFILE = None
ALL_MATERIALS = "__todos__"
DOWNLOAD_TIMEOUT_S = 90


def webengine_available() -> tuple[bool, str]:
    try:
        from PySide6.QtWebEngineCore import QWebEngineProfile  # noqa: F401
        from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
        return True, ""
    except Exception as e:  # pragma: no cover - depende da instalação
        return False, str(e)


def shared_profile():
    """Perfil persistente (cookies/sessão salvos), único para o programa todo."""
    global _PROFILE
    if _PROFILE is None:
        from PySide6.QtWebEngineCore import QWebEngineProfile
        base = os.environ.get("SINDRI_DATA_DIR") or QStandardPaths.writableLocation(QStandardPaths.AppDataLocation) or os.path.expanduser("~/.sindri")
        path = os.path.join(base, "navegador")
        os.makedirs(path, exist_ok=True)
        prof = QWebEngineProfile("dxfnest", QApplication.instance())   # nome mantido: preserva o login salvo
        prof.setPersistentStoragePath(path)
        prof.setCachePath(os.path.join(path, "cache"))
        prof.setPersistentCookiesPolicy(QWebEngineProfile.ForcePersistentCookies)
        _PROFILE = prof
    return _PROFILE


def _chip(text: str, color: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("MatChip")
    lab.setStyleSheet(f"background: {color};")
    return lab


class IntranetDialog(QDialog):
    def __init__(self, parent=None, start_url: str = INTRANET_URL, base_folder: Optional[str] = None):
        super().__init__(parent)
        from PySide6.QtWebEngineCore import QWebEnginePage
        from PySide6.QtWebEngineWidgets import QWebEngineView
        self.setWindowTitle("Sindri — Intranet FIAP · Solicitações Maker")
        self.resize(1480, 880)
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        self.start_url = start_url
        self.base_folder = base_folder or settings().value("intranet/folder", default_base_folder())
        self.detail: Optional[RequestDetail] = None
        self.chosen_material: Optional[str] = None
        self._rows: list[dict] = []
        self._logged = False
        self._auto_redirects = 0
        self._req_seq = 0          # cancela visualizações antigas quando outra é pedida
        self._pending: dict[str, object] = {}
        self._downloads = []
        self._sending: Optional[str] = None
        self._checked: list[str] = []              # solicitações marcadas para juntar (ordem do clique)
        self.open_codes: set[str] = set()          # já abertas no Sindri (aparecem marcadas, sem baixar de novo)
        self._cache: dict[int, RequestDetail] = {}  # detalhes já lidos
        self.batch: list[RequestDetail] = []         # lote em exibição/envio
        self.batch_result: Optional[list] = None     # [(detalhe, [arquivos baixados])]
        self._fetch_queue: list[int] = []
        self.file_rows: dict[str, QLabel] = {}
        self.failed_files: list[str] = []
        t = theme.tokens()

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        # ---------- cabeçalho
        head = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(pixmap("layers", t["accent"], 22))
        title = QLabel("Solicitações Maker")
        title.setObjectName("Logo")
        head.addWidget(logo)
        head.addWidget(title)
        head.addSpacing(10)
        self.state = QLabel("Conectando à intranet…")
        self.state.setObjectName("State")
        head.addWidget(self.state)
        head.addStretch(1)
        self.btn_go = QPushButton("Ir para Solicitações Maker")
        self.btn_go.setToolTip("Abre a página de Solicitações Maker na intranet")
        self.btn_go.clicked.connect(self.go_requests)
        head.addWidget(self.btn_go)
        self.btn_page = QPushButton("Página da intranet")
        self.btn_page.setCheckable(True)
        self.btn_page.setToolTip("Mostra/oculta o site da intranet (use para fazer login)")
        self.btn_page.toggled.connect(self._toggle_page)
        self.btn_reload = QPushButton("Atualizar")
        self.btn_reload.setIcon(icon("reset", t["text"], 15))
        self.btn_reload.setToolTip("Recarrega a lista de solicitações")
        self.btn_reload.clicked.connect(self.reload_page)
        head.addWidget(self.btn_page)
        head.addWidget(self.btn_reload)
        root.addLayout(head)

        self.split = QSplitter(Qt.Horizontal)
        self.split.setChildrenCollapsible(False)

        # ---------- lista
        lcard = QFrame()
        lcard.setObjectName("Card")
        ll = QVBoxLayout(lcard)
        ll.setContentsMargins(12, 12, 12, 12)
        ll.setSpacing(8)
        lt = QLabel("Fila de pedidos")
        lt.setObjectName("SectionHead")
        ll.addWidget(lt)
        frow = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setAccessibleName("Buscar solicitações por nome, RM ou número")
        self.search.setPlaceholderText("Buscar por nome, RM ou nº…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._fill_list)
        self.type_filter = QComboBox()
        self.type_filter.addItems(["Corte Laser", "Todos os tipos", "Impressão 3D"])
        self.type_filter.setToolTip("Mostrar só um tipo de solicitação")
        tsaved = settings().value("intranet/type_filter", "Corte Laser")
        if self.type_filter.findText(tsaved) >= 0:
            self.type_filter.setCurrentText(tsaved)
        self.type_filter.currentIndexChanged.connect(self._fill_list)
        self.type_filter.currentTextChanged.connect(lambda t: settings().setValue("intranet/type_filter", t))
        self.status_filter = QComboBox()
        self.status_filter.addItem("Aguardando")
        self.status_filter.setToolTip("Status da solicitação (abas do site: Aguardando, Em execução…)")
        self.status_filter.setMinimumWidth(150)
        self.status_filter.currentIndexChanged.connect(self._status_changed)
        ll.addWidget(self.search)
        frow.addWidget(self.status_filter, 1)
        frow.addWidget(self.type_filter, 1)
        ll.addLayout(frow)
        self.list = QTableWidget(0, 4)
        self.list.setHorizontalHeaderLabels(["Nº", "Aluno", "RM", "Data"])
        self.list.verticalHeader().setVisible(False)
        self.list.verticalHeader().setDefaultSectionSize(34)
        self.list.setShowGrid(False)
        self.list.setAlternatingRowColors(True)
        self.list.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh = self.list.horizontalHeader()
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        for c in (0, 2, 3):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.list.cellClicked.connect(self._cell_clicked)
        self.list.cellDoubleClicked.connect(lambda r, c: self._cell_clicked(r, 1))
        self.list.installEventFilter(self)
        from PySide6.QtGui import QKeySequence, QShortcut
        QShortcut(QKeySequence("Ctrl+F"), self, activated=lambda: (self.search.setFocus(), self.search.selectAll()))
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self.send_shown)
        self.search.setPlaceholderText("Buscar por nome, RM ou nº…  (Ctrl+F)")
        self.list.itemChanged.connect(self._item_checked)
        hh.setSectionsClickable(True)
        hh.sectionClicked.connect(self._header_clicked)
        self._set_date_header()
        ll.addWidget(self.list, 1)
        # barra do lote (aparece quando há solicitações marcadas)
        self.batch_bar = QFrame()
        self.batch_bar.setObjectName("RequestCard")
        bl = QHBoxLayout(self.batch_bar)
        bl.setContentsMargins(10, 6, 6, 6)
        self.batch_lbl = QLabel("")
        self.batch_lbl.setStyleSheet("font-weight: 700;")
        self.btn_batch_clear = QPushButton("Limpar")
        self.btn_batch_clear.setToolTip("Desmarcar todas")
        self.btn_batch_clear.clicked.connect(self.clear_checks)
        self.btn_batch = QPushButton("Juntar na placa")
        self.btn_batch.setObjectName("primary")
        self.btn_batch.setIcon(icon("layers", "#ffffff", 14))
        self.btn_batch.setToolTip("Mostra as solicitações marcadas juntas para enviar todas de uma vez")
        self.btn_batch.clicked.connect(self.view_batch)
        bl.addWidget(self.batch_lbl, 1)
        bl.addWidget(self.btn_batch_clear)
        bl.addWidget(self.btn_batch)
        self.batch_bar.hide()
        ll.addWidget(self.batch_bar)
        tip = QLabel("Marque a caixinha ao lado do nº para juntar várias solicitações nas mesmas placas.")
        tip.setObjectName("Muted")
        tip.setWordWrap(True)
        self.batch_tip = tip
        ll.addWidget(tip)
        nrow = QHBoxLayout()
        self.code = QLineEdit()
        self.code.setPlaceholderText("Nº da solicitação")
        self.code.returnPressed.connect(self.view_request)
        self.btn_view = QPushButton("Visualizar")
        self.btn_view.setIcon(icon("info", t["text"], 15))
        self.btn_view.setToolTip("Mostra os dados e arquivos da solicitação (não baixa nada)")
        self.btn_view.clicked.connect(self.view_request)
        nrow.addWidget(self.code, 1)
        nrow.addWidget(self.btn_view)
        ll.addLayout(nrow)
        self.count_lbl = QLabel("")
        self.count_lbl.setObjectName("Muted")
        ll.addWidget(self.count_lbl)
        self.split.addWidget(lcard)

        # ---------- detalhes
        dcard = QFrame()
        dcard.setObjectName("Card")
        dl = QVBoxLayout(dcard)
        dl.setContentsMargins(0, 0, 0, 0)
        self.dstack = QStackedWidget()
        dl.addWidget(self.dstack)
        # vazio
        empty = QWidget()
        el = QVBoxLayout(empty)
        el.addStretch(1)
        ei = QLabel()
        ei.setPixmap(pixmap("info", t["accent"], 46, 1.5))
        ei.setAlignment(Qt.AlignCenter)
        e1 = QLabel("Selecione uma solicitação")
        e1.setAlignment(Qt.AlignCenter)
        e1.setStyleSheet("font-size: 13pt; font-weight: 700;")
        self.empty_hint = QLabel("Os dados e os arquivos aparecem aqui. Nada é baixado até você\n"
                                 "clicar em “Enviar para a placa”.")
        self.empty_hint.setObjectName("Muted")
        self.empty_hint.setAlignment(Qt.AlignCenter)
        for w in (ei, e1, self.empty_hint):
            el.addWidget(w)
        el.addStretch(2)
        self.dstack.addWidget(empty)
        # detalhe
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        scroll.setWidget(body)
        self.dbody = QVBoxLayout(body)
        self.dbody.setContentsMargins(18, 16, 18, 16)
        self.dbody.setSpacing(12)
        self.dstack.addWidget(scroll)
        self.split.addWidget(dcard)

        # ---------- navegador
        self.view = QWebEngineView()
        self.page = QWebEnginePage(shared_profile(), self.view)
        self.view.setPage(self.page)
        self.page.loadFinished.connect(self._loaded)
        self.view.setMinimumWidth(420)
        self.split.addWidget(self.view)
        self.split.setSizes([430, 620, 0])
        self.view.hide()
        root.addWidget(self.split, 1)

        # ---------- rodapé
        foot = QHBoxLayout()
        self.folder_lbl = QLabel()
        self.folder_lbl.setObjectName("Muted")
        bfold = QPushButton("Pasta…")
        bfold.setToolTip("Onde os arquivos enviados para a placa são guardados")
        bfold.clicked.connect(self._choose_folder)
        self.auto_nest = QCheckBox("Encaixar automaticamente ao enviar")
        self.auto_nest.setToolTip("Depois de baixar, o Sindri escolhe a placa usada da última vez para o material "
                                  "e já começa o encaixe.")
        self.auto_nest.setChecked(settings().value("intranet/auto_nest", "true") == "true")
        self.auto_nest.toggled.connect(lambda v: settings().setValue("intranet/auto_nest", "true" if v else "false"))
        foot.addWidget(self.folder_lbl, 1)
        foot.addWidget(self.auto_nest)
        foot.addWidget(bfold)
        root.addLayout(foot)
        self._update_folder_label()

        self._fetch = QTimer(self)
        self._fetch.setInterval(300)
        self._fetch.timeout.connect(self._fetch_poll)
        self._poll = QTimer(self)
        self._poll.setInterval(300)
        self._poll.timeout.connect(self._poll_detail)
        self._crawl = QTimer(self)
        self._crawl.setInterval(400)
        self._crawl.timeout.connect(self._poll_crawl)
        self._base_rows: list[dict] = []
        self._crawl_rows: dict[str, list[dict]] = {}     # status -> linhas lidas das outras páginas
        self._crawled: set[str] = set()
        self._crawl_status = ""
        self._sort_desc = True
        self._dl_timer = QTimer(self)
        self._dl_timer.setSingleShot(True)
        self._dl_timer.timeout.connect(self._download_timeout)
        self.view.setUrl(QUrl(start_url))

    # ------------------------------------------------------------------ estado / página
    def _set_state(self, text: str, kind: str = ""):
        self.state.setText(text)
        from .accessibility import announce
        announce(self.state, text)
        self.state.setProperty("kind", kind)
        self.state.style().unpolish(self.state)
        self.state.style().polish(self.state)

    def _toggle_page(self, show: bool):
        self.view.setVisible(show)
        if show:
            sizes = self.split.sizes()
            self.split.setSizes([360, 520, max(560, sizes[2])])

    def _update_folder_label(self):
        self.folder_lbl.setText(f"Arquivos enviados para a placa ficam em:  {self.base_folder}")

    def _choose_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Pasta para as solicitações", self.base_folder)
        if d:
            self.base_folder = d
            settings().setValue("intranet/folder", d)
            self._update_folder_label()

    def go_requests(self):
        self._auto_redirects = 0
        self._set_state("Abrindo Solicitações Maker…", "run")
        self.view.setUrl(QUrl(self.start_url))

    def reload_page(self):
        self._set_state("Atualizando…", "run")
        self._crawl.stop()
        self._crawl_rows = {}
        self._crawled = set()
        self._cache.clear()
        if self._logged:
            self.view.reload()
        else:
            self.view.setUrl(QUrl(self.start_url))

    def _loaded(self, ok: bool):
        if not ok:
            self._set_state("Falha ao carregar a intranet. Verifique a conexão ou abra a página e clique em Atualizar.", "warn")
            return
        # pequena espera: algumas páginas terminam de montar a tabela depois do load
        QTimer.singleShot(400, self.refresh_list)

    def refresh_list(self):
        self.page.runJavaScript(JS_LIST, 0, self._got_list)

    def _got_list(self, raw):
        try:
            data = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            self._set_state("Não foi possível ler a página. Abra a intranet e tente Atualizar.", "warn")
            self.btn_page.setChecked(True)
            return
        if not isinstance(data, dict):
            self._set_state("Resposta da página não reconhecida. Tente Atualizar.", "warn")
            return
        if not data.get("temFuncao"):
            # logado mas na página inicial da intranet: vai sozinho para Solicitações Maker
            if should_go_to_requests(data.get("url", ""), bool(data.get("temSenha")), self.start_url) \
                    and self._auto_redirects < 3:
                self._auto_redirects += 1
                self._set_state("Login feito · abrindo Solicitações Maker…", "run")
                self.view.setUrl(QUrl(self.start_url))
                return
            self._logged = False
            self._set_state("Faça login na intranet ao lado" if data.get("temSenha") else
                            "Página não reconhecida. Abra Solicitações Maker ou Atualizar; confira seu acesso no site.", "warn")
            self.btn_page.setChecked(True)
            self._rows = []
            self._fill_list()
            return
        self._logged = True
        self._auto_redirects = 0
        self._base_rows = list(data.get("solicitacoes", []))
        self._merge_and_show()
        self._start_crawl()

    def _all_crawl_rows(self) -> list[dict]:
        return [r for rows in self._crawl_rows.values() for r in rows]

    def _merge_and_show(self, reading: str = ""):
        rows = merge_rows(self._base_rows + self._all_crawl_rows())
        changed = [r["codigo"] for r in rows] != [r["codigo"] for r in self._rows]
        self._rows = rows
        if changed or not reading:
            self._update_status_options()
            self._fill_list()
        if reading:
            self._set_state(f"Conectado · lendo todas as páginas de {reading}…", "run")
        else:
            self._set_state(f"Conectado · {len(rows)} solicitações", "ok")

    def _status_changed(self, *_):
        if self.status_filter.currentData():
            settings().setValue("intranet/status_filter", self.status_filter.currentData())
        self._fill_list()
        self._start_crawl()

    def _start_crawl(self):
        """Lê todas as páginas da aba escolhida (o site mostra só 10 por vez)."""
        status = self.status_filter.currentData() or ""
        if not self._logged or self._crawl.isActive() or not status or status == ALL_STATUS \
                or status in self._crawled:
            return
        self._crawl_status = status
        self.page.runJavaScript(JS_CRAWL_RESET, 0)
        self.page.runJavaScript(crawl_script(status), 0)
        self._crawl.start()

    def _poll_crawl(self):
        self.page.runJavaScript(JS_CRAWL_STATE, 0, self._got_crawl)

    def _got_crawl(self, raw):
        try:
            data = json.loads(raw) if raw else None
        except (TypeError, ValueError):
            data = None
        if not data:
            self._crawl.stop()
            self._merge_and_show()
            return
        status = data.get("status") or self._crawl_status
        self._crawl_rows[status] = list(data.get("rows") or [])
        if data.get("done"):
            self._crawl.stop()
            if not data.get("error"):
                self._crawled.add(status)              # só conta como lida se terminou bem
            self._write_log(status, data)
            self._merge_and_show()
            QTimer.singleShot(0, self._start_crawl)       # status trocado durante a leitura
        else:
            self._merge_and_show(data.get("progress") or status)

    def _log_dl(self, msg: str):
        """Registro dos downloads (vai junto no intranet_log.txt)."""
        import datetime as _dt
        self._dl_lines = (getattr(self, "_dl_lines", []) + [f"{_dt.datetime.now():%H:%M:%S} {msg}"])[-300:]
        self._logs = getattr(self, "_logs", {})
        self._logs["~downloads"] = "== downloads\n" + "\n".join(self._dl_lines)
        self._flush_log()

    def _flush_log(self):
        try:
            folder = os.path.dirname(self.base_folder) or self.base_folder
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, "intranet_log.txt"), "w", encoding="utf-8") as fh:
                fh.write("\n\n".join(self._logs.values()) + "\n")
        except OSError:
            pass

    def _write_log(self, status: str, data: dict):
        """Registro da leitura das páginas (ajuda a diagnosticar se o site mudar)."""
        lines = [f"== {status}: {len(data.get('rows') or [])} linhas lidas"]
        if data.get("error"):
            lines.append("erro durante a leitura da página")
        self._logs = getattr(self, "_logs", {})
        self._logs[status] = "\n".join(lines)
        self._flush_log()

    def _update_status_options(self):
        """Recria a lista de status com as abas encontradas, mantendo a escolha atual."""
        cur = self.status_filter.currentData() or settings().value("intranet/status_filter", "Aguardando")
        opts = status_options(self._rows) + [ALL_STATUS]
        self.status_filter.blockSignals(True)
        self.status_filter.clear()
        for o in opts:
            n = sum(1 for r in self._rows if r.get("situacao") == o) if o != ALL_STATUS else len(self._rows)
            self.status_filter.addItem(f"{o} ({n})", o)
        i = self.status_filter.findData(cur)
        if i < 0:
            i = self.status_filter.findData("Aguardando")
        self.status_filter.setCurrentIndex(max(0, i))
        self.status_filter.blockSignals(False)

    def _fill_list(self, *_):
        q = self.search.text().strip().lower()
        tf = self.type_filter.currentText()
        sf = self.status_filter.currentData() or self.status_filter.currentText()
        cur = self.code.text().strip()
        scroll = self.list.verticalScrollBar().value()
        self.list.blockSignals(True)
        self.list.setRowCount(0)
        shown = 0
        t = theme.tokens()
        rows = sorted(self._rows, key=lambda r: date_key(r.get("data", "")), reverse=self._sort_desc)
        seals = self._known_seals()
        for r in rows:
            if tf != "Todos os tipos" and r.get("tipo", "") != tf:
                continue
            if sf != ALL_STATUS and r.get("situacao", "") != sf and not q:
                continue          # a busca procura em todos os status
            if q and q not in f"{r['codigo']} {r.get('nome', '')} {r.get('rm', '')}".lower():
                continue
            i = self.list.rowCount()
            self.list.insertRow(i)
            is_open = str(r["codigo"]) in self.open_codes
            vals = [r["codigo"], r.get("nome", ""), r.get("rm", ""), r.get("data", "").split(" ")[0]]
            if is_open:
                vals[1] = f"{vals[1]}   ✓ no Sindri"
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setToolTip(f"{r.get('tipo', '')} · {r.get('situacao', '')} · {r.get('data', '')}"
                              + ("\nJá está aberta no Sindri: as peças dela já estão nas placas." if is_open else ""))
                if is_open:
                    it.setBackground(QColor(t["done_bg"]))
                if c == 0:
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                    seal = seals.get(str(r["codigo"]))
                    if seal is not None:          # selo de quando ela já foi aberta no Sindri
                        it.setIcon(pixmap("check" if seal.status == "ok" else "warn",
                                          t["success_text"] if seal.status == "ok" else
                                          (t["danger"] if seal.status == "bloqueado" else t["warn"]), 14))
                        it.setToolTip(it.toolTip() + f"\nSelo: {seal.label}"
                                      + "".join(f"\n• {x}" for x in seal.reasons))
                    if is_open:      # marcada e travada: já está no lote aberto, não entra de novo
                        it.setFlags(it.flags() & ~Qt.ItemIsUserCheckable)
                        it.setCheckState(Qt.Checked)
                    else:
                        it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                        it.setCheckState(Qt.Checked if str(v) in self._checked else Qt.Unchecked)
                self.list.setItem(i, c, it)
            if r["codigo"] == cur:
                self.list.selectRow(i)
            shown += 1
        self.list.blockSignals(False)
        self.list.verticalScrollBar().setValue(scroll)
        self.count_lbl.setText(f"{shown} de {len(self._rows)} solicitação(ões)" if self._rows else "")

    def _set_date_header(self):
        arrow = "▼" if getattr(self, "_sort_desc", True) else "▲"
        it = self.list.horizontalHeaderItem(3)
        if it:
            it.setText(f"Data {arrow}")
            it.setToolTip("Ordenado pela data de envio — clique para inverter")

    def _header_clicked(self, col: int):
        if col == 0:
            vis = [self.list.item(i, 0).text() for i in range(self.list.rowCount())
                   if self.list.item(i, 0).text() not in self.open_codes]
            if vis and all(c in self._checked for c in vis):
                self._checked = [c for c in self._checked if c not in vis]
            else:
                self._checked += [c for c in vis if c not in self._checked]
            self._fill_list()
            self._update_batch_bar()
            return
        if col == 3:
            self._sort_desc = not self._sort_desc
            self._set_date_header()
            self._fill_list()

    def eventFilter(self, obj, ev):
        """Teclado na fila: Enter = visualizar · Espaço = marcar/desmarcar · Ctrl+Enter = enviar."""
        from PySide6.QtCore import QEvent
        if obj is self.list and ev.type() == QEvent.KeyPress:
            r = self.list.currentRow()
            if ev.key() in (Qt.Key_Return, Qt.Key_Enter):
                if ev.modifiers() & Qt.ControlModifier:
                    self.send_shown()
                elif r >= 0:
                    self._cell_clicked(r, 1)
                return True
            if ev.key() == Qt.Key_Space and r >= 0 and self.list.item(r, 0):
                it = self.list.item(r, 0)
                if it.text() in self.open_codes:
                    return True
                it.setCheckState(Qt.Unchecked if it.checkState() == Qt.Checked else Qt.Checked)
                return True
        return super().eventFilter(obj, ev)

    def send_shown(self):
        """Ctrl+Enter: envia o lote marcado (lendo-o se preciso) ou a solicitação aberta (tudo)."""
        if self._checked and not self.batch:
            self.view_batch()
            return
        if self.batch or self.detail is not None:
            self.send(ALL_MATERIALS)

    def _cell_clicked(self, row: int, col: int):
        """Clicar na linha visualiza; clicar na 1ª coluna (caixinha + nº) só marca/desmarca."""
        it = self.list.item(row, 0)
        if it is None:
            return
        if col == 0:
            return              # a própria caixinha cuida da marcação (itemChanged)
        self.code.setText(it.text())
        self.view_request()

    # ------------------------------------------------------------------ visualizar
    def view_request(self):
        txt = self.code.text().strip()
        if not txt.isdigit():
            return
        if self._sending or self._fetch_queue:
            return
        self._req_seq += 1
        self._code = int(txt)
        self._set_state(f"Abrindo a solicitação {self._code}…", "run")
        self._poll.stop()
        seq = self._req_seq
        self.page.runJavaScript(JS_OPEN % self._code, 0, lambda res, s=seq: self._opened(res, s))

    def _opened(self, res, seq):
        if seq != self._req_seq:
            return
        if res != "ok":
            self._set_state("A intranet ainda não está pronta — faça login e abra Solicitações Maker", "warn")
            self.btn_page.setChecked(True)
            return
        self._poll_left = 60
        self._poll_seq = seq
        self._poll.start()

    def _poll_detail(self):
        self._poll_left -= 1
        if self._poll_left < 0:
            self._poll.stop()
            self._set_state("A solicitação não abriu a tempo. Tente de novo.", "warn")
            return
        seq = self._poll_seq
        self.page.runJavaScript(JS_DETAIL % self._code, 0, lambda raw, s=seq: self._got_detail(raw, s))

    def _got_detail(self, raw, seq):
        if not raw or seq != self._req_seq or not self._poll.isActive():
            return
        self._poll.stop()
        try:
            self.detail = parse_detail(json.loads(raw))
        except Exception as e:
            self._set_state(f"Não consegui ler a solicitação: {e}", "warn")
            return
        self._cache[self.detail.code] = self.detail
        self.batch = []
        self._show_detail()
        self._set_state(f"Solicitação {self.detail.code} · pronta para enviar", "ok")

    def _clear_detail(self):
        while self.dbody.count():
            it = self.dbody.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()
            elif it.layout():
                self._clear_layout(it.layout())

    def _clear_layout(self, lay):
        while lay.count():
            it = lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
            elif it.layout():
                self._clear_layout(it.layout())

    def _show_detail(self):
        d = self.detail
        t = theme.tokens()
        self._clear_detail()
        info = d.info
        top = QHBoxLayout()
        tt = QLabel(f"Solicitação nº {d.code}")
        tt.setStyleSheet("font-size: 17pt; font-weight: 800;")
        top.addWidget(tt)
        top.addStretch(1)
        tipo = info.get("Tipo de Solicitação", "")
        if tipo:
            top.addWidget(_chip(tipo, t["accent"]))
        seal = self._detail_seal(d)
        sc = _chip(f"{seal.icon} {seal.label}", {"ok": t["success_text"], "atencao": t["warn"],
                                                 "bloqueado": t["danger"]}[seal.status])
        sc.setToolTip("\n".join("• " + x for x in seal.reasons) or "Material e arquivos sem problemas conhecidos.")
        sc.setObjectName("SealChip")
        self.detail_seal = seal
        top.addWidget(sc)
        self.dbody.addLayout(top)

        # aluno em destaque
        who = QFrame()
        who.setObjectName("RequestCard")
        wl = QVBoxLayout(who)
        wl.setContentsMargins(14, 10, 14, 10)
        n = QLabel(info.get("Nome", "—"))
        n.setStyleSheet("font-size: 12pt; font-weight: 700;")
        n.setTextInteractionFlags(Qt.TextSelectableByMouse)
        r = QLabel(f"RM {info.get('RM', '—')}" + (f"  ·  Turma {info['Turma']}" if info.get("Turma") else ""))
        r.setTextInteractionFlags(Qt.TextSelectableByMouse)
        wl.addWidget(n)
        wl.addWidget(r)
        if info.get("Curso"):
            c = QLabel(info["Curso"])
            c.setObjectName("Muted")
            wl.addWidget(c)
        self.dbody.addWidget(who)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        row = 0
        for k in ("Projeto", "Professor", "Observações"):
            if info.get(k):
                kl = QLabel(k)
                kl.setObjectName("Muted")
                vl = QLabel(info[k])
                vl.setWordWrap(True)
                vl.setTextInteractionFlags(Qt.TextSelectableByMouse)
                grid.addWidget(kl, row, 0, Qt.AlignTop)
                grid.addWidget(vl, row, 1)
                row += 1
        grid.setColumnStretch(1, 1)
        self.dbody.addLayout(grid)

        ft = QLabel("Arquivos")
        ft.setObjectName("SectionHead")
        self.dbody.addWidget(ft)
        self.file_rows: dict[str, QLabel] = {}
        for f in d.files:
            fr = QFrame()
            fr.setObjectName("PartRow")
            fl = QHBoxLayout(fr)
            fl.setContentsMargins(10, 8, 10, 8)
            ic = QLabel()
            ic.setPixmap(pixmap("layers" if f.is_dxf else "warn", t["accent"] if f.is_dxf else t["warn"], 16))
            name = QLabel(f.name)
            name.setStyleSheet("font-weight: 600;")
            name.setWordWrap(True)
            fl.addWidget(ic)
            fl.addWidget(name, 1)
            if f.material:
                fl.addWidget(_chip(f.material, theme.material_color(f.material).name()))
            q = QLabel(f"× {f.quantity}")
            q.setStyleSheet("font-weight: 800;")
            q.setToolTip("Quantidade pedida")
            fl.addWidget(q)
            st = QLabel("" if f.is_dxf else "não é DXF")
            st.setObjectName("Muted")
            fl.addWidget(st)
            self.file_rows[self._fkey(d, f)] = st
            self.dbody.addWidget(fr)
        self._add_send_buttons(d.materials())
        self.dbody.addStretch(1)
        self.dstack.setCurrentIndex(1)

    @staticmethod
    def _known_seals() -> dict:
        """Selos já calculados pelo Sindri (geometria + material), por nº da solicitação."""
        from ..core.manufacturability import Seal, SealStore, default_seal_path
        out = {}
        for code, d in SealStore(default_seal_path()).load().items():
            if isinstance(d, dict) and d.get("status") in ("ok", "atencao", "bloqueado"):
                out[str(code)] = Seal(d["status"], [str(x) for x in d.get("reasons", [])])
        return out

    def _detail_seal(self, d: RequestDetail):
        """Selo da solicitação aberta: material e tipo de arquivo agora, mais a geometria se ela já passou
        pelo Sindri. Bloqueado (PVC, vinil…) aparece antes de juntar no lote."""
        from ..core.manufacturability import Seal, seal_for
        from ..core.material_safety import worst, SafetyResult
        mats = [f.material for f in d.files if f.is_dxf]
        now = seal_for(mats, non_dxf=sum(1 for f in d.files if not f.is_dxf))
        known = self._known_seals().get(str(d.code))
        if known is None:
            return now
        status = worst([SafetyResult(now.status), SafetyResult(known.status)]).status
        return Seal(status, list(dict.fromkeys(now.reasons + known.reasons)))

    @staticmethod
    def _fkey(d: RequestDetail, f) -> str:
        return f"{d.code}/{f.name}"

    def _add_send_buttons(self, mats: dict, batch: bool = False):
        act = QFrame()
        al = QVBoxLayout(act)
        al.setContentsMargins(0, 6, 0, 0)
        al.setSpacing(6)
        if not mats:
            w = QLabel("Nenhum arquivo DXF para corte a laser." if batch else
                       "Esta solicitação não tem arquivos DXF para corte a laser.")
            w.setObjectName("Muted")
            al.addWidget(w)
        else:
            from ..core.material_safety import check_material
            for m in mats:
                r = check_material(m)
                if r.status != "ok":
                    w = QLabel(("⛔ " if r.blocked else "⚠ ") + f"{m}: {r.reason}"
                               + (" O Sindri não exporta este material para o laser." if r.blocked else ""))
                    w.setWordWrap(True)
                    w.setObjectName("InlineWarning")
                    al.addWidget(w)
            total = sum(len(fs) for fs in mats.values())
            if len(mats) > 1:
                b = QPushButton(f"Enviar tudo para a placa  ·  {total} arquivo(s), placas separadas por material")
                b.setObjectName("primary")
                b.setIcon(icon("play", "#ffffff", 13))
                b.setMinimumHeight(38)
                b.clicked.connect(lambda _=False: self.send(ALL_MATERIALS))
                al.addWidget(b)
            for m, fs in mats.items():
                copies = sum(f.quantity for f in fs)
                b = QPushButton(f"Enviar {m} para a placa  ·  {len(fs)} arquivo(s), {copies} cópia(s)")
                b.setObjectName("primary" if len(mats) == 1 else "")
                if len(mats) == 1:
                    b.setIcon(icon("play", "#ffffff", 13))
                    b.setMinimumHeight(38)
                else:
                    col = theme.material_color(m).name()
                    b.setStyleSheet(f"border-left: 5px solid {col};")
                b.clicked.connect(lambda _=False, mm=m: self.send(mm))
                al.addWidget(b)
            hint = QLabel("Os arquivos são baixados agora, ao enviar." + (
                " As peças de todas as solicitações são encaixadas juntas (cada material em placas próprias) "
                "e o nome de cada peça começa com o nº da solicitação." if batch else ""))
            hint.setObjectName("Muted")
            hint.setWordWrap(True)
            al.addWidget(hint)
        self.send_buttons = [w for w in act.findChildren(QPushButton)]
        self.dbody.addWidget(act)

    # ------------------------------------------------------------------ enviar (baixar + importar)
    def send(self, material: str):
        items = self.batch if self.batch else ([self.detail] if self.detail is not None else [])
        if not items or self._sending:
            return
        plan = []
        for d in items:
            fs = [f for m, ffs in d.materials().items() for f in ffs if material == ALL_MATERIALS or m == material]
            for f in d.files:
                f.local_path = None
            if fs:
                plan.append((d, fs))
        if not plan:
            return
        self._sending = material
        self._plan = plan
        self._set_buttons(False)
        n = sum(len(fs) for _, fs in plan)
        self._set_state(f"Baixando {n} arquivo(s)…", "run")
        self._pending = {}
        self._claimed: set[str] = set()
        self._dl_ok = 0
        used: set[str] = set()
        try:
            for d, fs in plan:
                for f in fs:
                    path = target_path(self.base_folder, d, f)
                    stem, ext = os.path.splitext(path)
                    k = 2
                    while os.path.normcase(path) in used:        # mesmo nome duas vezes no pedido
                        path = f"{stem} ({k}){ext}"
                        k += 1
                    used.add(os.path.normcase(path))
                    if len(path) > 250:
                        raise OSError(f"caminho muito longo ({len(path)} caracteres): escolha uma pasta mais "
                                      "curta em “Pasta…”")
                    f.local_path = path
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    self._dl_seq = getattr(self, "_dl_seq", 0) + 1
                    key = f"sindri{self._dl_seq}_{uuid.uuid4().hex}"
                    self._pending[key] = (d, f)
                    lab = self.file_rows.get(self._fkey(d, f))
                    if lab:
                        lab.setText("baixando…")
                    self._log_dl(f"pedido {key}")
                    self.page.download(QUrl(f.url), key)
        except OSError as e:
            self._pending = {}
            self._cancel_downloads()
            self._sending = None
            self._set_buttons(True)
            for d, fs in plan:
                for f in fs:
                    f.local_path = None
            self._set_state("Não foi possível preparar a pasta dos arquivos", "warn")
            QMessageBox.warning(self, "Enviar para a placa", f"Não foi possível salvar os arquivos:\n{e}")
            return
        self._dl_timer.start(DOWNLOAD_TIMEOUT_S * 1000 + 3000 * max(0, n - 3))

    def _set_buttons(self, on: bool):
        for b in getattr(self, "send_buttons", []):
            try:
                b.setEnabled(on)
            except RuntimeError:          # botão já destruído (tela trocada)
                pass

    def _match(self, req):
        """Liga o download ao arquivo pedido: pelo nome sugerido ou, se não der, pela URL
        (na ordem em que foram pedidos, sem repetir um arquivo já ligado)."""
        name = req.downloadFileName()
        if name in self._pending and name not in self._claimed:
            return name
        url = req.url().toString()
        for k, (_, f) in self._pending.items():
            if k not in self._claimed and QUrl(f.url).toString() == url:
                return k
        for k, (_, f) in self._pending.items():
            if k not in self._claimed and os.path.basename(url.split("?")[0]) == os.path.basename(f.url.split("?")[0]):
                return k
        return None

    def _download_requested(self, req):
        key = self._match(req) if self._sending else None
        if self._sending:
            self._log_dl("download recebido: " + (key or "NÃO RECONHECIDO"))
        if key is None:
            return  # download feito pelo usuário na página: comportamento padrão
        self._claimed.add(key)
        _, f = self._pending[key]
        req.setDownloadDirectory(os.path.dirname(f.local_path))
        req._sindri_temp = os.path.join(os.path.dirname(f.local_path), f".{key}.part")
        req.setDownloadFileName(os.path.basename(req._sindri_temp))
        req.isFinishedChanged.connect(lambda r=req, k=key: self._download_finished(r, k))
        self._downloads.append(req)
        req.accept()

    def _download_finished(self, req, key):
        from PySide6.QtWebEngineCore import QWebEngineDownloadRequest
        item = self._pending.pop(key, None)
        if req in self._downloads:
            self._downloads.remove(req)
        if item is None:
            self._remove_partial(req)
            return
        d, f = item
        temp = getattr(req, "_sindri_temp", "")
        ok = req.state() == QWebEngineDownloadRequest.DownloadCompleted and os.path.isfile(temp)
        if ok:
            try:
                from ..core.fileutil import replace_file
                replace_file(temp, f.local_path)
            except OSError:
                ok = False
        self._remove_partial(req)
        self._log_dl(f"fim {key}: {'ok' if ok else 'ERRO estado=' + str(req.state())} "
                     f"{req.receivedBytes()} bytes")
        lab = self.file_rows.get(self._fkey(d, f))
        if lab:
            reason = req.interruptReasonString() if not ok else ""
            lab.setText("✓ baixado" if ok else "Erro no download ou ao gravar o arquivo")
            lab.setToolTip(reason or ("Confira a conexão, o acesso e a permissão na pasta de destino." if not ok else ""))
        if ok:
            self._dl_ok += 1
        else:
            f.local_path = None
        if not self._pending:
            self._dl_timer.stop()
            self._finish_send()

    def _download_timeout(self):
        if not self._pending:
            return
        for k, (d, f) in self._pending.items():
            self._log_dl(f"SEM RESPOSTA (tempo esgotado) {k}")
            f.local_path = None
            lab = self.file_rows.get(self._fkey(d, f))
            if lab:
                lab.setText("sem resposta")
        self._pending = {}
        self._cancel_downloads()
        self._finish_send()

    @staticmethod
    def _remove_partial(req):
        path = getattr(req, "_sindri_temp", "")
        if path and os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                pass

    def _cancel_downloads(self):
        pending, self._downloads = self._downloads, []
        for req in pending:
            if not req.isFinished():
                req.cancel()
            self._remove_partial(req)

    def _finish_send(self):
        material = self._sending
        self._sending = None
        self._set_buttons(True)
        if self._dl_ok == 0:
            self._set_state("Nenhum arquivo baixado. Confira o acesso na página, a conexão e a pasta de destino; depois tente enviar novamente.", "warn")
            self.btn_page.setChecked(True)
            return
        failed = [(d, f) for d, fs in self._plan for f in fs if not f.local_path]
        if failed:
            names = "\n".join(f"• {d.code} — {f.name}" for d, f in failed[:15])
            r = QMessageBox.question(
                self, "Alguns arquivos não baixaram",
                f"{len(failed)} arquivo(s) não foram baixados:\n\n{names}\n\n"
                "Continuar só com os que baixaram? (Não = ficar aqui e tentar enviar de novo)",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r != QMessageBox.Yes:
                self._set_state(f"{len(failed)} arquivo(s) não baixaram — tente enviar de novo", "warn")
                return
        self.failed_files = [f"{d.code} — {f.name}" for d, f in failed]
        self.chosen_material = material
        self.batch_result = [(d, [f for f in fs if f.local_path]) for d, fs in self._plan] if self.batch else None
        if self.batch:                       # lote enviado: desmarca para não mandar de novo sem querer
            for d, _ in self._plan:
                self._cache.pop(d.code, None)
            self._checked = []
            self._fill_list()
            self._update_batch_bar()
        self.accept()

    # ------------------------------------------------------------------ lote (várias solicitações)
    def set_open_codes(self, codes) -> None:
        """Solicitações já abertas no Sindri: aparecem marcadas (e travadas) na fila."""
        self.open_codes = {str(c) for c in codes or []}
        self._checked = [c for c in self._checked if c not in self.open_codes]
        self._fill_list()
        self._update_batch_bar()

    def _item_checked(self, item):
        if item.column() != 0:
            return
        code = item.text()
        if code in self.open_codes:
            return
        on = item.checkState() == Qt.Checked
        if on and code not in self._checked:
            self._checked.append(code)
        elif not on and code in self._checked:
            self._checked.remove(code)
        self._update_batch_bar()

    def _update_batch_bar(self):
        n = len(self._checked)
        self.batch_bar.setVisible(n > 0)
        self.batch_tip.setVisible(n == 0)
        extra = f" · {len(self.open_codes)} já no Sindri" if self.open_codes else ""
        self.batch_tip.setText(
            f"Em verde: {len(self.open_codes)} solicitação(ões) já abertas no Sindri. Marque outras para "
            "adicionar ao lote." if self.open_codes else
            "Marque a caixinha ao lado do nº para juntar várias solicitações nas mesmas placas.")
        self.batch_lbl.setText(f"{n} marcada(s){extra}")
        self.btn_batch.setEnabled(n >= 1 and not self._fetch.isActive())
        self.btn_batch.setText(f"Juntar {n} na placa" if n > 1 else "Juntar na placa")

    def clear_checks(self):
        self._checked = []
        self._fill_list()
        self._update_batch_bar()

    def view_batch(self):
        """Lê os detalhes de todas as marcadas (sem baixar nada) e mostra o lote."""
        if self._sending or self._fetch.isActive() or not self._checked:
            return
        self._poll.stop()
        self._req_seq += 1                         # cancela uma visualização em andamento
        self._fetch_queue = [int(c) for c in self._checked if c.isdigit()]
        self._fetch_done: list[RequestDetail] = []
        self._fetch_total = len(self._fetch_queue)
        self._fetch_next()

    def _fetch_next(self):
        while self._fetch_queue and self._fetch_queue[0] in self._cache:
            self._fetch_done.append(self._cache[self._fetch_queue.pop(0)])
        if not self._fetch_queue:
            self._fetch.stop()
            self.batch = list(self._fetch_done)
            self.detail = None
            self._update_batch_bar()
            self._show_batch()
            return
        code = self._fetch_queue[0]
        self._set_state(f"Lendo solicitação {code} ({len(self._fetch_done) + 1}/{self._fetch_total})…", "run")
        self._fetch_left = 60
        self.page.runJavaScript(JS_OPEN % code, 0, self._fetch_opened)

    def _fetch_opened(self, res):
        if res != "ok":
            left = ", ".join(str(c) for c in self._fetch_queue)
            self._fetch_queue = []
            self._set_state(f"Não consegui abrir {left} — faça login e abra Solicitações Maker", "warn")
            self._update_batch_bar()
            return
        self._fetch.start()
        self._update_batch_bar()

    def _fetch_poll(self):
        if not self._fetch_queue:
            self._fetch.stop()
            return
        self._fetch_left -= 1
        code = self._fetch_queue[0]
        if self._fetch_left < 0:
            self._fetch.stop()
            self._fetch_queue = []
            self._set_state(f"A solicitação {code} não abriu a tempo. Tente de novo.", "warn")
            self._update_batch_bar()
            return
        self.page.runJavaScript(JS_DETAIL % code, 0, lambda raw, c=code: self._fetch_got(raw, c))

    def _fetch_got(self, raw, code):
        if not raw or not self._fetch_queue or self._fetch_queue[0] != code:
            return
        try:
            d = parse_detail(json.loads(raw))
        except Exception:
            return
        self._fetch.stop()
        self._cache[d.code] = d
        self._fetch_queue.pop(0)
        self._fetch_done.append(d)
        self._fetch_next()

    def _show_batch(self):
        theme.tokens()
        self._clear_detail()
        self.file_rows = {}
        top = QHBoxLayout()
        n_new = len(self.batch)
        tt = QLabel(f"Lote · {n_new} solicitaç{'ão' if n_new == 1 else 'ões'}"
                    + (f"  <span style='font-size:11pt; font-weight:600;'>+ {len(self.open_codes)} já no Sindri"
                       "</span>" if self.open_codes else ""))
        tt.setStyleSheet("font-size: 17pt; font-weight: 800;")
        top.addWidget(tt)
        top.addStretch(1)
        self.dbody.addLayout(top)
        mats: dict[str, list] = {}
        for d in self.batch:
            card = QFrame()
            card.setObjectName("RequestCard")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(14, 10, 14, 10)
            cl.setSpacing(4)
            h = QHBoxLayout()
            n = QLabel(f"<b>{d.code}</b>  ·  {d.student or '—'}")
            n.setStyleSheet("font-size: 11pt;")
            n.setTextInteractionFlags(Qt.TextSelectableByMouse)
            h.addWidget(n, 1)
            r = QLabel(f"RM {d.rm or '—'}")
            r.setObjectName("Muted")
            h.addWidget(r)
            cl.addLayout(h)
            dm = d.materials()
            if not dm:
                w = QLabel("sem arquivos DXF")
                w.setObjectName("Muted")
                cl.addWidget(w)
            for m, fs in dm.items():
                mats.setdefault(m, []).extend(fs)
                for f in fs:
                    fr = QHBoxLayout()
                    nm = QLabel(f.name)
                    nm.setWordWrap(True)
                    fr.addWidget(nm, 1)
                    fr.addWidget(_chip(m, theme.material_color(m).name()))
                    q = QLabel(f"× {f.quantity}")
                    q.setStyleSheet("font-weight: 800;")
                    fr.addWidget(q)
                    st = QLabel("")
                    st.setObjectName("Muted")
                    fr.addWidget(st)
                    self.file_rows[self._fkey(d, f)] = st
                    cl.addLayout(fr)
            self.dbody.addWidget(card)
        self._add_send_buttons(mats, batch=True)
        self.dbody.addStretch(1)
        self.dstack.setCurrentIndex(1)
        self._set_state(f"Lote com {len(self.batch)} solicitações · pronto para enviar", "ok")

    # ------------------------------------------------------------------
    def _disconnect(self):
        self._poll.stop()
        self._fetch.stop()
        self._crawl.stop()
        self._dl_timer.stop()
        # fechar no meio de um envio/leitura = cancelar (a janela é reaproveitada depois)
        if self._sending and self.result() != QDialog.Accepted:
            for d, fs in getattr(self, "_plan", []):
                for f in fs:
                    f.local_path = None
        self._sending = None
        self._pending = {}
        self._cancel_downloads()
        self._fetch_queue = []
        self._set_buttons(True)
        if getattr(self, "_dl_connected", False):
            try:
                shared_profile().downloadRequested.disconnect(self._download_requested)
            except Exception:
                pass
            self._dl_connected = False

    def showEvent(self, e):
        super().showEvent(e)
        from .nowheel import protect
        protect(self)
        if not getattr(self, "_dl_connected", False):
            shared_profile().downloadRequested.connect(self._download_requested)
            self._dl_connected = True
        if self._logged:
            self.refresh_list()

    def done(self, r):
        self._disconnect()
        super().done(r)
