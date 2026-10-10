"""Banco de materiais: parâmetros de laser e de chapa iguais em todos os PCs do laboratório."""
from __future__ import annotations

import json
import os

from PySide6.QtWidgets import QMessageBox

from ...core import material_db
from ..dialogs import settings

NO_MATERIAL = "Sem material"      # peças sem material informado também têm parâmetros de laser


class MaterialsMixin:
    def material_db_path(self) -> str:
        """Arquivo do banco: o escolhido (pasta compartilhada do laboratório) ou Documentos/Sindri."""
        p = str(settings().value("materials/db_path", "") or "")
        if p and os.path.isdir(p):
            p = os.path.join(p, material_db.FILE_NAME)
        return p or material_db.default_path()

    def set_material_db_path(self, path: str):
        settings().setValue("materials/db_path", path or "")
        self.__dict__.pop("_mdb", None)
        self._refresh_laser_panel(force=True)

    def material_db(self) -> material_db.MaterialDB:
        """Banco atual (relido quando o arquivo muda — outro PC pode ter salvo). Se o arquivo estiver
        estragado, usa os pontos de partida só em memória e avisa uma vez (o arquivo não é sobrescrito)."""
        path = self.material_db_path()
        db = self.__dict__.get("_mdb")
        if db is not None and db.path == path and getattr(self, "_mdb_dirty", False):
            return db                                  # mudanças ainda não gravadas: não relê por cima
        try:
            mtime = os.path.getmtime(path) if os.path.isfile(path) else 0.0
        except OSError:
            mtime = 0.0
        if db is not None and db.path == path and (db.mtime == mtime or getattr(db, "_broken", False)):
            return db
        try:
            db = material_db.MaterialDB.load(path, migrate=self._legacy_material_cfg)
        except material_db.MaterialDBError as e:
            db = material_db.MaterialDB(path, material_db.seeds())
            db._broken = True
            if not getattr(self, "_mdb_warned", False):
                self._mdb_warned = True
                self.show_banner(f"⚠ Banco de materiais: {e}. Usando valores de partida só nesta sessão "
                                 "(o arquivo não foi alterado).", "warn")
        self.__dict__["_mdb"] = db
        return db

    @staticmethod
    def _legacy_material_cfg() -> dict:
        """Valores que ficavam só neste PC (antes do banco): migrados na criação do banco."""
        try:
            v = json.loads(settings().value("laser/materials", "") or "null")
        except ValueError:
            v = None
        return v if isinstance(v, dict) else {}

    def _db_change(self, material: str, change, now: bool = False) -> bool:
        """Muda um material na memória na hora (o painel e a exportação já usam) e grava no arquivo
        logo depois (digitar no painel não grava a cada tecla)."""
        db = self.material_db()
        if getattr(db, "_broken", False):
            QMessageBox.warning(self, "Banco de materiais",
                                "O banco de materiais está com problema e não pode ser alterado agora.\n"
                                f"Arquivo: {db.path}")
            return False
        try:
            name = material.strip() or NO_MATERIAL
            m = db.find(name) or db.add(material_db.Material(name))
            change(m)
            m.validate()
        except material_db.MaterialDBError as e:
            QMessageBox.warning(self, "Banco de materiais", str(e))
            return False
        self._mdb_dirty = True
        if now:
            return self.flush_material_db()
        if not hasattr(self, "_mdb_timer"):
            from PySide6.QtCore import QTimer
            self._mdb_timer = QTimer(self)
            self._mdb_timer.setSingleShot(True)
            self._mdb_timer.timeout.connect(self.flush_material_db)
        self._mdb_timer.start(700)
        return True

    def flush_material_db(self) -> bool:
        """Grava as mudanças pendentes (junta com materiais que outro PC criou nesse meio tempo)."""
        if not getattr(self, "_mdb_dirty", False):
            return True
        db = self.__dict__.get("_mdb")
        if db is None:
            return True
        try:
            db.save()
        except (OSError, material_db.MaterialDBError) as e:
            self.statusBar().showMessage(f"Não consegui salvar o banco de materiais: {e}", 15000)
            return False
        self._mdb_dirty = False
        return True

    def set_material_value(self, material: str, data: dict):
        """Linha do painel do laser: cor/velocidade/potência do corte, ou de vinco/gravação (``op``)."""
        op = data.get("op") or "corte"
        before = self._material_colors().get(material)
        old_op_color = int(((self.material_cfg().get(material) or {}).get("ops") or {}).get(op, {}).get("color") or 0)
        vals = {}
        if "speed" in data:
            vals["speed"] = round(float(data.get("speed") or 0), 2)
        if "power" in data:
            vals["power"] = round(float(data.get("power") or 0), 1)
        if "color" in data:
            vals["color"] = int(data["color"] or 0)
        self._db_change(material, lambda m: m.set_laser(op, vals))
        if op == "corte" and "color" in data and int(data["color"]) != before:
            self._refresh_laser_panel(force=True)    # outra cor: os outros materiais podem mudar
        elif op != "corte" and "color" in data and int(data["color"]) != old_op_color:
            self._refresh_laser_panel(force=True)

    def set_material_params(self, material: str, op: str, values: dict):
        """Parâmetros completos de uma camada (⋯ do painel)."""
        self._db_change(material, lambda m: m.set_laser(op, dict(values)), now=True)

    def material_cfg(self) -> dict:
        """{material: {"color", "speed", "power", …, "ops": {…}, "forbidden"}} — do banco de materiais.
        Também responde pelo nome como está nas peças ("mdf 3 mm" acha "MDF 3mm")."""
        db = self.material_db()
        out = db.laser_cfg()
        for pt in getattr(self, "parts", []):
            m = pt.material or ""
            if m not in out:
                found = db.find(m or NO_MATERIAL)
                if found is not None:
                    out[m] = found.laser_cfg()
        for name, c in list(out.items()):
            mat = db.find(name)
            if mat is not None:
                c["tested"] = mat.tested
                c["test_age"] = mat.test_age_days()
                c["stale"] = mat.is_stale()
        return out

    def material_sheets(self) -> dict:
        """Chapa/margem/espaçamento/veio por material (só os materiais em uso que o banco define)."""
        mats = {pt.material for pt in getattr(self, "parts", []) if pt.quantity > 0 and pt.material}
        return self.material_db().nest_overrides(sorted(mats))

    def open_materials_dialog(self, select: str = ""):
        from ..materials_dialog import MaterialsDialog
        dlg = MaterialsDialog(self, select)
        dlg.exec()
        self.__dict__.pop("_mdb", None)
        self._refresh_laser_panel(force=True)
        if self.parts:
            self.on_params_changed()

    # ------------------------------------------------------------------ retalhos
    def remnant_store(self):
        """Retalhos (mesma pasta do banco de materiais: compartilhados entre os PCs se ele estiver na rede)."""
        from ...core.remnants import RemnantError, RemnantStore, default_path
        path = default_path(self.material_db_path())
        try:
            return RemnantStore.load(path)
        except RemnantError as e:
            if not getattr(self, "_rem_warned", False):
                self._rem_warned = True
                self.show_banner(f"⚠ Retalhos: {e}", "warn")
            return RemnantStore(path, [])

    def available_remnants(self) -> list[dict]:
        """Retalhos disponíveis dos materiais em uso, com o material escrito como nas peças (o encaixe
        compara o nome exato)."""
        from ...core.material_db import key
        mats = {}
        for pt in getattr(self, "parts", []):
            if pt.quantity > 0 and pt.material:
                mats.setdefault(key(pt.material), pt.material)
        if not mats:
            return []
        out = []
        for r in self.remnant_store().available(mats.values()):
            d = r.to_params()
            d["material"] = mats.get(key(r.material), r.material)
            out.append(d)
        return out

    def open_remnants_dialog(self):
        from ..remnants_dialog import RemnantsDialog
        RemnantsDialog(self).exec()
        if self.placements:
            self._redraw(keep_view=True)
        self._update_status()

    def _remnant_on_cut(self, si: int, on: bool):
        """Placa que era retalho: cortada = retalho usado (sai da lista). E oferece guardar a sobra."""
        rid = self.sheet_remnants.get(si)
        if rid:
            try:
                self.remnant_store().mark_used(rid, on)
            except OSError as e:
                self.statusBar().showMessage(f"Não consegui atualizar os retalhos: {e}", 10000)
        if on:
            n = self.sheet_index().number.get(si, si + 1)
            self.show_banner(f"Placa {n} cortada. <a href=\"leftover:{si}\">Guardar o que sobrou como retalho</a>"
                             " (para o próximo encaixe usar antes de abrir chapa nova).", "info")

    def save_leftover(self, si: int) -> list:
        """Sobra da placa ``si`` (placa menos as peças + espaçamento) vira retalho(s) do material dela."""
        from ...core.remnants import leftover, make_remnant
        from ...core.validate import fine_solid, placed_geometry
        specs = self.sheet_specs()
        spec = specs.get(si)
        if spec is None:
            return []
        mat = self.sheet_index().material.get(si, "")
        used = [placed_geometry(fine_solid(self.pmap[pl.part_id]), pl)
                for pl in self.placements if pl.sheet_index == si and pl.part_id in self.pmap]
        polys = leftover(spec.shape(), used, spec.spacing)
        n = self.sheet_index().number.get(si, si + 1)
        store = self.remnant_store()
        added = []
        for k, poly in enumerate(polys):
            label = f"Sobra da placa {n}" + (f" ({k + 1})" if len(polys) > 1 else "")
            r = make_remnant(mat, poly, label, f"sobra da placa {n}" + (f" · {self.request_label}"
                                                                       if self.request_label else ""))
            store.add(r)
            added.append(r)
        if added:
            self.saved_leftover[si] = self.saved_leftover.get(si, 0.0) + sum(r.area for r in added)
            self.mark_changed()
            if getattr(self, "last_export_base", ""):      # o CSV do mês conta a sobra como não-perda
                plan = self._laser_export_plan()
                self._write_management_csv(self._request_costs(self._sheet_times(plan)))
            sizes = ", ".join(f"{r.size[0]:.0f}×{r.size[1]:.0f} mm" for r in added)
            self.show_banner(f"{len(added)} retalho(s) de {mat or 'sem material'} guardado(s): {sizes}. "
                             "O próximo encaixe deste material usa antes de abrir chapa nova.", "ok")
        else:
            self.show_banner("A sobra desta placa é pequena demais para valer guardar.", "info")
        return added
