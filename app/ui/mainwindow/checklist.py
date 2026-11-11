"""Checklist de corte: placas cortadas, peças feitas e filtro por solicitação."""
from __future__ import annotations

from ...core.sheets import SheetIndex, checklist_progress


class ChecklistMixin:
    def sheet_index(self) -> SheetIndex:
        return SheetIndex(self.pmap, self.placements)

    def _refresh_cut_panel(self):
        """Atualiza, na aba Peças, as caixinhas das placas e as peças feitas (e a cor no desenho)."""
        idx = self.sheet_index()
        sheets = [{"si": si, "n": idx.number[si], "material": idx.material[si], "count": idx.count(si)}
                  for si in idx.ordered]
        self.parts_panel.set_sheets(sheets, self.cut_sheets)
        per_part, per_tag, done_tags = checklist_progress(idx, self.parts, self.cut_sheets, self.done_parts)
        self.parts_panel.set_done(self.done_parts, per_part)
        if self.request_info:
            self.parts_panel.set_request(self.request_info, done_tags, per_tag)
        self._refresh_laser_panel()
        self.canvas.done_parts = self.done_parts
        if self.canvas.mode == "layout":
            for it in self.canvas.part_items:
                it.update()

    def toggle_current_cut(self):
        """C: marca/desmarca a placa que está no centro da tela; ao marcar, vai para a próxima não cortada."""
        if self.canvas.mode != "layout" or not self.placements or self.worker is not None:
            return
        si = self.canvas.current_sheet()
        if si not in self.cut_sheets:
            self.mark_cut_and_advance(si)
        else:
            self.on_sheet_cut(si, False)

    def next_uncut_sheet(self, after: int | None = None) -> int | None:
        """Próxima placa ainda não cortada (depois de ``after`` na numeração; senão a 1ª que falta)."""
        order = self.sheet_index().ordered
        later = [s for s in order[order.index(after) + 1:] if s not in self.cut_sheets] if after in order else []
        return later[0] if later else next((s for s in order if s not in self.cut_sheets), None)

    def mark_cut_and_advance(self, si: int) -> int | None:
        """Marca a placa como cortada e vai para a próxima não cortada. Com a preferência "abrir a
        próxima" ligada, já abre essa próxima no RDWorks (um C faz o ciclo inteiro). Devolve a próxima."""
        if not self.placements or self.worker is not None or si in self.cut_sheets:
            return None
        self.on_sheet_cut(si, True)
        nxt = self.next_uncut_sheet(si)
        if nxt is not None:
            self.goto_sheet(nxt)
            if self.open_next_on_cut():
                note = self.__dict__.pop("_cut_time_shown", "")
                self.open_sheet_in_rdworks(nxt)
                if note:                               # o aviso da placa aberta cobriu o do tempo real
                    self.statusBar().showMessage(note, 15000)
        return nxt

    # ---- preferência: C faz o ciclo inteiro (marca, vai para a próxima e abre no RDWorks)
    def open_next_on_cut(self) -> bool:
        from ..prefs import get_bool
        return get_bool("bancada/abrir_proxima", False)

    def set_open_next_on_cut(self, on: bool = True):
        from ..prefs import set_bool
        set_bool("bancada/abrir_proxima", bool(on))
        a = getattr(self, "a_open_next", None)
        if a is not None and a.isChecked() != bool(on):
            a.setChecked(bool(on))
        self.statusBar().showMessage(
            "Ao marcar uma placa como cortada, a próxima abre no RDWorks." if on
            else "Marcar como cortada só vai para a próxima placa (sem abrir o RDWorks).", 6000)

    def _init_open_next_action(self, action):
        """Deixa a ação do menu marcável e com o estado salvo."""
        action.setCheckable(True)
        action.setChecked(self.open_next_on_cut())
        self.a_open_next = action
        return action

    # ---- modo bancada (tela cheia para a hora do corte)
    def toggle_bench_mode(self):
        bench = getattr(self, "_bench", None)
        if bench is not None and bench.isVisible():
            bench.close()
            return
        if not self.placements or self.worker is not None or self.canvas.mode != "layout":
            self.statusBar().showMessage("Modo bancada: faça o encaixe primeiro (ou espere ele terminar).", 6000)
            return
        from ..bench_mode import BenchMode
        self._bench = BenchMode(self)
        self._bench.open_fullscreen()

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
        ready = tag in self.ready_requests()
        if ready:
            self.statusBar().showMessage(
                f"Solicitação {tag} pronta ✓ · Arquivo › “Solicitações prontas para retirada…” copia o recado e "
                "abre a página dela na intranet.", 10000)
        if self.placements and self.tabs.currentIndex() != 1:
            self.tabs.setCurrentIndex(1)
        idx = self.sheet_index()
        mine = [pl for pl in self.placements if self.pmap[pl.part_id].tag == tag]
        sheets = sorted({pl.sheet_index for pl in mine}, key=lambda s: idx.number.get(s, s))
        if sheets:
            self.goto_sheet(sheets[0])
            if ready:
                return
            self.statusBar().showMessage(
                f"Solicitação {tag}: {len(mine)} peça(s) na(s) placa(s) "
                f"{', '.join(str(idx.number.get(s, s + 1)) for s in sheets)}. Clique de novo nela para ver todas.",
                10000)
        elif not ready:
            self.statusBar().showMessage(f"Solicitação {tag}: nenhuma peça encaixada ainda.", 6000)

    def on_part_done(self, pid: str, on: bool):
        before = set(self.ready_requests())
        self._push_undo()
        (self.done_parts.add if on else self.done_parts.discard)(pid)
        self.mark_changed()
        self._refresh_cut_panel()
        self.schedule_autosave()
        if on and self.parts and all(p.id in self.done_parts for p in self.parts):
            self.statusBar().showMessage("Todas as peças feitas! 🎉", 8000)
        self._announce_ready(before)

    def on_sheet_cut(self, si: int, on: bool):
        """Placa cortada: as peças que só aparecem em placas cortadas ficam como feitas."""
        before = set(self.ready_requests())
        self._push_undo()
        (self.cut_sheets.add if on else self.cut_sheets.discard)(si)
        if on:                                         # tempo real × estimado (se a placa foi aberta daqui)
            self._cut_time_note = self._cut_time_shown = self._record_real_time(si)
        else:
            self._forget_real_time(si)
        idx = self.sheet_index()
        for pid in {pl.part_id for pl in idx.by_sheet.get(si, ())}:
            instances = {pl.instance for pl in self.placements if pl.part_id == pid and pl.sheet_index in self.cut_sheets}
            if on and instances == set(range(self.pmap[pid].quantity)):
                self.done_parts.add(pid)
            elif not on:
                self.done_parts.discard(pid)
        self.mark_changed()
        if self.canvas.mode == "layout":
            self.canvas.set_cut(self.cut_sheets)
        self._refresh_cut_panel()
        self.schedule_autosave()
        self._remnant_on_cut(si, on)
        if on and len(self.cut_sheets) >= len(idx.number):
            self.statusBar().showMessage("Todas as placas cortadas! 🎉", 8000)
        self._announce_ready(before)

    def reset_checklist(self):
        self._push_undo()
        self.cut_sheets.clear()
        self.done_parts.clear()
        if self.canvas.mode == "layout":
            self.canvas.set_cut(self.cut_sheets)
        self._refresh_cut_panel()
        self.mark_changed()

    # ---- fechar o ciclo com o aluno (o Sindri nunca muda o status na intranet)
    def ready_requests(self) -> list[str]:
        """Solicitações abertas com todas as peças feitas (ou nas placas cortadas), na ordem do lote."""
        from ...core.intranet import ready_codes
        if not getattr(self, "request_info", None) or not self.parts:
            return []
        _, _, done_tags = checklist_progress(self.sheet_index(), self.parts, self.cut_sheets, self.done_parts)
        return ready_codes(self.request_info, done_tags, all(p.id in self.done_parts for p in self.parts))

    def request_ready_actions(self, code) -> list:
        """[(texto, função)] para o cartão da solicitação quando ela está pronta (vazio se não está)."""
        code = str(code)
        if code not in self.ready_requests():
            return []
        return [("Copiar recado de retirada", lambda c=code: self.copy_pickup_note(c)),
                ("Abrir na intranet", lambda c=code: self.open_request_in_intranet(c))]

    def copy_pickup_note(self, code) -> str:
        from PySide6.QtWidgets import QApplication
        from ...core.intranet import pickup_note
        text = pickup_note(self.request_info, code)
        QApplication.clipboard().setText(text)
        self.statusBar().showMessage(f"Recado da solicitação {code} copiado. Cole no canal de contato com o aluno.",
                                     8000)
        return text

    def open_request_in_intranet(self, code):
        """Abre a janela da intranet na solicitação para o técnico mudar o status à mão."""
        from ..intranet import IntranetDialog, webengine_available
        ok, _err = webengine_available()
        if not ok:
            self.statusBar().showMessage("O navegador embutido não está disponível nesta instalação.", 8000)
            return
        dlg = getattr(self, "_intranet_dlg", None)
        if dlg is None:
            dlg = IntranetDialog(self)
            self._intranet_dlg = dlg
        dlg.open_request(code)
        self.open_intranet()

    def show_ready_menu(self):
        """Menu com as solicitações prontas: copiar o recado de retirada ou abrir na intranet."""
        from PySide6.QtGui import QCursor
        from PySide6.QtWidgets import QMenu
        codes = self.ready_requests()
        if not codes:
            self.statusBar().showMessage("Nenhuma solicitação pronta ainda: marque as peças feitas ou as placas "
                                         "cortadas.", 6000)
            return None
        menu = QMenu(self)
        for code in codes:
            sub = menu.addMenu(f"Pronta ✓  {code}")
            for text, fn in self.request_ready_actions(code):
                sub.addAction(text, fn)
        self._ready_menu = menu
        menu.popup(QCursor.pos())
        return menu

    def _announce_ready(self, before: set):
        new = [c for c in self.ready_requests() if c not in before]
        if new:
            self.statusBar().showMessage(
                ("Solicitações " if len(new) > 1 else "Solicitação ") + ", ".join(new)
                + (" prontas ✓" if len(new) > 1 else " pronta ✓")
                + " · Arquivo › “Solicitações prontas para retirada…” copia o recado e abre a página na intranet "
                "para mudar o status.", 12000)
