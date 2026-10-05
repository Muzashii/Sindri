"""Checklist de corte: placas cortadas, peças feitas e filtro por solicitação."""
from __future__ import annotations

from ..owners import sheet_numbers


class ChecklistMixin:
    def _refresh_cut_panel(self):
        """Atualiza, na aba Peças, as caixinhas das placas e as peças feitas (e a cor no desenho)."""
        from ...core.dxf_export import sheet_material
        sheets = []
        if self.placements:
            nums = sheet_numbers(self.pmap, self.placements)
            for si, n in sorted(nums.items(), key=lambda kv: kv[1]):
                sheets.append({"si": si, "n": n, "material": sheet_material(self.pmap, self.placements, si),
                               "count": sum(1 for pl in self.placements if pl.sheet_index == si)})
        self.parts_panel.set_sheets(sheets, self.cut_sheets)
        progress = {}
        for pl in self.placements:
            c, t = progress.get(pl.part_id, (0, 0))
            progress[pl.part_id] = (c + (pl.sheet_index in self.cut_sheets), t + 1)
        self.parts_panel.set_done(self.done_parts, progress)
        tags = {p.tag for p in self.parts if p.tag}
        done_tags = {t for t in tags if all(p.id in self.done_parts for p in self.parts if p.tag == t)}
        tag_prog = {}
        for pl in self.placements:
            t = self.pmap[pl.part_id].tag
            if t:
                c, n = tag_prog.get(t, (0, 0))
                ok = pl.part_id in self.done_parts or pl.sheet_index in self.cut_sheets
                tag_prog[t] = (c + ok, n + 1)
        if self.request_info:
            self.parts_panel.set_request(self.request_info, done_tags, tag_prog)
        self.canvas.done_parts = self.done_parts
        if self.canvas.mode == "layout":
            for it in self.canvas.part_items:
                it.update()

    def toggle_current_cut(self):
        """C: marca/desmarca a placa que está no centro da tela; ao marcar, vai para a próxima não cortada."""
        if self.canvas.mode != "layout" or not self.placements or self.worker is not None:
            return
        si = self.canvas.current_sheet()
        on = si not in self.cut_sheets
        self.on_sheet_cut(si, on)
        if on:
            nums = sheet_numbers(self.pmap, self.placements)
            order = [s for s, _ in sorted(nums.items(), key=lambda kv: kv[1])]
            later = [s for s in order[order.index(si) + 1:] if s not in self.cut_sheets] if si in order else []
            nxt = later[0] if later else next((s for s in order if s not in self.cut_sheets), None)
            if nxt is not None:
                self.goto_sheet(nxt)

    def filter_request_index(self, k: int):
        """Alt+1…9: só a solicitação k do lote; Alt+0: todas."""
        info = self.request_info or {}
        if not info.get("batch"):
            return
        codes = [str(r.get("code", "")) for r in info.get("requests", [])]
        if k == 0:
            self.parts_panel.set_filter("")
        elif k <= len(codes):
            tag = codes[k - 1]
            self.parts_panel.set_filter("" if self.parts_panel.filter_tag == tag else tag)

    def on_request_filter(self, tag: str):
        """Mostra no desenho só as peças de uma solicitação (as outras ficam apagadas)."""
        self.canvas.set_filter(tag)
        if not tag:
            self.statusBar().showMessage("Mostrando todas as solicitações.", 4000)
            return
        if self.placements and self.tabs.currentIndex() != 1:
            self.tabs.setCurrentIndex(1)
        sheets = sorted({pl.sheet_index for pl in self.placements if self.pmap[pl.part_id].tag == tag})
        nums = sheet_numbers(self.pmap, self.placements)
        n = sum(1 for pl in self.placements if self.pmap[pl.part_id].tag == tag)
        if sheets:
            self.goto_sheet(sheets[0])
            self.statusBar().showMessage(
                f"Solicitação {tag}: {n} peça(s) na(s) placa(s) {', '.join(str(nums.get(s, s + 1)) for s in sheets)}. "
                "Clique de novo nela para ver todas.", 10000)
        else:
            self.statusBar().showMessage(f"Solicitação {tag}: nenhuma peça encaixada ainda.", 6000)

    def on_part_done(self, pid: str, on: bool):
        (self.done_parts.add if on else self.done_parts.discard)(pid)
        self.dirty = True
        self._refresh_cut_panel()
        self.schedule_autosave()
        if on and self.parts and all(p.id in self.done_parts for p in self.parts):
            self.statusBar().showMessage("Todas as peças feitas! 🎉", 8000)

    def on_sheet_cut(self, si: int, on: bool):
        """Placa cortada: as peças que só aparecem em placas cortadas ficam como feitas."""
        (self.cut_sheets.add if on else self.cut_sheets.discard)(si)
        on_sheet = {pl.part_id for pl in self.placements if pl.sheet_index == si}
        for pid in on_sheet:
            sheets_of = {pl.sheet_index for pl in self.placements if pl.part_id == pid}
            if on and sheets_of <= self.cut_sheets:
                self.done_parts.add(pid)
            elif not on:
                self.done_parts.discard(pid)
        self.dirty = True
        if self.canvas.mode == "layout":
            self.canvas.set_cut(self.cut_sheets)
        self._refresh_cut_panel()
        self.schedule_autosave()
        nums = sheet_numbers(self.pmap, self.placements)
        if on and len(self.cut_sheets) >= len(nums):
            self.statusBar().showMessage("Todas as placas cortadas! 🎉", 8000)

    def reset_checklist(self):
        self.cut_sheets.clear()
        self.done_parts.clear()
        if self.canvas.mode == "layout":
            self.canvas.set_cut(self.cut_sheets)
        self._refresh_cut_panel()
