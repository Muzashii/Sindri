"""Conferência da importação: painel de avisos, desenho de cada arquivo e correções em lote."""
from __future__ import annotations

import os

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication

from ...core import import_issues as ii
from ..review_panel import ReviewPanel


class ReviewMixin:
    def _build_review_panel(self) -> ReviewPanel:
        self.file_repairs: dict[str, list] = {}    # arquivo -> trechos que fecham aberturas
        self.issue_acks: set[str] = set()          # avisos que o técnico marcou como "está certo"
        self._review_open = True
        p = ReviewPanel()
        p.issueActivated.connect(self.focus_issue)
        p.fileActivated.connect(self.focus_file)
        p.closeGapsRequested.connect(self.close_gaps)
        p.ackChanged.connect(self.set_issue_acks)
        p.undoRepairsRequested.connect(self.undo_repairs)
        p.openFileRequested.connect(lambda f: QDesktopServices.openUrl(QUrl.fromLocalFile(f)))
        p.folderRequested.connect(lambda f: QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(f))))
        p.noteRequested.connect(self.copy_student_note)
        p.closeRequested.connect(lambda: self.set_review_open(False))
        p.hide()
        self.review_panel = p
        return p

    # ------------------------------------------------------------------ estado
    def _issues(self) -> list:
        return list(getattr(self.report, "issues", []) or []) if self.report else []

    def _group_names(self) -> dict:
        info = self.request_info or {}
        names = {}
        reqs = info.get("requests", []) if info.get("batch") else ([info] if info else [])
        for r in reqs:
            code = str(r.get("code", ""))
            nome = r.get("nome") or r.get("student") or ""
            names[code] = f"{code} · {nome}" if nome else code
        return names

    def review_labels(self) -> dict:
        """Rótulo de cada arquivo no desenho: nome, solicitação/material e situação."""
        names = self._group_names()
        out = {}
        frames = (self.report.extra or {}).get("frames", {}) if self.report else {}
        for f in frames:
            tag = self.file_tags.get(f, "")
            sub = " · ".join(x for x in (names.get(tag) or tag, self.file_materials.get(f, "")) if x)
            name = os.path.basename(f)
            if name.lower().endswith(".dxf"):
                name = name[:-4]                      # todos são DXF: sobra espaço para o nome
            out[f] = (name, sub, self.review_panel.file_status(f))
        return out

    def refresh_review(self, auto_open: bool = False):
        """Atualiza painel, botão e desenho depois de importar, corrigir ou marcar como certo."""
        issues = self._issues()
        self.review_panel.set_data(issues, self.files, self.file_tags, self._group_names(), self.issue_acks,
                                   set(self.file_repairs))
        pend = self.review_panel.pending()
        if auto_open and pend:
            self._review_open = True
        btn = getattr(self, "btn_review_panel", None)
        if btn is not None:
            btn.setVisible(bool(issues))
            btn.setText(f"Conferência ({len(pend)})" if pend else "Conferência")
            btn.blockSignals(True)
            btn.setChecked(self._review_open)
            btn.blockSignals(False)
        self._sync_review_visibility()
        if self.tabs.currentIndex() == 0:
            self.show_original()

    def _sync_review_visibility(self):
        show = bool(self.report) and bool(self._issues()) and self._review_open and self.tabs.currentIndex() == 0
        self.review_panel.setVisible(show)

    def set_review_open(self, on: bool):
        self._review_open = bool(on)
        if on and self.tabs.currentIndex() != 0:
            self.tabs.setCurrentIndex(0)
        btn = getattr(self, "btn_review_panel", None)
        if btn is not None and btn.isChecked() != self._review_open:
            btn.blockSignals(True)
            btn.setChecked(self._review_open)
            btn.blockSignals(False)
        self._sync_review_visibility()

    def show_original(self):
        """Aba "Arquivo original": cada arquivo num quadro com nome, aberturas e pontas soltas."""
        if not self.report:
            self.canvas.show_empty_hint()
            return
        frames = (self.report.extra or {}).get("frames") or {}
        self.canvas.show_preview(self.report.preview, frames, self.review_labels(), self._issues(),
                                 self.issue_acks)

    # ------------------------------------------------------------------ navegação
    def focus_issue(self, issue):
        from ..review_overlay import highlight, issue_rect
        if self.tabs.currentIndex() != 0:
            self.tabs.setCurrentIndex(0)
        frames = self.canvas.preview_frames or {}
        rect = issue_rect(issue, frames)
        if rect is not None:
            self.canvas.focus_rect(rect)
        highlight(self.canvas, issue, frames)
        self.statusBar().showMessage(f"{os.path.basename(issue.file)}: {issue.message}", 8000)

    def focus_file(self, path: str):
        from PySide6.QtCore import QPointF, QRectF
        if self.tabs.currentIndex() != 0:
            self.tabs.setCurrentIndex(0)
        f = (self.canvas.preview_frames or {}).get(os.path.abspath(path))
        if f:
            self.canvas.focus_rect(QRectF(QPointF(f[2], f[3]), QPointF(f[4], f[5])).normalized(), 20.0)

    def on_preview_file_clicked(self, path: str):
        if self._issues():
            self.set_review_open(True)
            self.review_panel.select_file(path)
        self.focus_file(path)

    def next_review_issue(self):
        if not self._issues():
            return
        self.set_review_open(True)
        if self.review_panel.next_issue() is None:
            self.statusBar().showMessage("Nada pendente na conferência.", 5000)

    # ------------------------------------------------------------------ correções
    def close_gaps(self, work: list):
        """Liga as pontas de cada abertura com um trecho reto (na cor/camada do contorno) e reimporta."""
        factors = (self.report.extra or {}).get("unit_factors", {}) if self.report else {}
        before = {k: list(v) for k, v in self.file_repairs.items()}
        n, files = 0, set()
        for issue, gaps in work:
            f = os.path.abspath(issue.file)
            factor = float(factors.get(f, 1.0) or 1.0)
            have = {tuple(round(v, 6) for v in r["a"] + r["b"]) for r in self.file_repairs.get(f, [])}
            for g in gaps:
                rep = ii.gap_to_repair(g, factor)
                k = tuple(round(v, 6) for v in rep["a"] + rep["b"])
                if k in have:
                    continue
                have.add(k)
                self.file_repairs.setdefault(f, []).append(rep)
                n += 1
                files.add(f)
        if not n:
            return
        if self._reimport_after_review() is False:
            self.file_repairs = before                # leitura falhou: nada muda
            return
        self.statusBar().showMessage(f"{n} abertura(s) fechada(s) em {len(files)} arquivo(s). "
                                     "Os trechos novos aparecem em verde.", 10000)

    def undo_repairs(self, files: list):
        before = {k: list(v) for k, v in self.file_repairs.items()}
        n = 0
        for f in files:
            n += len(self.file_repairs.pop(os.path.abspath(f), []))
        if n:
            if self._reimport_after_review() is False:
                self.file_repairs = before
                return
            self.statusBar().showMessage(f"{n} correção(ões) desfeita(s).", 6000)

    def _reimport_after_review(self):
        view = self.canvas.transform(), self.canvas.horizontalScrollBar().value(), \
            self.canvas.verticalScrollBar().value()
        ok = self._import(self.files, keep_quantities=True, keep_layout=True, review=True)
        if ok is False:
            return False
        # continua olhando o mesmo trecho do desenho (a grade não muda de lugar ao fechar aberturas)
        self.canvas.setTransform(view[0])
        self.canvas.horizontalScrollBar().setValue(view[1])
        self.canvas.verticalScrollBar().setValue(view[2])
        self.canvas._auto_fit = False
        return True

    def set_issue_acks(self, keys: list, on: bool):
        if on:
            self.issue_acks.update(keys)
        else:
            self.issue_acks.difference_update(keys)
        self.mark_changed()
        self.schedule_autosave()
        view = self.canvas.transform(), self.canvas.horizontalScrollBar().value(), \
            self.canvas.verticalScrollBar().value()
        self.refresh_review()
        self.canvas.setTransform(view[0])
        self.canvas.horizontalScrollBar().setValue(view[1])
        self.canvas.verticalScrollBar().setValue(view[2])
        self.canvas._auto_fit = False

    def copy_student_note(self, issues: list):
        text = ii.student_note(issues)
        if not text:
            self.statusBar().showMessage("Nada para cobrar do aluno: os avisos já foram resolvidos ou conferidos.", 6000)
            return
        QApplication.clipboard().setText(text)
        self.statusBar().showMessage("Recado copiado. Cole no e-mail ou no chat da solicitação.", 8000)
