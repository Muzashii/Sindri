"""Encaixe automático: iniciar, acompanhar, pausar e parar o cálculo."""
from __future__ import annotations

import copy
import json

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox

from ...core.collision import CollisionChecker
from ...core.models import NestResult
from ...core.placement import Decoder
from ...workers.nest_worker import NestWorker
from ..dialogs import settings


class NestingMixin:
    def _nest_button(self):
        """Um botão só: Encaixar quando parado, Parar quando está calculando."""
        if self.worker is not None:
            self.stop_nest()
        else:
            self.start_nest()

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
        if self.banner.property("kind") == "info":
            self.banner.hide()
        # o cálculo usa uma cópia das peças (a tela pode mudar enquanto ele roda)
        self.worker = NestWorker(copy.deepcopy(self.parts), p, copy.deepcopy(locked), workers=self.workers)
        w = self.worker
        self.worker.bestFound.connect(lambda r, w=w: self.on_best(r) if w is self.worker else None)
        self.worker.progress.connect(lambda i, w=w: self.on_progress(i) if w is self.worker else None)
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
        # agrupa os redesenhos (no máximo ~5 por segundo enquanto calcula)
        if not hasattr(self, "_best_timer"):
            self._best_timer = QTimer(self)
            self._best_timer.setSingleShot(True)
            self._best_timer.timeout.connect(self._show_best)
        if not self._best_timer.isActive():
            self._best_timer.start(0 if not self._layout_shown_once else 200)
        self.btn_export.setEnabled(bool(self.placements))
        self._update_status()

    def _show_best(self):
        self._redraw(keep_view=self._layout_shown_once)
        self._layout_shown_once = True

    def on_progress(self, info: dict):
        self.generation = info.get("generation", 0)
        self.evaluated = info.get("evaluated", 0)
        self.since_improve = info.get("since_improve", 0.0)
        self.stop_after = info.get("stop_after", 0.0)
        self._auto_stopped = info.get("auto_stopped", False)
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
        if getattr(self, "_best_timer", None) is not None:
            self._best_timer.stop()
        self._redraw(keep_view=True)          # versão final, com colisões e checklist
        self.canvas.set_editable(True)
        self.schedule_autosave()
        self._update_buttons()
        self._update_status()
        missing = sum(p.quantity for p in self.parts) - len(self.placements)
        msg = "Encaixe concluído." if missing <= 0 else f"Encaixe concluído — {missing} peça(s) não couberam."
        if getattr(self, "_auto_stopped", False):
            msg = "Encaixe pronto (parou sozinho, sem melhorar mais)." if missing <= 0 else msg
        self._auto_stopped = False
        if self.placements and missing <= 0:
            msg += " Ctrl+E exporta."
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

    def _rebuild_checker(self):
        p = self.settings_panel.params()
        self.checker = CollisionChecker(self.parts, p) if self.parts else None
        self.too_big = set()
        if self.parts:
            dec = Decoder(self.checker.cache.shapes, p, cache=self.checker.cache)
            mo = (False, True) if p.allow_mirror else (False,)
            for pt in self.parts:
                rots = [0.0] if pt.rotation_locked else p.rotations()
                if not dec.fits_sheet(pt.id, rots, mo):
                    self.too_big.add(pt.id)

    def _params_debounced(self):
        """Cada passo de um campo numérico espera 150 ms antes de recalcular tudo."""
        if not hasattr(self, "_params_timer"):
            self._params_timer = QTimer(self)
            self._params_timer.setSingleShot(True)
            self._params_timer.timeout.connect(self.on_params_changed)
        self._params_timer.start(150)

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
            old_big = set(self.too_big)
            self._rebuild_checker()
            if self.too_big != old_big:
                self.parts_panel.set_parts(self.parts, self.too_big)
            if self.placements:
                self._redraw(keep_view=(old.sheet_width == new.sheet_width and old.sheet_height == new.sheet_height))
                if self.worker is None:
                    self.statusBar().showMessage("Parâmetros alterados — clique em Encaixar para refazer o encaixe.", 6000)
        self._update_status()
