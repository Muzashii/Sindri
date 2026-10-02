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
from typing import Optional

from PySide6.QtCore import QStandardPaths, Qt, QTimer, QUrl
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QDialog, QFileDialog,
                               QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QScrollArea, QSplitter, QStackedWidget,
                               QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget)

from ..core.intranet import (ALL_STATUS, CRAWL_BIG_PAGES, CRAWL_MAX_ROWS, JS_CRAWL, JS_CRAWL_RESET,
                             JS_CRAWL_STATE, merge_rows, status_options, INTRANET_URL, JS_DETAIL, JS_LIST, JS_OPEN, RequestDetail,
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
        base = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation) or os.path.expanduser("~/.sindri")
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
        self.search.setPlaceholderText("Buscar por nome, RM ou nº…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._fill_list)
        self.type_filter = QComboBox()
        self.type_filter.addItems(["Corte Laser", "Todos os tipos", "Impressão 3D"])
        self.type_filter.setToolTip("Mostrar só um tipo de solicitação")
        self.type_filter.currentIndexChanged.connect(self._fill_list)
        self.status_filter = QComboBox()
        self.status_filter.addItem("Aguardando")
        self.status_filter.setToolTip("Status da solicitação (abas do site: Aguardando, Em execução…)")
        self.status_filter.setMinimumWidth(150)
        self.status_filter.currentIndexChanged.connect(self._fill_list)
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
        self.list.itemSelectionChanged.connect(self._row_selected)
        ll.addWidget(self.list, 1)
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
        foot.addWidget(self.folder_lbl, 1)
        foot.addWidget(bfold)
        root.addLayout(foot)
        self._update_folder_label()

        self._poll = QTimer(self)
        self._poll.setInterval(300)
        self._poll.timeout.connect(self._poll_detail)
        self._crawl = QTimer(self)
        self._crawl.setInterval(400)
        self._crawl.timeout.connect(self._poll_crawl)
        self._base_rows: list[dict] = []
        self._crawl_rows: list[dict] = []
        self._dl_timer = QTimer(self)
        self._dl_timer.setSingleShot(True)
        self._dl_timer.timeout.connect(self._download_timeout)
        self.view.setUrl(QUrl(start_url))

    # ------------------------------------------------------------------ estado / página
    def _set_state(self, text: str, kind: str = ""):
        self.state.setText(text)
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
        self._crawl_rows = []
        if self._logged:
            self.view.reload()
        else:
            self.view.setUrl(QUrl(self.start_url))

    def _loaded(self, ok: bool):
        if not ok:
            self._set_state("Não foi possível carregar a intranet (sem internet?)", "warn")
            return
        # pequena espera: algumas páginas terminam de montar a tabela depois do load
        QTimer.singleShot(400, self.refresh_list)

    def refresh_list(self):
        self.page.runJavaScript(JS_LIST, 0, self._got_list)

    def _got_list(self, raw):
        try:
            data = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            data = {}
        if not data.get("temFuncao"):
            # logado mas na página inicial da intranet: vai sozinho para Solicitações Maker
            if should_go_to_requests(data.get("url", ""), bool(data.get("temSenha")), self.start_url) \
                    and self._auto_redirects < 3:
                self._auto_redirects += 1
                self._set_state("Login feito · abrindo Solicitações Maker…", "run")
                self.view.setUrl(QUrl(self.start_url))
                return
            self._logged = False
            self._set_state("Faça login na intranet ao lado", "warn")
            self.btn_page.setChecked(True)
            self._rows = []
            self._fill_list()
            return
        self._logged = True
        self._auto_redirects = 0
        self._base_rows = list(data.get("solicitacoes", []))
        self._merge_and_show()
        if not self._crawl.isActive():
            # as outras páginas de cada aba (o site mostra 10 por vez)
            self.page.runJavaScript(JS_CRAWL_RESET)
            self.page.runJavaScript(JS_CRAWL % (CRAWL_MAX_ROWS, CRAWL_BIG_PAGES))
            self._crawl.start()

    def _merge_and_show(self, reading: str = ""):
        rows = merge_rows(self._base_rows + self._crawl_rows)
        changed = [r["codigo"] for r in rows] != [r["codigo"] for r in self._rows]
        self._rows = rows
        if changed or not reading:
            self._update_status_options()
            self._fill_list()
        if reading:
            self._set_state(f"Conectado · {len(rows)} solicitações · lendo páginas ({reading})…", "run")
        else:
            self._set_state(f"Conectado · {len(rows)} solicitações", "ok")

    def _poll_crawl(self):
        self.page.runJavaScript(JS_CRAWL_STATE, 0, self._got_crawl)

    def _got_crawl(self, raw):
        try:
            data = json.loads(raw) if raw else None
        except (TypeError, ValueError):
            data = None
        if not data:
            self._crawl.stop()
            return
        self._crawl_rows = list(data.get("rows") or [])
        if data.get("done"):
            self._crawl.stop()
            self._merge_and_show()
        else:
            self._merge_and_show(data.get("progress") or "…")

    def _update_status_options(self):
        """Recria a lista de status com as abas encontradas, mantendo a escolha atual."""
        cur = self.status_filter.currentText() or "Aguardando"
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
        for r in self._rows:
            if tf != "Todos os tipos" and r.get("tipo", "") != tf:
                continue
            if sf != ALL_STATUS and r.get("situacao", "") != sf and not q:
                continue          # a busca procura em todos os status
            if q and q not in f"{r['codigo']} {r.get('nome', '')} {r.get('rm', '')}".lower():
                continue
            i = self.list.rowCount()
            self.list.insertRow(i)
            vals = [r["codigo"], r.get("nome", ""), r.get("rm", ""), r.get("data", "").split(" ")[0]]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setToolTip(f"{r.get('tipo', '')} · {r.get('situacao', '')} · {r.get('data', '')}")
                if c == 0:
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                self.list.setItem(i, c, it)
            if r["codigo"] == cur:
                self.list.selectRow(i)
            shown += 1
        self.list.blockSignals(False)
        self.list.verticalScrollBar().setValue(scroll)
        self.count_lbl.setText(f"{shown} de {len(self._rows)} solicitação(ões)" if self._rows else "")

    def _row_selected(self):
        r = self.list.currentRow()
        if r >= 0 and self.list.item(r, 0):
            self.code.setText(self.list.item(r, 0).text())
            self.view_request()

    # ------------------------------------------------------------------ visualizar
    def view_request(self):
        txt = self.code.text().strip()
        if not txt.isdigit():
            return
        if self._sending:
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
            self.file_rows[f.name] = st
            self.dbody.addWidget(fr)

        mats = d.materials()
        act = QFrame()
        al = QVBoxLayout(act)
        al.setContentsMargins(0, 6, 0, 0)
        al.setSpacing(6)
        if not mats:
            w = QLabel("Esta solicitação não tem arquivos DXF para corte a laser.")
            w.setObjectName("Muted")
            al.addWidget(w)
        else:
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
            hint = QLabel("Os arquivos são baixados agora, ao enviar.")
            hint.setObjectName("Muted")
            al.addWidget(hint)
        self.send_buttons = [w for w in act.findChildren(QPushButton)]
        self.dbody.addWidget(act)
        self.dbody.addStretch(1)
        self.dstack.setCurrentIndex(1)

    # ------------------------------------------------------------------ enviar (baixar + importar)
    def send(self, material: str):
        d = self.detail
        if d is None or self._sending:
            return
        mats = d.materials()
        files = [f for m, fs in mats.items() for f in fs if material == ALL_MATERIALS or m == material]
        if not files:
            return
        self._sending = material
        for b in getattr(self, "send_buttons", []):
            b.setEnabled(False)
        self._set_state(f"Baixando {len(files)} arquivo(s)…", "run")
        self._pending = {}
        self._dl_ok = 0
        for f in files:
            f.local_path = target_path(self.base_folder, d, f)
            os.makedirs(os.path.dirname(f.local_path), exist_ok=True)
            if os.path.exists(f.local_path):
                try:
                    os.remove(f.local_path)
                except OSError:
                    pass
            fname = os.path.basename(f.local_path)
            self._pending[fname] = f
            if f.name in self.file_rows:
                self.file_rows[f.name].setText("baixando…")
            self.page.download(QUrl(f.url), fname)
        self._dl_timer.start(DOWNLOAD_TIMEOUT_S * 1000)

    def _match(self, req):
        """Liga o download ao arquivo pedido: pelo nome sugerido ou, se não der, pela URL."""
        name = req.downloadFileName()
        if name in self._pending:
            return name
        url = req.url().toString()
        for k, f in self._pending.items():
            if QUrl(f.url).toString() == url or os.path.basename(url) == os.path.basename(f.url):
                return k
        return None

    def _download_requested(self, req):
        key = self._match(req) if self._sending else None
        if key is None:
            return  # download feito pelo usuário na página: comportamento padrão
        f = self._pending[key]
        req.setDownloadDirectory(os.path.dirname(f.local_path))
        req.setDownloadFileName(os.path.basename(f.local_path))
        req.isFinishedChanged.connect(lambda r=req, k=key: self._download_finished(r, k))
        self._downloads.append(req)
        req.accept()

    def _download_finished(self, req, key):
        from PySide6.QtWebEngineCore import QWebEngineDownloadRequest
        f = self._pending.pop(key, None)
        if f is None:
            return
        ok = req.state() == QWebEngineDownloadRequest.DownloadCompleted and os.path.isfile(f.local_path)
        if f.name in self.file_rows:
            self.file_rows[f.name].setText("✓ baixado" if ok else "erro")
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
        for f in self._pending.values():
            f.local_path = None
            if f.name in self.file_rows:
                self.file_rows[f.name].setText("sem resposta")
        self._pending = {}
        self._finish_send()

    def _finish_send(self):
        material = self._sending
        self._sending = None
        for b in getattr(self, "send_buttons", []):
            b.setEnabled(True)
        if self._dl_ok == 0:
            self._set_state("Nenhum arquivo baixado — a sessão pode ter expirado. Faça login de novo.", "warn")
            self.btn_page.setChecked(True)
            return
        self.chosen_material = material
        self.accept()

    # ------------------------------------------------------------------
    def _disconnect(self):
        self._poll.stop()
        self._crawl.stop()
        self._dl_timer.stop()
        if getattr(self, "_dl_connected", False):
            try:
                shared_profile().downloadRequested.disconnect(self._download_requested)
            except Exception:
                pass
            self._dl_connected = False

    def showEvent(self, e):
        super().showEvent(e)
        if not getattr(self, "_dl_connected", False):
            shared_profile().downloadRequested.connect(self._download_requested)
            self._dl_connected = True
        if self._logged:
            self.refresh_list()

    def done(self, r):
        self._disconnect()
        super().done(r)
