"""Janela principal do Sindri.

A lógica fica em app/ui/mainwindow/ (uma parte por assunto); aqui só a montagem e o fechamento."""
from __future__ import annotations

import json
from typing import Optional

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMainWindow, QMessageBox

from ..core.collision import CollisionChecker
from ..core.models import ImportReport, NestParams, Part, Placement
from ..workers.nest_worker import NestWorker
from .dialogs import settings
from .mainwindow.boxes import BoxMixin
from .mainwindow.checklist import ChecklistMixin
from .mainwindow.common import APP_NAME
from .mainwindow.editing import EditingMixin
from .mainwindow.export import ExportMixin
from .mainwindow.materials import MaterialsMixin
from .mainwindow.updates import UpdatesMixin
from .mainwindow.files import FilesMixin
from .mainwindow.nesting import NestingMixin
from .mainwindow.projects import ProjectMixin
from .mainwindow.status import StatusMixin
from .mainwindow.ui_build import UIBuildMixin


class MainWindow(UIBuildMixin, FilesMixin, ProjectMixin, NestingMixin, EditingMixin, ChecklistMixin,
                 StatusMixin, ExportMixin, MaterialsMixin, UpdatesMixin, BoxMixin, QMainWindow):
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
        self.file_units: dict[str, int] = {}
        self.source_hashes: dict = {}
        self.cut_sheets: set[int] = set()        # placas já cortadas (checklist)
        self.done_parts: set[str] = set()        # peças marcadas como feitas (cortadas)
        self.color_ops: dict = {}                # (material, cor do arquivo) -> corte/vinco/gravação
        self.color_ops_confirmed: set = set()    # cores que o técnico já conferiu
        self.sheet_files: dict[int, str] = {}    # placa -> DXF de corte exportado
        self.sheet_stamps: dict[int, str] = {}   # placa -> impressão digital do que foi exportado
        self.sheet_remnants: dict[int, str] = {} # placa -> id do retalho usado (sem = chapa inteira)
        self.saved_leftover: dict[int, float] = {}  # placa -> mm² de sobra guardada como retalho
        self.request_label: Optional[str] = None
        self.request_info: Optional[dict] = None
        self.generation = 0
        self.evaluated = 0
        self._layout_shown_once = False

        self._build_ui()
        self._build_actions()
        from .accessibility import configure
        configure(self)
        from .nowheel import protect
        protect(self)                                 # roda do mouse não muda valores dos campos
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
        scr = self.screen()
        self.apply_width(scr.availableGeometry().width() if scr else 1600, force=True)
        self._drag_timer = QTimer(self)
        self._drag_timer.setSingleShot(True)
        self._drag_timer.setInterval(30)
        self._drag_timer.timeout.connect(self._check_drag_collisions)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.apply_width(self.width())

    def closeEvent(self, e):
        if getattr(self, "_ui_task_depth", 0):
            self.statusBar().showMessage("Aguarde a conclusão da operação para fechar.", 5000)
            e.ignore()
            return
        if getattr(self, "_applying_update", False):
            self.statusBar().showMessage("Aguarde a conclusão da atualização para fechar.", 5000)
            e.ignore()
            return
        if self.worker is not None:
            self.stop_nest(wait=True)
        if self.dirty and self.files:
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
        self.flush_material_db()
        self._save_params()                           # parâmetros + "fechou normalmente"
        if getattr(self, "_autosave_timer", None) is not None:
            self._autosave_timer.stop()
        dlg = getattr(self, "_intranet_dlg", None)
        if dlg is not None:  # a página do navegador precisa sair antes do perfil
            from PySide6.QtCore import QCoreApplication, QEvent
            dlg.view.setPage(None)
            dlg.page.deleteLater()
            dlg.deleteLater()
            self._intranet_dlg = None
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        e.accept()
        if getattr(self, "_restart_after_close", False):
            from ..core.updater import restart
            restart()
