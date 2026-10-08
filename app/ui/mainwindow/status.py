"""Desenho e estado: redesenhar, navegar entre placas, métricas e botões."""
from __future__ import annotations

from ..icons import icon
from ..owners import owner_colors
from .common import fmt_pct


class StatusMixin:
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
        self.canvas.filter_tag = self.parts_panel.filter_tag
        nc = self.numbers_cfg()
        self.canvas.engrave_labels = self._part_labels()
        self.canvas.engrave_height = float(nc.get("height") or 3.0)
        self.canvas.engrave_aci = int(nc.get("color", 1))
        self.canvas.show_layout(self.pmap, self.placements, self.settings_panel.params(), n, keep_view)
        self.canvas.set_editable(self.worker is None)
        if self.worker is None:              # enquanto calcula, o encaixe não tem colisões e o checklist recomeça
            self._refresh_cut_panel()
            self._mark_collisions()
        self._update_sheet_label()

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
        max(pl.sheet_index for pl in self.placements) + 1
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
        from ...core.dxf_export import sheet_groups
        groups = sheet_groups(self.pmap, self.placements) if placed else []
        if len(groups) > 1 or (groups and groups[0][0]):
            self.chip_sheets.setText(" + ".join(str(len(sis)) for _, sis in groups) if len(groups) > 1
                                     else str(sheets))
            self.chip_sheets.setToolTip("\n".join(f"{m or 'sem material'}: {len(sis)} placa(s)" for m, sis in groups))
        self.chip_parts.setText(f"{placed}/{total}" if total else "—")
        cut = sum(pl.sheet_index in self.cut_sheets for pl in self.placements)
        self.chip_parts.setToolTip(f"Solicitadas: {total}\nEncaixadas: {placed}\nEm placas cortadas: {cut}\nSem encaixe: {max(0, total - placed)}")
        ev = getattr(self, "evaluated", 0)
        self.chip_gen.setText(f"{ev:,}".replace(",", ".") if (self.worker is not None or ev) else "—")
        missing = total - placed
        if self.worker is not None:
            paused = self.worker.pause_event.is_set()
            since, stop_after = getattr(self, "since_improve", 0.0), getattr(self, "stop_after", 0.0)
            txt = f"Otimizando… melhorou há {since:.0f} s"
            if stop_after > 0 and self.generation >= 2:
                txt += f" · para sozinho em {max(0, stop_after - since):.0f} s"
            kind, txt = "run", ("Pausado" if paused else txt)
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
        self.btn_nest.setEnabled(has_parts)
        from ..theme import tokens as _tk
        self.btn_nest.setIcon(icon("stop" if running else "play", "#ffffff", 14, _tk()["muted"]))
        if running:
            self.btn_nest.setText("Parar  (Esc)")
            self.btn_nest.setObjectName("danger")
            self.btn_nest.setToolTip("Para e mantém a melhor solução encontrada (Esc)")
        else:
            self.btn_nest.setText("Encaixar" if not self.placements else "Encaixar de novo")
            self.btn_nest.setObjectName("primary")
            self.btn_nest.setToolTip("Organiza as peças automaticamente (Espaço). Peças travadas ficam onde estão.")
        self.btn_nest.style().unpolish(self.btn_nest)
        self.btn_nest.style().polish(self.btn_nest)
        self.btn_pause.setVisible(running)
        self.btn_pause.setEnabled(running)
        self.btn_stop.setVisible(False)
        self.btn_export.setEnabled(bool(self.placements))
        self.btn_export_menu.setEnabled(bool(self.placements))
        self.btn_save.setEnabled(has_parts)
        self.parts_panel.btn_clear.setEnabled(has_parts)
        self.a_undo.setEnabled(bool(self.undo_stack) and not running)
        self.a_redo.setEnabled(bool(self.redo_stack) and not running)
        paused = running and self.worker.pause_event.is_set()
        self.btn_pause.setText("Continuar" if paused else "Pausar")
        from ..theme import tokens
        self.btn_pause.setIcon(icon("play" if paused else "pause", tokens()["text"], 14, tokens()["muted"]))
        editing = self.canvas.mode == "layout" and not running and bool(self.placements)
        for b in (self.btn_rot, self.btn_lock, self.btn_del):
            b.setEnabled(editing)
        self.stack.setCurrentIndex(1 if self.parts else 0)
        self.parts_panel.set_editing(not running)
        self.settings_panel.setEnabled(not running)
        self._update_sheet_label()
