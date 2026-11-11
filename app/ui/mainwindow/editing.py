"""Ajuste manual: arrastar, girar, travar, remover, desfazer e quantidades."""
from __future__ import annotations

import copy

from PySide6.QtWidgets import QInputDialog, QMenu

from ...core.models import Placement
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
                {p.id: p.rotation_locked for p in self.parts},
                {p.id: (p.tabs, p.kerf_comp) for p in self.parts})

    def _restore(self, snap):
        pls, n, qty, unplaced = snap[:4]
        if len(snap) > 4:
            self.cut_sheets = set(snap[4])
        if len(snap) > 6:
            self.done_parts = set(snap[5])
            for p in self.parts:
                p.rotation_locked = snap[6].get(p.id, False)
        if len(snap) > 7:
            for p in self.parts:
                p.tabs, p.kerf_comp = snap[7].get(p.id, (0, False))
                self.parts_panel.set_cut_options(p.id, p.tabs, p.kerf_comp)
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
                s, pl.x, pl.y = self._drop_target(it)                      # igual ao soltar
                pl.sheet_index = s
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

    def _drop_target(self, it) -> tuple[int, float, float]:
        """(placa, x, y) de uma peça arrastada: a vaga da grade sob ela (no máximo uma placa nova) e a
        posição relativa a essa placa."""
        pos = it.pos()
        s = min(self.canvas.sheet_at(pos.x(), pos.y()), max(self.n_sheets, 1))
        ox, oy = self.canvas.sheet_origin(s)
        return s, pos.x() - ox, pos.y() - oy

    def on_items_released(self):
        moved = []
        for it in self.canvas.selected_items():
            s, x, y = self._drop_target(it)
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

    # ------------------------------------------------------------------ mover pelo teclado
    NUDGE_SEQ_MS = 1200        # toques seguidos dentro deste intervalo = um passo só de desfazer

    def nudge_selected(self, dx: float, dy: float, snap: bool = False):
        """Setas: move a seleção dx/dy mm na própria placa. Com ``snap`` (Ctrl), (dx, dy) é só a direção e
        a seleção desliza até encostar na vizinha ou na margem, respeitando o espaçamento. Peças travadas
        não se movem; colisão deixa a peça vermelha (mesma checagem do arraste)."""
        if self.worker is not None or self.canvas.mode != "layout":
            return
        items = [it for it in self.canvas.selected_items() if not it.placement.locked]
        if not items:
            if self.canvas.selected_items():
                self.statusBar().showMessage("Peça travada: destrave com L para mover.", 4000)
            return
        if snap:
            dist = self._snap_distance([it.placement for it in items], dx, dy)
            if dist is None:
                self.statusBar().showMessage("Para encostar, a peça precisa estar sem colisão.", 4000)
                return
            if dist < 0.005:
                self.statusBar().showMessage("Já está encostada nessa direção.", 3000)
                return
            dx, dy = dx * dist, dy * dist
        self._nudge_undo_step(items)
        self._invalidate_cut_for([it.placement for it in items])
        for it in items:
            it.placement.x = round(it.placement.x + dx, 4)
            it.placement.y = round(it.placement.y + dy, 4)
            it.sync_from_placement()
        self.mark_changed()
        self.schedule_autosave()
        self._mark_collisions()
        self._canvas_selection()
        if snap:
            self.statusBar().showMessage(f"Encostada: andou {abs(dx) + abs(dy):.2f} mm.".replace(".", ","), 3000)

    def _nudge_undo_step(self, items):
        """Um passo de desfazer por sequência de toques (mesma seleção, sem pausa longa nem outra ação)."""
        from PySide6.QtCore import QTimer
        keys = frozenset((it.placement.part_id, it.placement.instance) for it in items)
        timer = getattr(self, "_nudge_timer", None)
        if timer is None:
            timer = self._nudge_timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(self.NUDGE_SEQ_MS)
        same = (timer.isActive() and getattr(self, "_nudge_keys", None) == keys and self.undo_stack
                and id(self.undo_stack[-1]) == getattr(self, "_nudge_snap", None))
        if not same:
            self._push_undo()
            self._nudge_snap = id(self.undo_stack[-1])
            self._nudge_keys = keys
        timer.start()

    def _snap_distance(self, moving: list[Placement], ux: float, uy: float):
        """Quanto a seleção pode andar na direção (ux, uy) até encostar (mm), ou None se já colide.
        Avança com a distância livre (shapely) e fecha o contato por busca binária no verificador."""
        if not self.checker:
            return None
        from shapely import affinity
        from shapely.geometry import box
        from shapely.strtree import STRtree
        from ...core.collision import TOL_AREA
        chk = self.checker
        idx = self.sheet_index()
        keys = {(pl.part_id, pl.instance) for pl in moving}
        groups: dict[int, list] = {}
        for pl in moving:
            groups.setdefault(pl.sheet_index, []).append(pl)
        worlds = []
        for si, pls in groups.items():
            mat = idx.material.get(si) or self.pmap[pls[0].part_id].material or ""
            usable = chk.usable_for(mat, self.sheet_remnants.get(si, ""))
            mine = [affinity.translate(chk.solid(p.part_id, p.rotation, p.mirrored), p.x, p.y) for p in pls]
            others = [chk.placed(p) for p in self.placements
                      if p.sheet_index == si and (p.part_id, p.instance) not in keys]
            worlds.append((usable, mine, others, STRtree(others) if others else None))

        def free(d: float):
            """Folga mínima depois de andar d mm (< 0 = colide ou sai da área útil)."""
            gap = float("inf")
            for usable, mine, others, tree in worlds:
                for g in mine:
                    m = affinity.translate(g, ux * d, uy * d)
                    if not usable.covers(m):
                        return -1.0
                    gap = min(gap, m.distance(usable.boundary))
                    if tree is not None:
                        x0, y0, x1, y1 = m.bounds
                        for j in tree.query(box(x0 - gap, y0 - gap, x1 + gap, y1 + gap)):
                            o = others[int(j)]
                            if m.intersection(o).area > TOL_AREA:
                                return -1.0
                            gap = min(gap, m.distance(o))
            return gap

        if free(0.0) < 0:
            return None
        lo, step_min = 0.0, 0.25
        # avanço conservador: anda a folga livre (nunca atravessa uma vizinha) ou, rente a uma peça que fica
        # para trás/ao lado, um passo mínimo de 0,25 mm (as peças com folga têm vários mm: não dá para pular)
        for _ in range(4000):
            g = free(lo)
            hi = lo + max(g, step_min)
            if free(hi) < 0:
                break                                 # contato entre lo (livre) e hi (colide)
            lo = hi
        else:
            return lo
        for _ in range(30):                           # busca binária até 0,005 mm
            if hi - lo < 0.005:
                break
            mid = (lo + hi) / 2
            if free(mid) < 0:
                hi = mid
            else:
                lo = mid
        return lo

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

    def on_part_tabs_changed(self, pid: str, n: int):
        """Micro-pontes da peça (0 = sem). Só a exportação muda: o encaixe continua o mesmo, mas o arquivo
        já exportado das placas com essa peça deixa de valer (a impressão digital inclui as pontes)."""
        part = self.pmap.get(pid)
        if part and part.tabs != int(n):
            self._push_undo()
            part.tabs = int(n)
            self.mark_changed()
            self._update_status()

    def on_part_kerf_changed(self, pid: str, on: bool):
        """Compensar o kerf desta peça na exportação (corte externo + kerf/2, furos − kerf/2)."""
        part = self.pmap.get(pid)
        if part and part.kerf_comp != bool(on):
            self._push_undo()
            part.kerf_comp = bool(on)
            self.mark_changed()
            if on:
                from ...core.dxf_export import part_kerf
                k = part_kerf(part, self.nest_params(), self._material_kerf())
                if k <= 0:
                    self.show_banner(f"<b>{part.name}</b>: o material “{part.material or 'sem material'}” não tem "
                                     "kerf cadastrado no banco de materiais (Ctrl+M). Sem ele a peça sai sem "
                                     "compensação.", "warn")
            self._update_status()

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
            text = f"{len(sel)} cópias selecionadas · setas movem · R girar · L travar · Del remover"
        else:
            from .ui_build import SELECTION_HINT
            text = SELECTION_HINT
        hint = not sel
        if self.selection_summary.property("hint") != hint:
            self.selection_summary.setProperty("hint", hint)
            self.selection_summary.style().unpolish(self.selection_summary)
            self.selection_summary.style().polish(self.selection_summary)
        self.selection_summary.setText(text)
        from ..accessibility import announce
        announce(self.selection_summary, text)
