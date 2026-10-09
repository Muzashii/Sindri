"""Projetos .sindri: salvar, abrir, salvamento automático e recuperação."""
from __future__ import annotations

import json
import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from ...core.models import NestResult
from ...core.project import ProjectError, load_project, save_project
from ..dialogs import settings
from ..render import clear_graphics_cache
from ..tasks import run_task
from .common import APP_NAME, now_txt


class ProjectMixin:
    def mark_changed(self):
        self.dirty = True
        self.project_state.setText("Alterações não salvas")
        self.schedule_autosave()

    def reconcile_state(self):
        """Sincroniza cópias, pendências e checklist após alterar quantidades."""
        seen = set()
        kept = []
        for pl in self.placements:
            key = (pl.part_id, pl.instance)
            part = self.pmap.get(pl.part_id)
            if part is not None and 0 <= pl.instance < part.quantity and key not in seen:
                seen.add(key)
                kept.append(pl)
        self.placements = kept
        self.unplaced = [(p.id, i) for p in self.parts for i in range(p.quantity) if (p.id, i) not in seen]
        self.done_parts.difference_update(pid for pid, _ in self.unplaced)
        self.done_parts.intersection_update(self.pmap)
        self._compact_sheets()

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
        except (OSError, ValueError) as e:
            QMessageBox.critical(self, "Erro ao salvar", f"Não foi possível salvar o projeto:\n{e}")
            return
        self.project_path = path
        self.dirty = False
        self.project_state.setText("Projeto salvo")
        self.statusBar().showMessage(f"Projeto salvo em {path}", 6000)

    def _write_project(self, path: str):
        res = NestResult(self.placements, self.n_sheets, self._utilization(), 0.0, self.unplaced)
        save_project(path, self.files, self.settings_panel.params(), self.parts, res,
                     multipliers=self.file_multipliers, label=self.request_label,
                     materials=self.file_materials, request=self.request_info, tags=self.file_tags,
                     checklist={"cut": sorted(self.cut_sheets), "done": sorted(self.done_parts)},
                     file_units=self.file_units, source_hashes=self.source_hashes, report=self.report)

    @staticmethod
    def autosave_path() -> str:
        if os.environ.get("SINDRI_DATA_DIR"):
            return os.path.join(os.environ["SINDRI_DATA_DIR"], "ultimo_trabalho.sindri")
        from ...core.intranet import default_base_folder
        return os.path.join(os.path.dirname(default_base_folder()), "ultimo_trabalho.sindri")

    def schedule_autosave(self):
        if not hasattr(self, "_autosave_timer"):
            self._autosave_timer = QTimer(self)
            self._autosave_timer.setSingleShot(True)
            self._autosave_timer.timeout.connect(self.autosave)
        self._autosave_timer.start(2000)

    def autosave(self):
        """Guarda o trabalho atual (encaixe + checklist) para recuperar se o programa fechar sem salvar."""
        if getattr(self, "_ui_task_depth", 0):
            self.schedule_autosave()
            return
        if not self.files or not self.dirty or self.worker is not None:
            return
        try:
            path = self.autosave_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            self._write_project(path)
            settings().setValue("autosave/clean", "false")
            settings().setValue("autosave/when", now_txt())
            self.project_state.setText("Alterações não salvas · recuperação: " + now_txt())
        except Exception as e:
            self.statusBar().showMessage(f"Falha no salvamento automático: {e}", 15000)

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
        self.show_banner("O último trabalho" + (f" ({when})" if when else "") + " não foi salvo. "
                         '<a href="recover:">Recuperar</a> · <a href="discard:">Descartar</a>', "warn")

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
            proj = run_task(self, "Abrindo projeto de encaixe", lambda: load_project(path))
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
        self.file_units = dict(proj.units)
        self.source_hashes = dict(proj.source_hashes)
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
        self.project_state.setText("Projeto aberto")
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

    def _save_params(self):
        settings().setValue("ui/last_params", json.dumps(self.settings_panel.params().to_json()))
        settings().setValue("autosave/clean", "true")      # fechou normalmente (salvou ou descartou)
