"""Abrir arquivos (DXF, intranet), placas pré-definidas e limpeza de arquivos."""
from __future__ import annotations

import json
import os
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from ...core.part_builder import import_files
from ..dialogs import CleanupDialog, PresetsDialog, load_presets, save_presets, settings
from ..prefs import get_list_value
from ..render import clear_graphics_cache
from .common import APP_NAME


class FilesMixin:
    def _refresh_presets(self):
        self.presets = load_presets()
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        for p in self.presets:
            size = f"{p['w']:g}×{p['h']:g}"
            self.preset_combo.addItem(p["name"] if size in p["name"] else f"{p['name']}  ({size})", p)
        self.preset_combo.addItem("Personalizada…", None)
        self.preset_combo.blockSignals(False)
        self._sync_preset_combo()

    def _sync_preset_combo(self):
        p = self.settings_panel.params()
        idx = -1
        for i, pr in enumerate(self.presets):
            if abs(pr["w"] - p.sheet_width) < 1e-6 and abs(pr["h"] - p.sheet_height) < 1e-6:
                idx = i
                if self.preset_combo.currentIndex() == i:
                    break
        last = self.preset_combo.count() - 1
        self.preset_combo.blockSignals(True)
        if idx >= 0:
            if self.preset_combo.currentIndex() < 0 or self.preset_combo.currentData() is None or \
                    abs(self.preset_combo.currentData()["w"] - p.sheet_width) > 1e-6 or \
                    abs(self.preset_combo.currentData()["h"] - p.sheet_height) > 1e-6:
                self.preset_combo.setCurrentIndex(idx)
        else:
            self.preset_combo.setItemText(last, f"Personalizada ({p.sheet_width:g}×{p.sheet_height:g})")
            self.preset_combo.setCurrentIndex(last)
        self.preset_combo.blockSignals(False)

    def _preset_chosen(self, i: int):
        data = self.preset_combo.itemData(i)
        if data is None:
            self.settings_panel.w.setFocus()
            self.settings_panel.w.selectAll()
            self.statusBar().showMessage("Digite a largura e a altura da placa no painel à direita.", 5000)
            return
        self.settings_panel.w.blockSignals(True)
        self.settings_panel.w.setValue(data["w"])
        self.settings_panel.w.blockSignals(False)
        self.settings_panel.h.setValue(data["h"])
        self.on_params_changed()
        mats = {p.material for p in self.parts if p.material}
        if len(mats) >= 1:                       # lembra a placa usada para este(s) material(is)
            st = settings()
            try:
                m = json.loads(st.value("presets/by_material", "{}") or "{}")
            except ValueError:
                m = {}
            m[" + ".join(sorted(mats))] = data["name"]
            st.setValue("presets/by_material", json.dumps(m))

    def _apply_material_preset(self):
        """Escolhe sozinho a placa usada da última vez para estes materiais."""
        mats = {p.material for p in self.parts if p.material}
        if not mats:
            return
        try:
            m = json.loads(settings().value("presets/by_material", "{}") or "{}")
        except ValueError:
            return
        name = m.get(" + ".join(sorted(mats))) or next((m[k] for k in m if k in mats), None)
        for i, pr in enumerate(self.presets):
            if pr["name"] == name and self.preset_combo.currentIndex() != i:
                self.preset_combo.setCurrentIndex(i)
                self.statusBar().showMessage(f"Placa escolhida pelo material: {name}", 5000)
                break

    def edit_presets(self):
        p = self.settings_panel.params()
        dlg = PresetsDialog(self.presets, p.sheet_width, p.sheet_height, self)
        if dlg.exec():
            save_presets(dlg.presets())
            self._refresh_presets()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            if any(u.toLocalFile().lower().endswith((".dxf", ".dxfnest", ".sindri")) for u in e.mimeData().urls()):
                e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls()]
        projs = [p for p in paths if p.lower().endswith((".dxfnest", ".sindri"))]
        dxfs = [p for p in paths if p.lower().endswith(".dxf")]
        if projs:
            self.open_project(projs[0])
        elif dxfs:
            self.load_files(dxfs, add=bool(self.files))

    def open_dxf_dialog(self, add: bool = False):
        start = settings().value("ui/last_dir", os.path.expanduser("~"))
        paths, _ = QFileDialog.getOpenFileNames(self, "Abrir DXF", start, "Desenhos DXF (*.dxf);;Todos (*.*)")
        if paths:
            settings().setValue("ui/last_dir", os.path.dirname(paths[0]))
            self.load_files(paths, add=add)

    def load_files(self, paths: list[str], add: bool = False, multipliers: Optional[dict] = None,
                   request_label: Optional[str] = None, materials: Optional[dict] = None,
                   request_info: Optional[dict] = None, tags: Optional[dict] = None) -> bool:
        if self.worker is not None:
            self.stop_nest(wait=True)
        if not self.confirm_discard(add):
            return False
        old = (dict(self.file_multipliers), dict(self.file_materials), dict(self.file_tags), set(self.cut_sheets),
               set(self.done_parts), self.request_label, self.request_info)
        if not add:
            self.settings_panel.reset_file_options()   # arquivo novo: unidade automática, todas as camadas
            self.file_multipliers = {}
            self.file_materials = {}
            self.file_tags = {}
            self.cut_sheets, self.done_parts = set(), set()
            self.request_label = request_label
            self.request_info = request_info
            self.parts_panel.set_request(request_info)
        if multipliers:
            self.file_multipliers.update(multipliers)
        if materials:
            self.file_materials.update(materials)
        if tags:
            self.file_tags.update(tags)
        files = list(self.files) + [p for p in paths if p not in self.files] if add else list(paths)
        if self._import(files, keep_quantities=add) is False:
            # leitura falhou: volta ao estado anterior (as peças antigas continuam na tela)
            (self.file_multipliers, self.file_materials, self.file_tags, self.cut_sheets, self.done_parts,
             self.request_label, self.request_info) = old
            self.parts_panel.set_request(self.request_info)
            self._refresh_cut_panel()
            return False
        return True

    def _import(self, files: list[str], keep_quantities: bool = False):
        p = self.settings_panel.params()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            rep = import_files(files, p.join_tolerance, p.curve_tolerance, **p.import_kwargs(),
                               multipliers=self.file_multipliers, file_materials=self.file_materials,
                               file_tags=self.file_tags)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Erro ao abrir", f"Não foi possível ler os arquivos.\n\n{e}")
            return False
        finally:
            if QApplication.overrideCursor():
                QApplication.restoreOverrideCursor()
        if files and not rep.files:
            QMessageBox.critical(self, "Erro ao abrir", "Não foi possível ler os arquivos.\n\n"
                                 + "\n".join(rep.warnings[:8]))
            return False
        old_q = {(pt.id, round(pt.area, 1)): pt.quantity for pt in self.parts} if keep_quantities else {}
        clear_graphics_cache()
        self.report = rep
        self.files = rep.files
        self.parts = rep.parts
        for pt in self.parts:
            q = old_q.get((pt.id, round(pt.area, 1)))
            if q is not None:
                pt.quantity = q
        self.pmap = {pt.id: pt for pt in self.parts}
        self.placements = []
        self.n_sheets = 0
        self.unplaced = []
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.dirty = True
        self._rebuild_checker()
        self.parts_panel.set_parts(self.parts, self.too_big)
        self.settings_panel.set_layers(rep.extra.get("layers", {}))
        self.banner.hide()
        self.tabs.blockSignals(True)
        self.tabs.setCurrentIndex(0)
        self.tabs.blockSignals(False)
        self.canvas.show_preview(rep.preview)
        names = ", ".join(os.path.basename(f) for f in self.files)
        self.setWindowTitle(f"{APP_NAME} — {names}" if names else APP_NAME)
        self._refresh_cut_panel()
        self._update_status()
        self._update_buttons()
        if self._ask_fix_units(rep):
            return
        if rep.warnings:
            self._show_warnings(rep.warnings)
        if not self.parts:
            self.statusBar().showMessage("Nenhuma peça encontrada nos arquivos.", 8000)
        else:
            total = sum(p.quantity for p in self.parts)
            self.statusBar().showMessage(
                f"{len(self.parts)} tipo(s) de peça, {total} no total. Escolha a placa e clique em Encaixar.", 10000)

    def _ask_fix_units(self, rep) -> bool:
        """Se o tamanho do desenho for absurdo na unidade declarada, corrige sozinho. True = reimportou."""
        from ...core.dxf_import import UNIT_NAMES
        sus = rep.extra.get("suspicious_units") or []
        if not sus or self.settings_panel.params().units_override >= 0:
            return False
        path, declared, suggested = sus[0]
        self.settings_panel.set_units(suggested)
        self._import(self.files, keep_quantities=False)
        self.show_banner(
            f"O arquivo “{os.path.basename(path)}” declara estar em "
            f"{UNIT_NAMES.get(declared, 'unidade indefinida')}, mas assim as peças ficariam com tamanho "
            f"absurdo. Usei <b>{UNIT_NAMES[suggested]}</b>. Se estiver errado, mude em "
            "Parâmetros › Arquivo DXF › Unidade.", "warn")
        return True

    def reimport(self):
        if self.files:
            self._import(self.files, keep_quantities=True)

    def _show_warnings(self, warnings: list[str]):
        important = [w for w in warnings if "assumindo" in w or "não" in w.lower() or "aberto" in w
                     or "corromp" in w or "ignorad" in w or "duplicad" in w]
        if not important:
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Avisos da importação")
        box.setText("O arquivo foi aberto, mas há pontos de atenção:")
        box.setInformativeText("\n".join("• " + w for w in important[:12]) +
                               ("\n…" if len(important) > 12 else ""))
        box.exec()

    def confirm_discard(self, add: bool = False) -> bool:
        if add or not self.placements or not self.dirty:
            return True
        r = QMessageBox.question(self, "Descartar encaixe?",
                                 "O encaixe atual não foi salvo. Deseja continuar e descartá-lo?",
                                 QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        return r == QMessageBox.Yes

    def clear_all(self, ask: bool = True):
        """Remove todos os arquivos, peças e o encaixe (volta para a tela inicial)."""
        if not self.parts and not self.files:
            return
        if ask:
            r = QMessageBox.question(self, "Limpar tudo",
                                     "Remover todas as peças e o encaixe atual?\n"
                                     "(os arquivos DXF no disco não são alterados)",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r != QMessageBox.Yes:
                return
        if self.worker is not None:
            self.stop_nest(wait=True)
        clear_graphics_cache()
        self.files, self.report, self.parts, self.pmap = [], None, [], {}
        self.file_multipliers, self.request_label = {}, None
        self.file_materials, self.request_info = {}, None
        self.file_tags = {}
        self.parts_panel.set_request(None)
        self.placements, self.n_sheets, self.unplaced = [], 0, []
        self.cut_sheets, self.done_parts = set(), set()
        self._refresh_cut_panel()
        self.too_big = set()
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.checker = None
        self.project_path = None
        self.dirty = False
        self.generation = 0
        self.settings_panel.reset_file_options()
        self.settings_panel.set_layers({})
        self.parts_panel.set_parts([])
        self.banner.hide()
        self.canvas.show_empty_hint()
        self.tabs.blockSignals(True)
        self.tabs.setCurrentIndex(0)
        self.tabs.blockSignals(False)
        self.setWindowTitle(APP_NAME)
        self._update_status()
        self._update_buttons()
        self.statusBar().showMessage("Tudo limpo. Abra ou arraste um novo DXF.", 6000)

    def cleanup_files(self):
        """Apaga (Lixeira) as solicitações baixadas da intranet e os arquivos exportados."""
        from PySide6.QtCore import QFile

        from ...core.cleanup import (
            CUT_PATTERNS,
            REPORT_PATTERNS,
            downloaded_files,
            exported_files,
            human,
            remove_empty_dirs,
            total_size,
        )
        from ...core.intranet import default_base_folder
        st = settings()
        base = st.value("intranet/folder", default_base_folder())
        hist = get_list_value(st.value("export/history", []))
        dirs = [st.value("export/last_dir", "")]        # pasta da última exportação (+ o histórico)
        log = os.path.join(os.path.dirname(base), "intranet_log.txt")
        groups = [
            ("down", "Arquivos das solicitações baixadas da intranet", downloaded_files(base), True),
            ("rep", "Relatórios PDF exportados", exported_files(hist, dirs, REPORT_PATTERNS), True),
            ("cut", "Arquivos de corte exportados (…_todas_placas.dxf)", exported_files(hist, dirs, CUT_PATTERNS),
             False),
        ]
        if os.path.isfile(log):
            groups[0][2].append(log)
        if not any(g[2] for g in groups):
            QMessageBox.information(self, "Limpar arquivos", "Não há arquivos baixados nem relatórios para apagar.")
            return
        dlg = CleanupDialog(groups, self)
        if not dlg.exec():
            return
        files = dlg.chosen()
        loaded = {os.path.abspath(f) for f in self.files}
        if loaded & {os.path.abspath(f) for f in files}:
            if self.worker is not None:
                self.stop_nest(wait=True)
            if not self.confirm_discard():   # oferece salvar o encaixe/checklist antes
                return
            self.dirty = False
            self.clear_all(ask=False)        # as peças abertas vêm de arquivos que serão apagados
        size = total_size(files)
        failed, no_trash = [], []
        for f in files:
            if not QFile.moveToTrash(f):
                no_trash.append(f)
        if no_trash:        # pendrive / rede: sem Lixeira -> só apaga de vez se a pessoa confirmar
            r = QMessageBox.question(self, "Sem Lixeira",
                                     f"{len(no_trash)} arquivo(s) estão num local sem Lixeira (pendrive ou rede).\n"
                                     "Apagar DEFINITIVAMENTE esses arquivos?",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            for f in no_trash:
                if r != QMessageBox.Yes:
                    failed.append(f)
                    continue
                try:
                    os.remove(f)
                except OSError:
                    failed.append(f)
        remove_empty_dirs(base)
        st.setValue("export/history", [h for h in hist if os.path.isfile(h)])
        if failed:
            QMessageBox.warning(self, "Limpar arquivos",
                                f"{len(files) - len(failed)} arquivo(s) apagados. Não consegui apagar "
                                f"{len(failed)} (talvez abertos em outro programa):\n\n" +
                                "\n".join(os.path.basename(f) for f in failed[:15]))
        else:
            self.statusBar().showMessage(f"{len(files)} arquivo(s) ({human(size)}) enviados para a Lixeira.", 8000)

    def open_intranet(self):
        """Abre a intranet, baixa a solicitação escolhida e carrega no encaixe (placas por material)."""
        from ...core.intranet import file_materials, file_multipliers, request_summary
        from ..intranet import ALL_MATERIALS, IntranetDialog, webengine_available
        ok, err = webengine_available()
        if not ok:
            QMessageBox.critical(self, "Intranet", "O navegador embutido não está disponível nesta instalação."
                                 f"\n\n{err}")
            return
        if self.worker is not None:
            self.stop_nest(wait=True)
        dlg = getattr(self, "_intranet_dlg", None)
        if dlg is None:
            dlg = IntranetDialog(self)
            self._intranet_dlg = dlg
        dlg.chosen_material = None
        dlg.batch_result = None
        dlg.failed_files = []
        if not dlg.exec() or not dlg.chosen_material:
            return
        failed = list(getattr(dlg, "failed_files", []) or [])
        if dlg.batch_result:
            self._load_batch(dlg.batch_result, failed)
            self._after_intranet_load()
            return
        if dlg.detail is None:
            return
        d, choice = dlg.detail, dlg.chosen_material
        mats = d.materials()
        chosen = list(mats) if choice == ALL_MATERIALS else [choice]
        files = [f for m in chosen for f in mats.get(m, []) if f.local_path and os.path.isfile(f.local_path)]
        if not files:
            return
        info = request_summary(d, chosen)
        rm = info.get("rm") or ""
        label = f"{d.code}_RM{rm}" if rm else f"{d.code}"
        if not self.load_files([f.local_path for f in files], multipliers=file_multipliers(files),
                               request_label=label, materials=file_materials(files), request_info=info):
            return
        others = [f"{m} ({len(fs)} arquivo(s))" for m, fs in mats.items() if m not in chosen]
        txt = (f"Solicitação <b>{d.code}</b> · RM <b>{rm}</b> · {d.student} — "
               + ", ".join(f"<b>{m}</b>" for m in chosen)
               + ". Quantidades da intranet aplicadas; cada material ganha placas próprias.")
        if others:
            txt += " Não carregado: " + ", ".join(others) + "."
        if failed:
            self.show_banner(txt + f" <b>ATENÇÃO: {len(failed)} arquivo(s) não baixaram:</b> "
                             + ", ".join(failed[:6]) + ("…" if len(failed) > 6 else ""), "warn")
        elif not self.banner.isVisible():
            self.show_banner(txt, "info")
        self.setWindowTitle(f"{APP_NAME} — Solicitação {d.code} · RM {rm}")
        self._after_intranet_load()

    def _after_intranet_load(self):
        """Depois de baixar da intranet: placa do material e (se ligado) já começa a encaixar."""
        if not self.parts:
            return
        self._apply_material_preset()
        if settings().value("intranet/auto_nest", "true") == "true" and not self.too_big:
            QTimer.singleShot(300, self.start_nest)

    def _load_batch(self, items: list, failed: Optional[list] = None):
        """Várias solicitações encaixadas juntas (cada material com suas placas)."""
        from ...core.intranet import (
            batch_label,
            batch_summary,
            file_materials,
            file_multipliers,
            file_tags,
        )
        items = [(d, [f for f in fs if f.local_path and os.path.isfile(f.local_path)]) for d, fs in items]
        missing = [str(d.code) for d, fs in items if not fs]
        items = [(d, fs) for d, fs in items if fs]
        if not items:
            return
        files = [f for _, fs in items for f in fs]
        info = batch_summary(items)
        if not self.load_files([f.local_path for f in files], multipliers=file_multipliers(files),
                               request_label=batch_label([d.code for d, _ in items]),
                               materials=file_materials(files), request_info=info, tags=file_tags(items)):
            return
        codes = ", ".join(str(d.code) for d, _ in items)
        txt = (f"Lote com <b>{len(items)}</b> solicitações ({codes}) — "
               + ", ".join(f"<b>{m}</b>" for m in info["materials"])
               + ". As peças estão juntas nas placas; o nome de cada peça começa com o nº da solicitação.")
        warn = []
        if missing:
            warn.append(f"solicitação(ões) sem nenhum arquivo baixado, NÃO incluídas: {', '.join(missing)}")
        if failed:
            warn.append(f"{len(failed)} arquivo(s) não baixaram: " + ", ".join(failed[:6])
                        + ("…" if len(failed) > 6 else ""))
        if warn:
            txt += " <b>ATENÇÃO:</b> " + "; ".join(warn) + "."
        self.show_banner(txt, "warn" if warn else "info")
        self.setWindowTitle(f"{APP_NAME} — Lote {codes}")
