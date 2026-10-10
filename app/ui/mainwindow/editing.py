"""Ajuste manual: arrastar, girar, travar, remover, desfazer e quantidades."""
from __future__ import annotations

import copy

from PySide6.QtWidgets import QInputDialog, QMenu

from ...core.models import Placement
from ..canvas import sheet_offset
from ..owners import sheet_numbers
from .common import MAX_UNDO


class EditingMixin:
    def select_instance(self, key):
        self.tabs.setCurrentIndex(1)
        self.canvas.scene().clearSelection()
        for item in self.canvas.part_items:
            if (item.placement.part_id, item.placement.instance) == tuple(key):
                item.setSelected(True)
                self.canvas.ensureVisible(item)

    def edit_positions(self):
        if self.worker is not None or not self.placements:
            return
        from ..position_dialog import PositionDialog
        self.tabs.setCurrentIndex(1)
        dialog = PositionDialog(self)
        dialog.selected.connect(self.select_instance)
        dialog.applied.connect(self.apply_instance_position)
        dialog.load(dialog.list.currentRow())
        dialog.exec()

    def apply_instance_position(self, key, x, y, rotation, target):
        if self.worker is not None:
            return
        pl = next((p for p in self.placements if (p.part_id, p.instance) == tuple(key)), None)
        if pl is None:
            return
        if self.pmap[pl.part_id].rotation_locked:
            rotation = pl.rotation
        target_mat = self.sheet_index().material.get(target)
        if target_mat and target_mat != self.pmap[pl.part_id].material:
            return
        if (pl.x, pl.y, pl.rotation, pl.sheet_index) == (x, y, rotation, target):
            return
        self._push_undo()
        self._invalidate_cut_for([pl] + [p for p in self.placements if p.sheet_index == target])
        pl.x, pl.y, pl.rotation, pl.sheet_index = x, y, rotation, target
        self._compact_sheets()
        self.mark_changed()
        self._redraw(keep_view=True)
        self.select_instance(key)
        self._update_status()

    def _invalidate_cut_for(self, placements):
        changed = {pl.sheet_index for pl in placements} & self.cut_sheets
        if not changed:
            return
        self.cut_sheets.difference_update(changed)
        self.done_parts.difference_update(pl.part_id for pl in self.placements if pl.sheet_index in changed)
        self.canvas.set_cut(self.cut_sheets)
        self._refresh_cut_panel()

    def _snapshot(self):
        return (copy.deepcopy(self.placements), self.n_sheets, {p.id: p.quantity for p in self.parts},
                list(self.unplaced), set(self.cut_sheets), set(self.done_parts),
                {p.id: p.rotation_locked for p in self.parts})

    def _restore(self, snap):
        pls, n, qty, unplaced = snap[:4]
        if len(snap) > 4:
            self.cut_sheets = set(snap[4])
        if len(snap) > 6:
            self.done_parts = set(snap[5])
            for p in self.parts:
                p.rotation_locked = snap[6].get(p.id, False)
        self.placements = copy.deepcopy(pls)
        self.n_sheets = n
        self.unplaced = list(unplaced)
        for p in self.parts:
            if p.id in qty:
                p.quantity = qty[p.id]
                self.parts_panel.set_quantity(p.id, p.quantity)
        self.parts_panel.update_summary()
        self._rebuild_checker()
        self.parts_panel.set_parts(self.parts, self.too_big)
        if self.placements and self.tabs.currentIndex() != 1:
            self.tabs.setCurrentIndex(1)
        else:
            self._redraw(keep_view=True)
        self._update_status()
        self._update_buttons()

    def _push_undo(self):
        self.undo_stack.append(self._snapshot())
        if len(self.undo_stack) > MAX_UNDO:
            self.undo_stack.pop(0)
        self.redo_stack.clear()
        self._update_buttons()

    def undo(self):
        if self.worker is not None or not self.undo_stack:
            return
        self.redo_stack.append(self._snapshot())
        self._restore(self.undo_stack.pop())
        self.mark_changed()

    def redo(self):
        if self.worker is not None or not self.redo_stack:
            return
        self.undo_stack.append(self._snapshot())
        self._restore(self.redo_stack.pop())
        self.mark_changed()

    def _check_drag_collisions(self):
        if not self.checker:
            return
        pls = []
        involved: set[int] = set()
        for it in self.canvas.part_items:
            pl = it.placement
            if it.isSelected():
                involved.add(pl.sheet_index)
                pl = copy.copy(pl)
                s = min(self.canvas.sheet_at(it.pos().x()), max(self.n_sheets, 1))   # igual ao soltar
                pl.sheet_index = s
                pl.x = it.pos().x() - sheet_offset(self.canvas.params, s)
                pl.y = it.pos().y()
                involved.add(s)
            pls.append(pl)
        bad = self.checker.colliding(pls, involved, self.sheet_remnants)   # só as placas envolvidas no arraste
        for i, it in enumerate(self.canvas.part_items):
            if pls[i].sheet_index not in involved:
                continue
            c = i in bad
            if c != it.colliding:
                it.colliding = c
                it.update()

    def on_items_released(self):
        moved = []
        for it in self.canvas.selected_items():
            s = self.canvas.sheet_at(it.pos().x())
            s = min(s, max(self.n_sheets, 1))  # no máximo uma placa nova
            x = it.pos().x() - sheet_offset(self.canvas.params, s)
            y = it.pos().y()
            pl = it.placement
            if s != pl.sheet_index or abs(x - pl.x) > 1e-6 or abs(y - pl.y) > 1e-6:
                moved.append((it, s, x, y))
        if not moved:
            return
        self._push_undo()
        grow = False
        self._invalidate_cut_for([it.placement for it, *_ in moved])
        for it, s, x, y in moved:
            it.placement.sheet_index = s
            it.placement.x = x
            it.placement.y = y
            if s >= self.n_sheets:
                grow = True
        self._compact_sheets()
        self.mark_changed()
        self.schedule_autosave()
        if grow or any(it.placement.sheet_index != s for it, s, _, _ in moved):
            self._redraw(keep_view=True)
        else:
            for it, *_ in moved:
                it.sync_from_placement()
            self._mark_collisions()
        self._update_status()

    def _compact_sheets(self):
        """Remove placas vazias e renumera na ordem por material (a mesma do arquivo 'todas as placas',
        do relatório e do checklist), levando junto as marcações de 'cortada'."""
        nums = sheet_numbers(self.pmap, self.placements)          # índice -> nº (1..N) por material
        remap = {s: n - 1 for s, n in nums.items()}
        for pl in self.placements:
            pl.sheet_index = remap.get(pl.sheet_index, pl.sheet_index)
        self.cut_sheets = {remap[s] for s in self.cut_sheets if s in remap}
        self.sheet_remnants = {remap[s]: r for s, r in self.sheet_remnants.items() if s in remap}
        self.sheet_files = {remap[s]: f for s, f in self.sheet_files.items() if s in remap}
        self.sheet_stamps = {remap[s]: t for s, t in self.sheet_stamps.items() if s in remap}
        self.saved_leftover = {remap[s]: a for s, a in self.saved_leftover.items() if s in remap}
        self.n_sheets = len(nums)

    def _selected_placements(self) -> list[Placement]:
        if self.worker is not None or self.canvas.mode != "layout":
            return []
        return [it.placement for it in self.canvas.selected_items()]

    def rotate_selected(self):
        sel = self._selected_placements()
        if not sel:
            return
        p = self.nest_params()
        self._push_undo()
        self._invalidate_cut_for(sel)
        for pl in sel:
            if self.pmap[pl.part_id].rotation_locked:
                self.statusBar().showMessage("Esta peça está com rotação travada.", 4000)
                continue
            # próxima rotação permitida (material com veio: só 0° e 180°)
            rots = sorted(p.rotations(self.pmap[pl.part_id].material or ""))
            cur = round(pl.rotation % 360.0, 4)
            nxt = next((r for r in rots if r > cur + 1e-6), rots[0])
            if len(rots) == 1 and abs(rots[0] - cur) < 1e-6:
                self.statusBar().showMessage("Este material só permite esta rotação.", 4000)
                continue
            pl.rotation = round(nxt % 360.0, 4)
        self.mark_changed()
        for it in self.canvas.selected_items():
            it.sync_from_placement()
        self._mark_collisions()
        self._update_status()

    def mirror_selected(self):
        sel = self._selected_placements()
        if not sel:
            return
        if not self.settings_panel.params().allow_mirror:
            self.statusBar().showMessage("Ative “Permitir espelhar peças” nos parâmetros para espelhar.", 5000)
            return
        self._push_undo()
        for pl in sel:
            pl.mirrored = not pl.mirrored
        self._invalidate_cut_for(sel)
        self.mark_changed()
        self._redraw(keep_view=True)

    def toggle_lock_selected(self):
        sel = self._selected_placements()
        if not sel:
            return
        self._push_undo()
        new = not all(pl.locked for pl in sel)
        for pl in sel:
            pl.locked = new
        self.mark_changed()
        for it in self.canvas.selected_items():
            it.update()
        self.statusBar().showMessage(
            "Peça(s) travada(s): ficarão no lugar no próximo encaixe." if new else "Peça(s) destravada(s).", 5000)

    def delete_selected(self):
        sel = self._selected_placements()
        if not sel:
            return
        self._push_undo()
        self._invalidate_cut_for(sel)
        for pl in sorted(sel, key=lambda q: -q.instance):
            self.placements.remove(pl)
            part = self.pmap[pl.part_id]
            part.quantity = max(0, part.quantity - 1)
            for other in self.placements:
                if other.part_id == pl.part_id and other.instance > pl.instance:
                    other.instance -= 1
            self.parts_panel.set_quantity(part.id, part.quantity)
        self._compact_sheets()
        self.mark_changed()
        self._redraw(keep_view=True)
        self.parts_panel.update_summary()
        self.reconcile_state()
        self._update_status()

    def select_all(self):
        for it in self.canvas.part_items:
            it.setSelected(True)

    def move_selected_to_sheet(self, target: int):
        sel = self._selected_placements()
        if not sel:
            return
        self._push_undo()
        self._invalidate_cut_for(sel + [pl for pl in self.placements if pl.sheet_index == target])
        for pl in sel:
            pl.sheet_index = target
        self._compact_sheets()
        self.mark_changed()
        self._redraw(keep_view=True)
        self._update_status()

    def show_item_menu(self, items, pos):
        if self.worker is not None:
            return
        m = QMenu(self)
        m.addAction("Girar (R)", self.rotate_selected)
        a = m.addAction("Espelhar (M)", self.mirror_selected)
        a.setEnabled(self.settings_panel.params().allow_mirror)
        locked = all(it.placement.locked for it in items)
        m.addAction("Destravar posição (L)" if locked else "Travar posição (L)", self.toggle_lock_selected)
        sub = m.addMenu("Mover para placa")
        idx = self.sheet_index()
        cur = {it.placement.sheet_index for it in items}
        sel_mats = {it.part.material for it in items}
        for i in range(max(1, self.n_sheets)):
            sm = idx.material.get(i, "")
            act = sub.addAction(f"Placa {idx.number.get(i, i + 1)}" + (f" · {sm}" if sm else ""),
                                lambda i=i: self.move_selected_to_sheet(i))
            act.setEnabled(cur != {i} and (not sm or sel_mats <= {sm}))
        sub.addAction("Nova placa", lambda: self.move_selected_to_sheet(self.n_sheets))
        m.addSeparator()
        m.addAction("Remover (Del)", self.delete_selected)
        m.exec(pos)

    def on_quantity_changed(self, pid: str, q: int):
        part = self.pmap.get(pid)
        if part is None:
            return
        self._push_undo()
        part.quantity = q
        before = len(self.placements)
        self.placements = [pl for pl in self.placements if not (pl.part_id == pid and pl.instance >= q)]
        self.mark_changed()
        self.done_parts.discard(pid)
        self.reconcile_state()
        if len(self.placements) != before:
            self._redraw(keep_view=True)
        self.parts_panel.update_summary()
        self._update_status()

    def on_rotation_lock_changed(self, pid: str, locked: bool):
        part = self.pmap.get(pid)
        if part:
            self._push_undo()
            part.rotation_locked = locked
            self._rebuild_checker()
            self.mark_changed()

    def multiply_kits(self):
        if not self.parts:
            return
        n, ok = QInputDialog.getInt(self, "Quantidade de kits",
                                    "Quantos kits completos? (as quantidades do arquivo serão multiplicadas)",
                                    1, 1, 999)
        if ok:
            self._push_undo()
            for p in self.parts:
                p.quantity = p.file_quantity * n
                self.parts_panel.set_quantity(p.id, p.quantity)
            self.reconcile_state()
            self._redraw(keep_view=True)
            self.parts_panel.update_summary()
            self._update_status()
            self.mark_changed()

    def reset_quantities(self):
        self._push_undo()
        self.mark_changed()
        for p in self.parts:
            p.quantity = p.file_quantity
            self.parts_panel.set_quantity(p.id, p.quantity)
        self.placements = [pl for pl in self.placements if pl.instance < self.pmap[pl.part_id].quantity]
        self.reconcile_state()
        self._redraw(keep_view=True)
        self.parts_panel.update_summary()
        self._update_status()

    def on_part_selected(self, pid: str):
        if self.canvas.mode != "layout":
            return
        self.canvas.scene().blockSignals(True)
        self.canvas.scene().clearSelection()
        for it in self.canvas.part_items:
            if it.part.id == pid:
                it.setSelected(True)
        self.canvas.scene().blockSignals(False)
        self._canvas_selection()

    def _canvas_selection(self):
        sel = self.canvas.selected_items()
        if len(sel) == 1:
            item = sel[0]
            self.parts_panel.select_part(item.part.id)
            width, height = item.part.size
            text = (f"{item.part.name} · cópia {item.placement.instance + 1} · "
                    f"{width:g} × {height:g} mm · {item.part.material or 'sem material'}")
            if item.colliding:
                text += " · Atenção: colisão, borda ou material incompatível"
        elif sel:
            text = f"{len(sel)} cópias selecionadas · R girar · L travar · Del remover"
        else:
            text = "Selecione uma peça para conferir seus detalhes"
        self.selection_summary.setText(text)
        from ..accessibility import announce
        announce(self.selection_summary, text)
