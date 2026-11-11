"""Desenho e estado: redesenhar, navegar entre placas, métricas e botões."""
from __future__ import annotations

from ..icons import icon
from ..owners import owner_colors
from .common import fmt_pct


class StatusMixin:
    def _tab_changed(self, i: int):
        if i == 0:
            self.show_original()
        else:
            self._redraw()
        self._sync_review_visibility()
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
        self.canvas.show_layout(self.pmap, self.placements, self.nest_params(), n, keep_view,
                                specs=self.sheet_specs())
        self.canvas.set_editable(self.worker is None)
        if self.worker is None:              # enquanto calcula, o encaixe não tem colisões e o checklist recomeça
            self._refresh_cut_panel()
            self._mark_collisions()
        self._update_sheet_label()

    def _mark_collisions(self) -> int:
        if not self.checker or not self.canvas.part_items:
            self._collision_count = 0
            return 0
        pls = [it.placement for it in self.canvas.part_items]
        bad = self.checker.colliding(pls, sheet_remnants=self.sheet_remnants)
        for i, it in enumerate(self.canvas.part_items):
            c = i in bad
            if it.colliding != c:
                it.colliding = c
                it.update()
        self._collision_count = len(bad)
        self._update_status()
        return len(bad)

    def _update_sheet_label(self):
        n = max(1, self.n_sheets)
        cur = min(self.canvas.current_sheet(), n - 1) + 1 if self.canvas.mode == "layout" else 0
        if self.canvas.mode == "layout":
            # janela estreita: "1/2" deixa espaço para o resumo da seleção na mesma barra
            tiny = getattr(self, "_width_mode", (False, False))[1]
            self.sheet_label.setText(f" {cur}/{n} " if tiny else f"  Placa {cur} de {n}  ")
            self.sheet_label.setToolTip(f"Placa {cur} de {n} (PgUp / PgDn)")
        else:
            self.sheet_label.setText("")
        on = self.canvas.mode == "layout" and n > 1
        self.btn_prev.setEnabled(on and cur > 1)
        self.btn_next.setEnabled(on and cur < n)

    def goto_sheet(self, i: int):
        if self.canvas.mode != "layout":
            return
        n = max(1, self.n_sheets)
        i = max(0, min(n - 1, i))
        self.canvas.focus_sheet(i)
        self._update_sheet_label()

    def _utilization(self) -> float:
        if not self.placements:
            return 0.0
        specs = self.sheet_specs()
        used = {pl.sheet_index for pl in self.placements}
        area = sum(self.pmap[pl.part_id].outer.area - sum(h.area for h in self.pmap[pl.part_id].holes)
                   for pl in self.placements)
        total = sum(specs[si].area for si in used if si in specs)
        return area / total if total else 0.0

    def _usage(self) -> dict:
        """Aproveitamento das placas cheias × última placa de cada material (ver canvas.usage_split)."""
        from ..canvas import usage_split
        if not self.placements:
            return {"total": 0.0, "full": None, "last": 0.0, "lasts": set(), "low": set(), "by_sheet": {},
                    "areas": {}}
        specs = self.sheet_specs()
        by_sheet: dict[int, float] = {}
        for pl in self.placements:
            part = self.pmap[pl.part_id]
            by_sheet[pl.sheet_index] = by_sheet.get(pl.sheet_index, 0) + part.outer.area - sum(h.area for h in part.holes)
        areas = {si: specs[si].area for si in by_sheet if si in specs}
        out = usage_split(by_sheet, areas, self.sheet_index().material)
        out["by_sheet"], out["areas"] = by_sheet, areas
        return out

    def _update_util_chip(self):
        """Rodapé: com 2+ placas, "Cheias 59,6% · última 8,5%" (o total engana quando a última placa
        está quase vazia); com uma placa só, o total. Compacto em janelas estreitas."""
        cap = self._metric_caps["aproveitamento"][0]
        tiny = getattr(self, "_width_mode", (False, False))[1]
        compact = getattr(self, "_width_mode", (False, False))[0]
        if not self.placements:
            self.progress.setValue(0)
            self.chip_util.setText("—")
            cap.setText("aproveit. total" if compact else "aproveitamento total")
            self.chip_util.setToolTip("Aproveitamento: área das peças ÷ área das placas usadas")
            return
        us = self._usage()
        idx = self.sheet_index()
        from ..theme import tokens
        muted = f"color: {tokens()['muted']}; font-size: 9pt; font-weight: 600;"
        lasts_word = "última" if len(us["lasts"]) == 1 else "últimas"
        if us["full"] is None:
            self.progress.setValue(int(round(us["total"] * 1000)))
            self.chip_util.setText(fmt_pct(us["total"]))
            cap.setText("aproveit. total" if compact else "aproveitamento total")
        else:
            self.progress.setValue(int(round(us["full"] * 1000)))
            if tiny:
                self.chip_util.setText(f"{fmt_pct(us['full'])}<span style='{muted}'> · </span>"
                                       f"{fmt_pct(us['last'])}")
                cap.setText("cheias · " + lasts_word)
            else:
                self.chip_util.setText(f"<span style='{muted}'>Cheias </span>{fmt_pct(us['full'])}"
                                       f"<span style='{muted}'> · {lasts_word} </span>{fmt_pct(us['last'])}")
                cap.setText("aproveitamento")
        detail = "\n".join(f"Placa {idx.number.get(si, si + 1)}"
                           + (f" ({idx.material.get(si)})" if idx.material.get(si) else "")
                           + f": {fmt_pct(area / us['areas'][si])}"
                           + (" · pouco usada" if si in us["low"] else "")
                           for si, area in sorted(us["by_sheet"].items()) if us["areas"].get(si))
        head = ("Cheias: área das peças ÷ área das placas, sem contar a última de cada material.\n"
                f"{lasts_word.capitalize()}: quanto da última placa de cada material foi usado "
                "(a sobra pode virar retalho).\n" if us["full"] is not None else "")
        self.chip_util.setToolTip(head + f"Aproveitamento total: {fmt_pct(us['total'])} "
                                  "(área das peças ÷ área das placas usadas)" + ("\n" + detail if detail else ""))
        self.chip_util.setAccessibleName(
            f"Aproveitamento: placas cheias {fmt_pct(us['full'])}, {lasts_word} {fmt_pct(us['last'])}"
            if us["full"] is not None else f"Aproveitamento total {fmt_pct(us['total'])}")

    def _update_time_chip(self):
        """Tempo estimado de corte no rodapé. Calcula um instante depois (vários eventos seguidos —
        arrastar, encaixe melhorando — viram um cálculo só)."""
        t = getattr(self, "_time_timer", None)
        if t is None:
            from PySide6.QtCore import QTimer
            t = self._time_timer = QTimer(self)
            t.setSingleShot(True)
            t.timeout.connect(self._refresh_time_chip)
        t.start(400)

    def _refresh_time_chip(self):
        """Soma o tempo estimado das placas; com placas já cortadas, mostra quanto falta."""
        from ...core.machine_time import fmt_duration
        cap, val = self._metric_caps["tempo de corte"]
        if not self.placements:
            val.setText("—")
            cap.setText("tempo de corte")
            val.setToolTip("Tempo estimado de corte: aparece depois do encaixe.")
            return
        try:
            times = self._sheet_times(self._laser_export_plan())
        except Exception:                               # a estimativa é informativa: nunca atrapalha o encaixe
            from ...core.fileutil import log_exception
            log_exception("rodapé: tempo estimado de corte")
            val.setText("—")
            return
        idx = self.sheet_index()
        order = [si for si in idx.ordered if si in times]
        left = [si for si in order if si not in self.cut_sheets]
        total = sum(times[si].seconds for si in order)
        rest = sum(times[si].seconds for si in left)
        if not left:
            val.setText("✓")
            cap.setText("tudo cortado")
        elif len(left) < len(order):
            val.setText(f"~{fmt_duration(rest)}")
            cap.setText("falta cortar")
        else:
            val.setText(f"~{fmt_duration(total)}")
            cap.setText("tempo de corte")
        lines = [f"Placa {idx.number.get(si, si + 1)}: {times[si].text}"
                 + (" · cortada" if si in self.cut_sheets else "") for si in order]
        calib = next((times[si].calibration_text for si in order if times[si].calibrated_n), "")
        tip = ["Tempo estimado de corte (laser ligado + deslocamentos), pelas velocidades do painel Laser.",
               f"Total do lote: ~{fmt_duration(total)}" + (f" · falta ~{fmt_duration(rest)}" if rest < total else "")]
        tip += lines
        if any(times[si].fallback for si in order):
            tip.append("Camadas sem velocidade cadastrada usam um valor padrão: preencha a velocidade para afinar.")
        tip.append(calib.strip().capitalize() + "." if calib else
                   "Fica mais preciso sozinho: abra a placa pelo Sindri e marque como cortada (3 cortes calibram).")
        val.setToolTip("\n".join(tip))
        cap.setToolTip(val.toolTip())

    def _warn_low_use(self):
        """Aviso não modal quando a última placa de um material ficou quase vazia (≥ 2 placas)."""
        us = self._usage()
        if not us["low"]:
            return
        idx = self.sheet_index()
        bits = [f"placa {idx.number.get(si, si + 1)}" + (f" de {idx.material.get(si)}" if idx.material.get(si) else "")
                + f" com {fmt_pct(us['by_sheet'][si] / us['areas'][si])}" for si in sorted(us["low"])]
        self.show_banner("Última placa pouco usada: " + "; ".join(bits) + ". "
                         '<a href="renest:">Encaixar de novo com mais tempo</a> · '
                         '<a href="remnants:">Ver retalhos</a>', "warn")

    def _update_status(self):
        total = sum(p.quantity for p in self.parts)
        placed = len(self.placements)
        self._update_util_chip()
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
        self._update_time_chip()
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
        elif getattr(self, "_collision_count", 0):
            kind, txt = "warn", f"⚠ {self._collision_count} peça(s) com colisão, borda ou material incompatível"
        elif self.placements and missing > 0 and not self.settings_panel.multi.isChecked():
            kind, txt = "warn", f"⚠ {missing} sem lugar · ligue “Abrir nova placa se não couber” em Encaixe"
        elif self.placements and missing > 0:
            kind, txt = "warn", f"⚠ {missing} sem lugar"
        elif self.placements:
            kind, txt = "ok", "✓ Todas encaixadas · exportação fará validação final"
        else:
            kind, txt = "", "Clique em Encaixar"
        self.status_label.setProperty("kind", kind)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        self.status_label.setText(txt)
        from ..accessibility import announce
        announce(self.status_label, "Estado do encaixe: " + txt)

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
            tiny = getattr(self, "_width_mode", (False, False))[1]     # janela estreita: texto curto
            self.btn_nest.setText("Encaixar" if not self.placements or tiny else "Encaixar de novo")
            self.btn_nest.setObjectName("primary")
            self.btn_nest.setToolTip("Organiza as peças automaticamente (Espaço). Peças travadas ficam onde estão.")
        self.btn_nest.style().unpolish(self.btn_nest)
        self.btn_nest.style().polish(self.btn_nest)
        self.btn_pause.setVisible(running)
        self.btn_pause.setEnabled(running)
        self.btn_stop.setVisible(False)
        self.btn_export.setEnabled(bool(self.placements))
        self.btn_save_as.setEnabled(bool(self.placements))
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
        # o resumo/dica da seleção só faz sentido no encaixe (no original não há o que selecionar)
        if self.canvas.mode != "layout":
            self.selection_summary.setText("")
        elif not self.selection_summary.full_text():
            from .ui_build import SELECTION_HINT
            self.selection_summary.setText(SELECTION_HINT)
        self.stack.setCurrentIndex(1 if self.parts else 0)
        self.parts_panel.set_editing(not running)
        self.settings_panel.setEnabled(not running)
        self._update_sheet_label()
