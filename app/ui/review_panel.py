"""Painel de Conferência da importação: todos os avisos do lote num lugar só, agrupados por
solicitação e arquivo, com ações em lote (fechar aberturas, marcar como conferido, recado ao aluno)."""
from __future__ import annotations

import os
from collections import defaultdict

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QPushButton, QSizePolicy, QToolButton, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout)

from ..core import import_issues as ii
from . import theme
from .icons import icon

ROLE = Qt.UserRole + 1
STATUS_TEXT = {ii.ERROR: "não abriu", ii.CHECK: "conferir", ii.FIXED: "resolvido", "ok": "ok"}


def _n(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def issue_text(it: ii.ImportIssue) -> str:
    """Texto curto da árvore (o aviso completo fica na dica). Contornos abertos ganham uma 2ª linha com o
    que dá para fazer: quantas aberturas e de que tamanho, e quantas pontas não têm par."""
    n = it.count
    if it.kind == "open":
        first = _n(n, "contorno aberto", "contornos abertos")
        det = []
        if it.gaps:
            det.append(_n(len(it.gaps), "abertura", "aberturas") + f" de até {ii.fmt_mm(max(g.dist for g in it.gaps))}")
        if it.loose_ends:
            det.append(_n(len(it.loose_ends), "ponta sem par", "pontas sem par"))
        return first + ("\n" + " · ".join(det) if det else "")
    short = {
        "duplicate_entities": _n(n, "linha repetida removida", "linhas repetidas removidas"),
        "duplicate_contours": _n(n, "contorno em dobro", "contornos em dobro") + ": corta uma vez",
        "invalid_contour": _n(n, "contorno que cruza a si mesmo", "contornos que cruzam a si mesmos"),
        "loose_mark": _n(n, "texto/marca fora das peças", "textos/marcas fora das peças"),
        "units_assumed": "Sem unidade: lido em milímetros",
        "units_unknown": "Unidade desconhecida: lido em milímetros",
        "units_suspicious": "A unidade parece errada",
        "empty": "Nada que o laser consiga cortar",
        "read_error": "Não abriu (corrompido ou não é DXF)",
        "repaired": _n(n, "abertura fechada aqui", "aberturas fechadas aqui"),
        "ignored": it.message,
        "text_ignored": _n(n, "texto ignorado", "textos ignorados"),
    }
    return short.get(it.kind, it.message)


HINTS = {
    "open": "Abertura pequena: feche aqui. Ponta sem par: pode faltar um trecho (peça ao aluno) ou ser "
            "uma linha proposital (Está certo).",
    "invalid_contour": "O contorno cruza a si mesmo; o encaixe usa o envoltório. Confira no CAD.",
    "loose_mark": "Vai como peça própria. Se não deve ser gravado, desmarque a camada em Parâmetros.",
    "units_suspicious": "O Sindri já ajustou a unidade; confira as medidas das peças.",
}


class ReviewPanel(QFrame):
    issueActivated = Signal(object)        # ImportIssue: mostrar no desenho
    fileActivated = Signal(str)            # arquivo: enquadrar no desenho
    closeGapsRequested = Signal(list)      # [(ImportIssue, [Gap])]
    ackChanged = Signal(list, bool)        # [chave], conferido?
    undoRepairsRequested = Signal(list)    # [arquivo]
    openFileRequested = Signal(str)
    folderRequested = Signal(str)
    noteRequested = Signal(list)           # [ImportIssue] para o recado
    closeRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ReviewPanel")
        self.setMinimumWidth(300)
        self.issues: list[ii.ImportIssue] = []
        self.acked: set[str] = set()
        self.repaired_files: set[str] = set()
        self.tags: dict[str, str] = {}
        self.group_names: dict[str, str] = {}
        self.files: list[str] = []
        self._cursor = -1

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(8)
        head = QHBoxLayout()
        title = QLabel("Conferência")
        title.setObjectName("PanelTitle")
        head.addWidget(title)
        head.addStretch(1)
        self.btn_hide = QToolButton()
        self.btn_hide.setAutoRaise(True)
        self.btn_hide.setText("✕")
        self.btn_hide.setToolTip("Fechar o painel (ele volta pelo botão Conferência)")
        self.btn_hide.setAccessibleName("Fechar painel de conferência")
        self.btn_hide.clicked.connect(self.closeRequested.emit)
        head.addWidget(self.btn_hide)
        lay.addLayout(head)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setObjectName("Muted")
        lay.addWidget(self.summary)

        # ---- em lote: fechar todas as aberturas pequenas
        batch = QFrame()
        batch.setObjectName("ReviewBatch")
        bl = QGridLayout(batch)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setHorizontalSpacing(6)
        cap = QLabel("Fechar todas as aberturas de até")
        cap.setObjectName("FieldLabel")
        bl.addWidget(cap, 0, 0, 1, 2)
        self.limit = QDoubleSpinBox()
        self.limit.setRange(0.05, ii.GAP_SEARCH_MM)
        self.limit.setDecimals(2)
        self.limit.setSingleStep(0.1)
        self.limit.setValue(0.5)
        self.limit.setSuffix(" mm")
        self.limit.setAccessibleName("Tamanho máximo da abertura a fechar")
        self.limit.setToolTip("Liga as pontas que estão até esta distância com um trecho reto, só naquela abertura.\n"
                              "Folgas pequenas costumam ser erro de desenho; acima de 1 mm confira antes.")
        self.limit.valueChanged.connect(lambda _: self._update_batch())
        bl.addWidget(self.limit, 1, 0)
        self.btn_close_all = QPushButton("Fechar")
        self.btn_close_all.setObjectName("primary")
        self.btn_close_all.clicked.connect(self._close_all)
        bl.addWidget(self.btn_close_all, 1, 1)
        bl.setColumnStretch(1, 1)
        lay.addWidget(batch)
        self.batch = batch

        self.show_fixed = QCheckBox("Mostrar também o que já foi resolvido")
        self.show_fixed.toggled.connect(lambda _: self._rebuild())
        lay.addWidget(self.show_fixed)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setIndentation(14)
        self.tree.setWordWrap(True)
        self.tree.setUniformRowHeights(False)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setIconSize(QSize(12, 12))
        self.tree.setAccessibleName("Avisos da importação por arquivo")
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        self.tree.itemActivated.connect(lambda it, _c: self._activate(it))
        self.tree.itemClicked.connect(lambda it, _c: self._activate(it))
        lay.addWidget(self.tree, 1)

        # ---- ações sobre a seleção
        acts = QGridLayout()
        acts.setHorizontalSpacing(6)
        acts.setVerticalSpacing(6)
        self.btn_close = QPushButton("Fechar aberturas")
        self.btn_close.setToolTip("Fecha todas as aberturas dos itens selecionados (até 5 mm)")
        self.btn_close.clicked.connect(self._close_selected)
        self.btn_ack = QPushButton("Está certo")
        self.btn_ack.setToolTip("O desenho está assim de propósito (ex.: linha de vinco ou corte aberto).\n"
                                "O aviso sai da lista de pendências e não é cobrado do aluno.")
        self.btn_ack.clicked.connect(self._ack_selected)
        self.btn_undo = QPushButton("Desfazer correções")
        self.btn_undo.setToolTip("Remove os trechos acrescentados neste arquivo")
        self.btn_undo.clicked.connect(self._undo_selected)
        self.btn_open = QPushButton("Abrir no CAD")
        self.btn_open.setToolTip("Abre o arquivo original no programa padrão para DXF")
        self.btn_open.clicked.connect(lambda: self._with_file(self.openFileRequested.emit))
        self.btn_folder = QPushButton("Pasta")
        self.btn_folder.setToolTip("Mostra a pasta do arquivo")
        self.btn_folder.clicked.connect(lambda: self._with_file(self.folderRequested.emit))
        for b in (self.btn_close, self.btn_ack, self.btn_undo, self.btn_open, self.btn_folder):
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        acts.addWidget(self.btn_close, 0, 0)
        acts.addWidget(self.btn_ack, 0, 1)
        acts.addWidget(self.btn_undo, 1, 0, 1, 2)
        acts.addWidget(self.btn_open, 2, 0)
        acts.addWidget(self.btn_folder, 2, 1)
        lay.addLayout(acts)
        foot = QHBoxLayout()
        self.btn_note = QPushButton("Copiar recado para o aluno")
        self.btn_note.setObjectName("ghost")
        self.btn_note.setToolTip("Copia um texto explicando o que corrigir (dos itens selecionados ou de tudo)")
        self.btn_note.clicked.connect(self._note)
        self.btn_next = QPushButton("Próximo  ▸")
        self.btn_next.setToolTip("Vai para o próximo aviso a conferir (F8)")
        self.btn_next.clicked.connect(self.next_issue)
        foot.addWidget(self.btn_note, 1)
        foot.addWidget(self.btn_next)
        lay.addLayout(foot)
        self._selection_changed()

    # ------------------------------------------------------------------ dados
    def set_data(self, issues: list, files: list[str], tags: dict | None = None,
                 group_names: dict | None = None, acked: set | None = None, repaired_files: set | None = None):
        self.issues = list(issues or [])
        self.files = [os.path.abspath(f) for f in files or []]
        self.tags = {os.path.abspath(k): v for k, v in (tags or {}).items()}
        self.group_names = dict(group_names or {})
        self.acked = set(acked or ())
        self.repaired_files = {os.path.abspath(f) for f in repaired_files or ()}
        self._cursor = -1
        self._rebuild()

    def pending(self) -> list[ii.ImportIssue]:
        """Avisos que ainda pedem atenção, na ordem da lista."""
        return [it for it in self._ordered() if it.severity != ii.FIXED and it.key not in self.acked]

    def file_status(self, path: str) -> str:
        sev = [it.severity for it in self.issues if os.path.abspath(it.file) == os.path.abspath(path)
               and (it.severity == ii.FIXED or it.key not in self.acked)]
        if not sev:
            return "ok"
        return min(sev, key=lambda s: ii.SEVERITY_ORDER[s])

    def _ordered(self) -> list[ii.ImportIssue]:
        order = {f: i for i, f in enumerate(self.files)}
        return sorted(self.issues, key=lambda it: (self.tags.get(os.path.abspath(it.file), ""),
                                                   order.get(os.path.abspath(it.file), 1 << 30),
                                                   ii.SEVERITY_ORDER[it.severity]))

    # ------------------------------------------------------------------ árvore
    def _dot(self, status: str):
        name = {ii.ERROR: "danger", ii.CHECK: "warn"}.get(status, "success")
        return icon("dot", theme.tokens()[name], 12)

    def _rebuild(self):
        sel = {self._item_key(it) for it in self.tree.selectedItems()}
        self.tree.blockSignals(True)
        self.tree.clear()
        show_fixed = self.show_fixed.isChecked()
        by_file: dict[str, list] = defaultdict(list)
        for it in self._ordered():
            if it.severity == ii.FIXED and not show_fixed:
                continue
            by_file[os.path.abspath(it.file)].append(it)
        files = [f for f in self.files if f in by_file or show_fixed]
        files += [f for f in by_file if f not in files]           # arquivo que nem abriu
        multi = len({self.tags.get(f, "") for f in files}) > 1
        groups: dict[str, QTreeWidgetItem] = {}
        for f in files:
            parent = self.tree.invisibleRootItem()
            tag = self.tags.get(f, "")
            if multi:
                g = groups.get(tag)
                if g is None:
                    g = QTreeWidgetItem([self.group_names.get(tag) or tag or "Sem solicitação"])
                    g.setData(0, ROLE, ("group", tag))
                    font = g.font(0)
                    font.setBold(True)
                    g.setFont(0, font)
                    self.tree.addTopLevelItem(g)
                    groups[tag] = g
                parent = g
            status = self.file_status(f)
            n_pend = sum(1 for it in by_file.get(f, []) if it.severity != ii.FIXED and it.key not in self.acked)
            label = os.path.basename(f) + (f"  ({n_pend})" if n_pend else "")
            fi = QTreeWidgetItem([label])
            fi.setIcon(0, self._dot(status))
            fi.setToolTip(0, f"{f}\nSituação: {STATUS_TEXT.get(status, status)}"
                          + ("\nTem correções feitas na conferência" if f in self.repaired_files else ""))
            fi.setData(0, ROLE, ("file", f))
            parent.addChild(fi)
            for it in by_file.get(f, []):
                done = it.key in self.acked
                txt = issue_text(it) + ("  ✓ conferido" if done else "")
                ci = QTreeWidgetItem([txt])
                ci.setToolTip(0, it.message + (f"\n\n{HINTS[it.kind]}" if it.kind in HINTS else ""))
                ci.setData(0, ROLE, ("issue", it))
                ci.setIcon(0, self._dot(ii.FIXED if done else it.severity))
                if done or it.severity == ii.FIXED:
                    ci.setForeground(0, theme.qcolor("muted"))
                fi.addChild(ci)
            fi.setExpanded(n_pend > 0 or show_fixed)
        for g in groups.values():
            g.setExpanded(True)
        # mantém a seleção
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            if self._item_key(item) in sel:
                item.setSelected(True)
            stack.extend(item.child(i) for i in range(item.childCount()))
        self.tree.blockSignals(False)
        self._update_summary()
        self._update_batch()
        self._selection_changed()

    @staticmethod
    def _item_key(item):
        kind, ref = item.data(0, ROLE)
        return (kind, ref.key if kind == "issue" else ref)

    def _update_summary(self):
        pend = self.pending()
        files = {os.path.abspath(it.file) for it in pend}
        fixed = sum(1 for it in self.issues if it.severity == ii.FIXED)
        acked = sum(1 for it in self.issues if it.severity != ii.FIXED and it.key in self.acked)
        if not self.issues:
            txt = "Nenhum aviso: todos os arquivos abriram sem problemas."
        elif not pend:
            txt = "✓ Nada pendente."
        else:
            txt = f"<b>{len(pend)} para conferir</b> em {len(files)} arquivo(s)."
        extra = []
        if fixed:
            extra.append(f"{fixed} resolvido(s) sozinho(s)")
        if acked:
            extra.append(f"{acked} marcado(s) como certo(s)")
        if extra:
            txt += " " + " · ".join(extra) + "."
        self.summary.setText(txt)

    def _all_gaps(self, items, limit=None):
        out = []
        for it in items:
            if it.kind != "open" or it.key in self.acked:
                continue
            gaps = it.gaps if limit is None else it.fixable_gaps(limit)
            if gaps:
                out.append((it, list(gaps)))
        return out

    def _update_batch(self):
        lim = self.limit.value()
        n = sum(len(g) for _, g in self._all_gaps(self.issues, lim))
        self.btn_close_all.setText(f"Fechar {_n(n, 'abertura', 'aberturas')}" if n else "Nenhuma até esse tamanho")
        self.btn_close_all.setEnabled(n > 0)
        self.btn_close_all.setToolTip(f"Fecha {n} abertura(s) de até {ii.fmt_mm(lim)} em todos os arquivos")
        self.batch.setVisible(any(it.kind == "open" and it.gaps for it in self.issues))

    # ------------------------------------------------------------------ seleção
    def _selected(self):
        """(avisos, arquivos) cobertos pela seleção (grupo e arquivo incluem os filhos)."""
        issues, files = [], []
        for item in self.tree.selectedItems():
            kind, ref = item.data(0, ROLE)
            if kind == "issue":
                issues.append(ref)
                files.append(os.path.abspath(ref.file))
            elif kind == "file":
                files.append(ref)
                issues += [it for it in self.issues if os.path.abspath(it.file) == ref]
            else:
                fs = [f for f in self.files if self.tags.get(f, "") == ref]
                files += fs
                issues += [it for it in self.issues if os.path.abspath(it.file) in fs]
        seen, uniq = set(), []
        for it in issues:
            if id(it) not in seen:
                seen.add(id(it))
                uniq.append(it)
        return uniq, list(dict.fromkeys(files))

    def _selection_changed(self):
        issues, files = self._selected()
        n = sum(len(g) for _, g in self._all_gaps(issues))
        self.btn_close.setEnabled(n > 0)
        self.btn_close.setText(f"Fechar {_n(n, 'abertura', 'aberturas')}" if n else "Fechar aberturas")
        checkable = [it for it in issues if it.severity == ii.CHECK]
        self.btn_ack.setEnabled(bool(checkable))
        all_acked = bool(checkable) and all(it.key in self.acked for it in checkable)
        self.btn_ack.setText("Voltar a conferir" if all_acked else "Está certo")
        self.btn_undo.setEnabled(any(f in self.repaired_files for f in files))
        one = len(files) == 1 and os.path.isfile(files[0])
        self.btn_open.setEnabled(one)
        self.btn_folder.setEnabled(one)
        self.btn_next.setEnabled(bool(self.pending()))

    def _activate(self, item):
        if item is None:
            return
        kind, ref = item.data(0, ROLE)
        if kind == "issue":
            pend = self.pending()
            if ref in pend:
                self._cursor = pend.index(ref)
            self.issueActivated.emit(ref)
        elif kind == "file":
            self.fileActivated.emit(ref)

    def select_file(self, path: str):
        """Clique no nome do arquivo no desenho: seleciona e mostra os avisos dele (inclusive os já
        resolvidos, se forem os únicos)."""
        path = os.path.abspath(path)
        if self._find_file(path) is None and any(os.path.abspath(i.file) == path for i in self.issues):
            self.show_fixed.setChecked(True)
        item = self._find_file(path)
        self.tree.clearSelection()
        if item is None:
            return False
        item.setSelected(True)
        item.setExpanded(True)
        self.tree.scrollToItem(item)
        return True

    def _find_file(self, path: str):
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            kind, ref = item.data(0, ROLE)
            if kind == "file" and ref == path:
                return item
            stack.extend(item.child(i) for i in range(item.childCount()))
        return None

    def next_issue(self, step: int = 1):
        pend = self.pending()
        if not pend:
            return None
        self._cursor = (self._cursor + step) % len(pend)
        target = pend[self._cursor]
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            kind, ref = item.data(0, ROLE)
            if kind == "issue" and ref is target:
                self.tree.clearSelection()
                item.setSelected(True)
                self.tree.scrollToItem(item)
                break
            stack.extend(item.child(i) for i in range(item.childCount()))
        self.issueActivated.emit(target)
        return target

    # ------------------------------------------------------------------ ações
    def _close_all(self):
        work = self._all_gaps(self.issues, self.limit.value())
        if work:
            self.closeGapsRequested.emit(work)

    def _close_selected(self):
        work = self._all_gaps(self._selected()[0])
        if work:
            self.closeGapsRequested.emit(work)

    def _ack_selected(self):
        checkable = [it for it in self._selected()[0] if it.severity == ii.CHECK]
        if not checkable:
            return
        on = not all(it.key in self.acked for it in checkable)
        self.ackChanged.emit([it.key for it in checkable], on)

    def _undo_selected(self):
        files = [f for f in self._selected()[1] if f in self.repaired_files]
        if files:
            self.undoRepairsRequested.emit(files)

    def _with_file(self, fn):
        files = self._selected()[1]
        if len(files) == 1:
            fn(files[0])

    def _note(self):
        issues = self._selected()[0] or self.issues
        self.noteRequested.emit([it for it in issues if it.key not in self.acked])
