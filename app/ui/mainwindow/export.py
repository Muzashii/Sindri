"""Exportação para o RDWorks (DXF com todas as placas + relatório PDF)."""
from __future__ import annotations

import os
import copy
import tempfile

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from ...core import laser, material_safety, operations
from ...core.dxf_export import export_all_sheets
from ...core.models import NestResult
from ...core.rdworks import files_to_open, find_rdworks, launch
from ...core.validate import validate_layout
from ..dialogs import ExportDialog, settings
from ..prefs import get_list_value
from ..report import export_pdf
from ..tasks import run_task


class ExportMixin:
    def export_with_options(self):
        self.export(ask=True)

    def export_photo(self):
        """Aba Gravação de foto: grava o DXF da foto e abre no RDWorks com a potência de cada nível."""
        from ...core.photo import write_bmp, write_dxf
        pp = self.photo_panel
        if pp.result is None or not pp.btn_export.isEnabled():
            return
        rp = pp.export_params()
        if rp is None:
            return
        ext = ".bmp" if rp.mode == "imagem" else ".dxf"
        st = settings()
        folder = st.value("photo/last_dir", "") or (os.path.dirname(pp.image_path) if pp.image_path else "")
        if not folder or not os.path.isdir(folder):
            folder = QFileDialog.getExistingDirectory(self, "Onde salvar o arquivo da foto?",
                                                      os.path.expanduser("~"))
            if not folder:
                return
        st.setValue("photo/last_dir", folder)
        stem = os.path.splitext(os.path.basename(pp.image_path))[0] or "foto"
        import re
        stem = re.sub(r"[^\w\-]+", "_", stem).strip("_") or "foto"
        base, k = f"{stem}_foto", 2
        while os.path.exists(os.path.join(folder, base + ext)):
            base = f"{stem}_foto_{k}"
            k += 1
        path = os.path.join(folder, base + ext)
        p = rp
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            w, h = pp.plate_size()
            outline = None
            if rp.mode == "imagem":
                write_bmp(pp.result, path)
            else:
                outline = (w, h) if st.value("export/outline2", "true") == "true" else None
                write_dxf(pp.result, rp, path, outline=outline)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Erro ao exportar", f"Não foi possível salvar o arquivo da foto:\n{e}")
            return
        QApplication.restoreOverrideCursor()
        values = pp.laser_values(self._laser_palette())
        opened = self._open_in_rdworks([path], values=values) if st.value("export/open_rdworks", "true") == "true" else ""
        from urllib.parse import quote
        txt = (f"<b>Foto exportada:</b> {os.path.basename(path)} · "
               f'<a href="open:{quote(folder)}">Abrir pasta</a>')
        if rp.mode == "imagem" and rp.dither:
            txt += (f"<br>Imagem pontilhada ({pp.result.width_mm:.0f} × {pp.result.height_mm:.0f} mm): camada preta "
                    f"com {p.power_max:g}% a {p.speed:g} mm/s. No RDWorks deixe a camada em <b>modo varredura "
                    "(scan)</b> e posicione a imagem na placa.")
        elif rp.mode == "imagem":
            txt += (f"<br>Imagem em tons de cinza ({pp.result.width_mm:.0f} × {pp.result.height_mm:.0f} mm): "
                    f"camada preta com {p.power_min:g}% (claros) a {p.power_max:g}% (escuros) a {p.speed:g} mm/s. "
                    "No RDWorks deixe a camada em <b>modo varredura (scan)</b> e posicione a imagem na placa.")
        else:
            powers = ", ".join(f"{pw:g}%" for pw in p.level_powers())
            txt += f"<br>Níveis (do escuro ao claro): {powers} a {p.speed:g} mm/s."
        if opened:
            txt += "<br>" + opened.replace("\n", "<br>")
        if outline:
            txt += "<br><b>RDWorks:</b> na camada cinza (contorno da placa) coloque <b>saída = NÃO</b>."
        pp.show_message(txt)

    def _fill_export_menu(self):
        """Menu da seta ao lado de Exportar: tudo, a placa da tela, ou uma placa escolhida."""
        m = self._export_menu
        m.clear()
        if not self.placements:
            m.addAction("Encaixe primeiro").setEnabled(False)
            return
        a = m.addAction("Todas as placas + relatório\tCtrl+E")
        a.triggered.connect(lambda: self.export())
        idx = self.sheet_index()
        if self.canvas.mode == "layout":
            cur = self.canvas.current_sheet()
            if cur in idx.number:
                a = m.addAction(f"Só a placa da tela (Placa {idx.number[cur]})\tCtrl+Alt+E")
                a.triggered.connect(lambda _=False, s=cur: self.export(sheet=s))
        m.addSeparator()
        head = m.addAction("Só uma placa:")
        head.setEnabled(False)
        for si in idx.ordered:
            n = idx.count(si)
            mat = idx.material.get(si) or "sem material"
            mark = "✓ cortada · " if si in self.cut_sheets else ""
            a = m.addAction(f"   Placa {idx.number[si]} · {mat} · {n} peça(s)  {mark}".rstrip(" ·"))
            a.triggered.connect(lambda _=False, s=si: self.export(sheet=s))

    def export_current_sheet(self):
        """Só a placa que está no centro da tela."""
        if self.placements and self.canvas.mode == "layout":
            self.export(sheet=self.canvas.current_sheet())

    def export(self, ask: bool = False, sheet: int | None = None, open_rd: bool | None = None):
        """Exporta direto com as opções da última vez (Ctrl+E). Ctrl+Shift+E abre a janela de opções.
        ``sheet``: exporta só essa placa (um DXF com o nº dela, sem relatório).
        ``open_rd``: abre (ou não) no RDWorks, independentemente da opção salva."""
        if self.worker is not None:                 # exportar durante o encaixe: para e usa a melhor solução
            self.stop_nest(wait=True)
        if not self.placements:
            return
        sheet_no, sheet_mat = None, ""
        if sheet is not None:
            idx0 = self.sheet_index()
            sheet_no = idx0.number.get(sheet)
            if sheet_no is None:
                return
            sheet_mat = idx0.material.get(sheet, "")
        p = self.settings_panel.params()
        # problemas numa única confirmação (só aparece se houver algum)
        problems = []
        self._mark_collisions()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            check = [pl for pl in self.placements if sheet is None or pl.sheet_index == sheet]
            parts_snapshot, check = copy.deepcopy(self.pmap), copy.deepcopy(check)
            issues = run_task(self, "Validando geometria antes de exportar", lambda: validate_layout(parts_snapshot, check, p))
        finally:
            QApplication.restoreOverrideCursor()
        if issues:
            QMessageBox.warning(self, "Encaixe inválido", "Corrija o encaixe antes de exportar:\n\n" + "\n".join(issues[:8]))
            return
        if not self.check_material_safety(sheet):
            return
        if not self.confirm_color_ops():
            return
        missing = sum(pt.quantity for pt in self.parts) - len(self.placements)
        if missing > 0 and sheet is None:
            problems.append(f"{missing} peça(s) não estão no encaixe (ficam de fora do arquivo).")
        if problems:
            r = QMessageBox.warning(self, "Exportar mesmo assim?", "\n\n".join("• " + x for x in problems),
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r != QMessageBox.Yes:
                return
        o = self._export_options(ask)
        if o is None:
            return
        if open_rd is not None:
            o["open_rdworks"] = bool(open_rd)
        self._do_export(o, p, sheet_no, sheet_mat)

    def _export_options(self, ask: bool) -> dict | None:
        st = settings()
        folder = st.value("export/last_dir", "") or ""
        base = self.request_label or os.path.splitext(os.path.basename(self.project_path or self.files[0]))[0]
        if ask or not folder or not os.path.isdir(folder):
            dlg = ExportDialog(folder or (os.path.dirname(self.files[0]) if self.files else os.getcwd()), base,
                               len({pl.sheet_index for pl in self.placements}), self)
            if not dlg.exec():
                return None
            o = dlg.options()
        else:
            o = {"folder": folder, "base": base, "version": st.value("export/version", "R2000"),
                 "outline": st.value("export/outline2", "true") == "true",
                 "inner": st.value("export/inner", "true") == "true",
                 "path": st.value("export/path", "true") == "true",
                 "open_rdworks": st.value("export/open_rdworks", "true") == "true"}
        o.setdefault("start", st.value("export/start", "inferior_esquerdo") or "inferior_esquerdo")
        o.setdefault("order", st.value("export/order", "caminho") or "caminho")
        st.setValue("export/last_dir", o["folder"])
        return o

    def _laser_export_plan(self, only: set | None = None) -> dict:
        """Cores e velocidade/potência do arquivo exportado (o mesmo para o lote e para cada placa)."""
        groups = self._laser_plan(only)
        numbers = self.numbers_cfg()
        labels = self._part_labels()
        plan = {"groups": groups, "cmap": laser.color_map(groups), "rd_values": None, "numbers": numbers,
                "labels": labels, "op_colors": None, "color_ops": self.effective_color_ops()}
        if self.material_mode():
            _, plan["op_colors"], plan["rd_values"] = self._material_plan()
            plan["cmap"] = None
        elif labels:                                 # cores do desenho + camada dos números
            vals = self.laser_values()
            rd = {g.rd_index: tuple(vals[g.key]) for g in groups if vals.get(g.key)}
            rd.update(laser.material_values({}, {}, numbers, self._laser_palette()))
            plan["rd_values"] = rd
        return plan

    def _export_kwargs(self, o: dict, plan: dict, stats: dict) -> dict:
        numbers = plan["numbers"]
        return dict(version=o["version"], inner_first=o["inner"], sort_path=o["path"],
                    color_map=plan["cmap"], stats=stats, part_labels=plan["labels"] or None,
                    label_aci=int(numbers["color"]), label_height=float(numbers.get("height") or 3.0),
                    op_colors=plan["op_colors"], color_ops=plan["color_ops"],
                    start_corner=o.get("start", "inferior_esquerdo"))

    def _sheet_stamp(self, si: int, plan: dict) -> str:
        """Impressão digital do que vai no arquivo de uma placa (peças, posições e cores): se mudar,
        o arquivo antigo não serve mais para cortar."""
        import hashlib
        import json
        pls = sorted((self.pmap[pl.part_id].identity, round(pl.x, 4), round(pl.y, 4), round(pl.rotation, 4),
                      bool(pl.mirrored)) for pl in self.placements if pl.sheet_index == si and pl.part_id in self.pmap)
        colors = sorted(f"{k}={v}" for k, v in (plan["op_colors"] or plan["cmap"] or {}).items())
        payload = json.dumps([pls, colors, sorted((plan["labels"] or {}).items())], ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _do_export(self, o: dict, p, sheet_no: int | None = None, sheet_mat: str = ""):
        """Grava os arquivos. Exportação completa: um DXF de CORTE por placa (origem no canto da chapa, sem
        contorno), o arquivo de CONFERÊNCIA com todas as placas lado a lado e o relatório PDF."""
        from ...core.dxf_export import export_cut_files, sheet_file_suffix
        st = settings()
        self._compact_sheets()
        idx = self.sheet_index()
        only = None
        if sheet_no is not None:                     # índice da placa depois de renumerar
            only = {si for si in idx.ordered if idx.number[si] == sheet_no}
        sheets = [si for si in idx.ordered if only is None or si in only]
        suffixes = [sheet_file_suffix(idx.number[si], idx.material.get(si, "")) + ".dxf" for si in sheets]
        if only is None:
            suffixes += ["_todas_placas.dxf", "_relatorio.pdf"]
        # nomes livres: em vez de perguntar, acrescenta _2, _3…
        base, k = o["base"], 2
        while any(os.path.exists(os.path.join(o["folder"], f"{base}{suf}")) for suf in suffixes):
            base = f"{o['base']}_{k}"
            k += 1
        o = {**o, "base": base}
        plan = self._laser_export_plan(only)
        export_stats: dict = {}
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            parts, placements, pmap = copy.deepcopy((self.parts, self.placements, self.pmap))
            n_sheets, utilization, unplaced = self.n_sheets, self._utilization(), list(self.unplaced)
            header, requests = self._report_header(), copy.deepcopy(self._report_requests())
            kw = self._export_kwargs(o, plan, export_stats)

            def write_outputs():
                os.makedirs(o["folder"], exist_ok=True)
                with tempfile.TemporaryDirectory(prefix=".sindri-export-", dir=o["folder"]) as stage:
                    cut = export_cut_files(parts, placements, p, stage, o["base"], only_sheets=only, **kw)
                    sources = [cut[si] for si in sheets]
                    combined = pdf = None
                    if only is None:                     # conferência + relatório só na exportação completa
                        kw2 = {**kw, "stats": {}}
                        combined = export_all_sheets(parts, placements, p, stage, o["base"],
                                                     sheet_outline=o["outline"], file_suffix="_todas_placas", **kw2)
                        res = NestResult(placements, n_sheets, utilization, 0.0, unplaced)
                        pdf = os.path.join(stage, f"{o['base']}_relatorio.pdf")
                        export_pdf(pdf, o["base"], pmap, res, p, header=header, requests=requests)
                        sources += [combined, pdf]
                    moved = {}
                    try:
                        for source in sources:
                            target = os.path.join(o["folder"], os.path.basename(source))
                            if os.path.exists(target):
                                raise FileExistsError(target)
                            os.rename(source, target)
                            moved[source] = target
                    except OSError:
                        for target in moved.values():
                            os.remove(target)
                        raise
                    return ({si: moved[cut[si]] for si in sheets}, moved.get(combined), moved.get(pdf))
            sheet_files, combined, pdf = run_task(self, "Gerando os DXF de corte e o relatório PDF", write_outputs)
            files = list(sheet_files.values()) + [f for f in (combined, pdf) if f]
            hist = get_list_value(st.value("export/history", []))
            st.setValue("export/history", (hist + [f for f in files if f not in hist])[-500:])
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Erro ao exportar", f"Não foi possível salvar os arquivos:\n{e}")
            return
        QApplication.restoreOverrideCursor()
        for si, path in sheet_files.items():
            self.sheet_files[si] = path
            self.sheet_stamps[si] = self._sheet_stamp(si, plan)
        self._redraw(keep_view=True)
        self._refresh_cut_panel()
        # abre a placa pedida, ou a 1ª ainda não cortada (fluxo: abrir → cortar → marcar → próxima)
        target_si = next((si for si in sheets if si not in self.cut_sheets), sheets[0])
        opened = self._open_sheet_file(target_si, plan) if o.get("open_rdworks") else ""
        from urllib.parse import quote
        links = (f' · <a href="open:{quote(o["folder"])}">Abrir pasta</a>'
                 + (f' · <a href="open:{quote(pdf)}">Abrir relatório</a>' if pdf else "")
                 + (f' · <a href="open:{quote(combined)}">Conferência (todas as placas)</a>' if combined else "")
                 + ' · <a href="opts:">Opções de exportação…</a>')
        if sheet_no is not None:
            what = f"só a placa {sheet_no}" + (f" · {sheet_mat}" if sheet_mat else "")
        else:
            what = f"{len(sheet_files)} arquivo(s) de corte, um por placa + conferência + relatório"
        txt = f"<b>Exportado ({what}):</b> {os.path.basename(sheet_files[sheets[0]])}" \
              + ("…" if len(sheets) > 1 else "") + links
        if export_stats.get("overlaps"):
            txt += (f"<br>{export_stats['overlaps']} linha(s) repetida(s) ou sobreposta(s) removida(s) — "
                    "o laser passa uma vez só em cada trecho.")
        labels, numbers = plan["labels"], plan["numbers"]
        if labels:
            n_ok, n_skip = export_stats.get("labels", 0), export_stats.get("labels_skipped", 0)
            txt += (f"<br>Nº da solicitação gravado em {n_ok} peça(s), em "
                    f"{laser.color_name(int(numbers['color'])).lower()}"
                    + (f" ({n_skip} pequena(s) demais ficaram sem número)" if n_skip else "")
                    + ". No RDWorks deixe a camada dos números <b>antes</b> das de corte.")
        if opened:
            txt += "<br>" + opened.replace("\n", "<br>")
        txt += ("<br><b>Confira no RDWorks antes do Start</b> (cada placa num arquivo, origem no canto da chapa, "
                "sem contorno da placa):" + laser.checklist_html(self._layer_checks(target_si, plan)))
        if combined and o["outline"]:
            txt += ("<br>No arquivo de conferência a camada cinza (contorno e nº das placas) é só para olhar: "
                    "não corte por ele.")
        self.show_banner(txt, "ok")
        self.statusBar().showMessage(f"Exportado em {o['folder']}", 8000)
        self.schedule_autosave()

    def _open_sheet_file(self, si: int, plan: dict) -> str:
        path = self.sheet_files.get(si)
        if not path or not os.path.isfile(path):
            return ""
        values = plan["rd_values"]
        groups = [] if values is not None else plan["groups"]
        return self._open_in_rdworks([path], groups, values)

    def open_sheet_in_rdworks(self, si: int):
        """Botão "Abrir no RDWorks" do checklist: abre só o arquivo daquela placa. Se o arquivo não
        existe ou a placa mudou desde a exportação, exporta a placa de novo antes."""
        if self.worker is not None or not self.placements:
            return
        plan = self._laser_export_plan({si})
        path = self.sheet_files.get(si)
        if path and os.path.isfile(path) and self.sheet_stamps.get(si) == self._sheet_stamp(si, plan):
            if not self.check_material_safety(si):
                return
            msg = self._open_sheet_file(si, plan)
            n = self.sheet_index().number.get(si, si + 1)
            self.show_banner(f"<b>Placa {n}</b> aberta no RDWorks: {os.path.basename(path)}"
                             + ("<br>" + msg.replace("\n", "<br>") if msg else "")
                             + "<br><b>Confira antes do Start:</b>"
                             + laser.checklist_html(self._layer_checks(si, plan))
                             + "<br>Depois de cortar, marque a placa como cortada.", "ok")
            return
        self.export(sheet=si, open_rd=True)

    def _layer_checks(self, si: int | None, plan: dict) -> list:
        """O que o RDWorks deve mostrar, camada por camada, para o arquivo da placa ``si``."""
        from ...core.operations import LABELS
        sheet_parts = {pl.part_id for pl in self.placements if si is None or pl.sheet_index == si}
        mats = {self.pmap[pid].material or "" for pid in sheet_parts if pid in self.pmap}
        entries = []
        if plan["op_colors"] is not None:
            cfg = self.material_cfg()
            parts = [self.pmap[pid] for pid in sheet_parts if pid in self.pmap]
            used = operations.layer_ops_in_use(parts, plan["color_ops"])
            for (m, op), aci in plan["op_colors"].items():
                if m not in mats or op not in used.get(m, []):
                    continue
                c = cfg.get(m) or {}
                if op != operations.CUT:
                    c = (c.get("ops") or {}).get(op) or {}
                entries.append(laser.LayerCheck(aci, f"{m or 'sem material'} · {LABELS[op].lower()}", op,
                                                c.get("mode", ""), float(c.get("speed") or 0),
                                                float(c.get("power_min") or 0), float(c.get("power") or 0),
                                                int(c.get("passes") or 1)))
        else:
            vals = self.laser_values()
            for g in plan["groups"]:
                if g.material not in mats:
                    continue
                v = vals.get(g.key) or [0, 0]
                entries.append(laser.LayerCheck(g.target_aci, f"{g.material or 'sem material'} · "
                                                f"{', '.join(g.layers) or 'camada'}", "corte", "",
                                                float(v[0]), 0.0, float(v[1])))
        if plan["labels"] and any(pid in plan["labels"] for pid in sheet_parts):
            n = plan["numbers"]
            entries.append(laser.LayerCheck(int(n["color"]), "nº da solicitação", "numeros", "",
                                            float(n.get("speed") or 0), 0.0, float(n.get("power") or 0)))
        return laser.layer_checklist(entries, self._laser_palette())

    # ------------------------------------------------------------------ velocidade/potência
    def laser_values(self) -> dict:
        """{"material|cor": [velocidade mm/s, potência %]} lembrados entre usos."""
        import json
        try:
            v = json.loads(settings().value("laser/params", "{}") or "{}")
            return v if isinstance(v, dict) else {}
        except ValueError:
            return {}

    def set_laser_value(self, key: str, speed: float, power: float):
        import json
        v = self.laser_values()
        if speed > 0 and power > 0:
            v[key] = [round(float(speed), 2), round(float(power), 1)]
        else:
            v.pop(key, None)
        settings().setValue("laser/params", json.dumps(v, ensure_ascii=False))

    # ---- uma cor por material + nº das solicitações
    def _json_setting(self, key: str, default):
        import json
        try:
            v = json.loads(settings().value(key, "") or "null")
        except ValueError:
            v = None
        return v if isinstance(v, type(default)) else default

    def material_mode(self) -> bool:
        return str(settings().value("laser/material_mode", "true")).lower() == "true"

    def set_material_mode(self, on: bool):
        settings().setValue("laser/material_mode", "true" if on else "false")
        self._refresh_laser_panel(force=True)

    def material_cfg(self) -> dict:
        """{material: {"color", "speed", "power"}} lembrados entre usos."""
        return self._json_setting("laser/materials", {})

    def numbers_cfg(self) -> dict:
        return {**laser.NUMBERS_DEFAULT, **self._json_setting("laser/numbers", {})}

    def set_material_value(self, material: str, data: dict):
        """Linha do painel do laser. ``data`` com "op" = vinco/gravação daquele material."""
        import json
        cfg = self.material_cfg()
        entry = dict(cfg.get(material) or {})
        op = data.get("op")
        if op:
            o = dict((entry.get("ops") or {}).get(op) or {})
            old = o.get("color")
            for k in ("speed", "power", "color"):
                if k in data:
                    o[k] = round(float(data[k] or 0), 2 if k == "speed" else 1) if k != "color" else int(data[k])
            entry["ops"] = {**(entry.get("ops") or {}), op: o}
            cfg[material] = entry
            settings().setValue("laser/materials", json.dumps(cfg, ensure_ascii=False))
            if "color" in data and int(data["color"]) != (old or 0):
                self._refresh_laser_panel(force=True)
            return
        old = entry.get("color")
        cur = self._material_colors().get(material)
        entry.update({"color": int(data.get("color", cur or 7)),
                      "speed": round(float(data.get("speed") or 0), 2),
                      "power": round(float(data.get("power") or 0), 1)})
        cfg[material] = entry
        settings().setValue("laser/materials", json.dumps(cfg, ensure_ascii=False))
        if cfg[material]["color"] != (old if old is not None else cur):
            self._refresh_laser_panel(force=True)    # outra cor: os outros materiais podem mudar

    def set_numbers_value(self, data: dict):
        import json
        old = self.numbers_cfg()
        new = {**old, **data}
        settings().setValue("laser/numbers", json.dumps(new))
        if (new["on"], new["color"]) != (old["on"], old["color"]):
            self._refresh_laser_panel(force=True)
        if (new["on"], new["color"], new["height"]) != (old["on"], old["color"], old["height"]):
            self._redraw(keep_view=True)             # o desenho mostra os números onde vão ser gravados

    def _materials_in_use(self) -> list[str]:
        order = [m for m, _ in self.sheet_index().groups] if self.placements else []
        for pt in self.parts:
            m = pt.material or ""
            if pt.quantity > 0 and m not in order:
                order.append(m)
        return [m for m in order if any((pt.material or "") == m and pt.quantity > 0 for pt in self.parts)]

    def _material_colors(self) -> dict[str, int]:
        n = self.numbers_cfg()
        return laser.default_material_colors(self._materials_in_use(), self.material_cfg(),
                                             int(n["color"]) if n.get("on") and self.request_info else -1)

    def _refresh_laser_panel(self, force: bool = False):
        from ...core.laser import groups_from_parts
        op_colors = self._material_plan()[1] if self.material_mode() and self.parts else {}
        self.settings_panel.set_laser_state(groups_from_parts(self.parts), self.laser_values(),
                                            self.material_mode(), self._material_colors(), self.material_cfg(),
                                            self.numbers_cfg(), bool(self.request_info), force=force,
                                            op_colors=op_colors)

    def _request_numbers(self) -> list[tuple[str, dict]]:
        """[(nº da solicitação, dados)] na ordem do lote: a 1ª leva o número 1, a 2ª o 2…"""
        i = self.request_info
        if not i:
            return []
        reqs = list(i.get("requests", [])) if i.get("batch") else [i]
        return [(str(r.get("code", "")), r) for r in reqs]

    def _part_labels(self) -> dict[str, str]:
        """id da peça -> número gravado nela (só peças de solicitações do portal)."""
        if not self.numbers_cfg().get("on"):
            return {}
        reqs = self._request_numbers()
        if not reqs:
            return {}
        num = {code: str(k + 1) for k, (code, _) in enumerate(reqs)}
        out = {}
        for pt in self.parts:
            if pt.tag and pt.tag in num:
                out[pt.id] = num[pt.tag]
            elif len(reqs) == 1:                     # solicitação única: todas as peças são dela
                out[pt.id] = "1"
        return out

    def _numbers_legend(self) -> list[str]:
        if not self._part_labels():
            return []
        return ["Números gravados nas peças: " + "  ·  ".join(
            f"{k + 1} = nº {code}" + (f" {r.get('nome', '')}" if r.get("nome") else "")
            for k, (code, r) in enumerate(self._request_numbers()))]

    def _laser_palette(self):
        exe = find_rdworks(settings().value("rdworks/exe", "") or None)
        return laser.read_palette(exe) if exe else None

    def _laser_plan(self, only: set | None = None) -> list:
        """Camadas do arquivo exportado (material × cor) e a camada do RDWorks de cada uma."""
        placed = {pl.part_id for pl in self.placements if only is None or pl.sheet_index in only}
        groups = laser.groups_from_parts(pt for pt in self.parts if pt.id in placed)
        order = [m for m, _ in self.sheet_index().groups]
        return laser.plan_layers(groups, self._laser_palette(), order)

    def _material_plan(self) -> tuple[dict, dict, dict]:
        """(cor do corte por material, {(material, camada de laser): cor no DXF},
        {camada do RDWorks: (vel, pot)}). Só o corte vai para a cor do material; vinco e gravação
        ganham cores próprias."""
        colors = self._material_colors()
        placed = {pl.part_id for pl in self.placements}
        parts = [pt for pt in self.parts if pt.id in placed] if placed else list(self.parts)
        n = self.numbers_cfg() if self._part_labels() else None
        palette = self._laser_palette()
        op_colors = laser.material_op_colors(parts, colors, self.effective_color_ops(),
                                             int(n["color"]) if n and n.get("on") else -1,
                                             self._op_color_prefs(), palette)
        values = laser.material_values(op_colors, self.material_cfg(), n, palette)
        return colors, op_colors, values

    def _op_color_prefs(self) -> dict:
        """{(material, camada): cor} escolhida no painel para vinco/gravação."""
        out = {}
        for m, c in self.material_cfg().items():
            for op, o in ((c or {}).get("ops") or {}).items():
                if isinstance(o, dict) and int(o.get("color") or 0) > 0:
                    out[(m, op)] = int(o["color"])
        return out

    # ---- operação de cada cor do arquivo (corte × vinco × gravação)
    def effective_color_ops(self) -> dict:
        return operations.merge_color_ops(self.parts, self.color_ops)

    def confirm_color_ops(self, force: bool = False) -> bool:
        """Arquivo com mais de uma cor num material: o técnico confere o que é corte e o que é gravação
        (uma vez por importação). False = cancelou."""
        parts = [pt for pt in self.parts if pt.quantity > 0]
        keys = set(operations.file_colors(parts))
        if not force and (not operations.needs_confirmation(parts) or keys <= self.color_ops_confirmed):
            return True
        from ..color_ops_dialog import ColorOpsDialog
        dlg = ColorOpsDialog(parts, self.color_ops, self)
        if not dlg.exec():
            return False
        self.color_ops.update(dlg.result_ops())
        self.color_ops_confirmed |= keys
        self.mark_changed()
        self._refresh_laser_panel(force=True)
        return True

    def check_material_safety(self, sheet: int | None = None) -> bool:
        """Bloqueia a exportação de material que não pode ir para o laser (PVC, vinil, policarbonato…)."""
        mats = [self.pmap[pl.part_id].material for pl in self.placements
                if pl.part_id in self.pmap and (sheet is None or pl.sheet_index == sheet)]
        blocked = material_safety.blocked_materials(mats, self._material_forbidden)
        if not blocked:
            return True
        QMessageBox.critical(self, "Material proibido no laser",
                             "Não exporto este lote para o laser:\n\n"
                             + "\n".join(f"• {m}: {why}" for m, why in blocked)
                             + "\n\nTroque o material da solicitação (ou devolva ao aluno) antes de cortar.")
        return False

    def _material_forbidden(self, material: str) -> tuple[bool, str]:
        c = self.material_cfg().get(material) or {}
        return bool(c.get("forbidden")), str(c.get("forbidden_reason") or "")

    def _laser_report_lines(self) -> list[str]:
        if self.material_mode():
            cfg, lines = self.material_cfg(), []
            _, op_colors, _ = self._material_plan()
            for (m, op), aci in op_colors.items():
                c = cfg.get(m) or {}
                if op != operations.CUT:
                    c = (c.get("ops") or {}).get(op) or {}
                v = (f"{float(c['speed']):g} mm/s · {float(c['power']):g}%"
                     if float(c.get("speed") or 0) > 0 and float(c.get("power") or 0) > 0 else "valores do RDWorks")
                lines.append(f"Laser · {m or 'sem material'} · {operations.LABELS[op].lower()} em "
                             f"{laser.color_name(aci).lower()}: {v}")
            if self._part_labels():
                n = self.numbers_cfg()
                v = (f"{float(n['speed']):g} mm/s · {float(n['power']):g}%"
                     if float(n.get("speed") or 0) > 0 and float(n.get("power") or 0) > 0 else "valores do RDWorks")
                lines.append(f"Laser · números em {laser.color_name(int(n['color'])).lower()}: {v}")
            return lines + self._numbers_legend()
        vals = self.laser_values()
        lines = []
        for g in self._laser_plan():
            v = vals.get(g.key)
            if v:
                lines.append(f"Laser · {g.material or 'sem material'} · {', '.join(g.layers) or 'camada'}: "
                             f"{v[0]:g} mm/s · {v[1]:g}%")
        return lines + self._numbers_legend()

    def _apply_laser(self, exe: str, path: str, groups: list,
                     values: dict | None = None) -> tuple[bool, str]:
        """Grava velocidade/potência no RDWorks antes de abrir. Devolve (já abriu o RDWorks?, aviso).
        ``values``: {camada do RDWorks: (velocidade, potência)} já prontos (ex.: gravação de foto)."""
        if values is None:
            vals = self.laser_values()
            values = {g.rd_index: tuple(vals[g.key]) for g in groups if vals.get(g.key)}
        if not values:
            return False, ""
        cfg = laser.config_path(exe)
        if not os.path.isfile(cfg):
            return False, "Velocidade/potência não aplicadas: não achei o arquivo de configuração do RDWorks."
        while laser.rdworks_running():
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("Feche o RDWorks")
            box.setText("O RDWorks já está aberto.")
            box.setInformativeText("Para o Sindri preencher velocidade e potência, feche o RDWorks e clique em "
                                   "“Tentar de novo”. (Com ele aberto, os valores seriam perdidos.)")
            again = box.addButton("Tentar de novo", QMessageBox.AcceptRole)
            box.addButton("Abrir sem aplicar", QMessageBox.RejectRole)
            box.setDefaultButton(again)
            box.exec()
            if box.clickedButton() != again:
                return False, "Velocidade/potência NÃO aplicadas (o RDWorks estava aberto)."
        try:
            laser.apply_to_config(cfg, values)
            return False, ""
        except PermissionError:
            pass                                    # pasta do RDWorks protegida: ajudante como administrador
        except (OSError, ValueError) as e:
            return False, f"Velocidade/potência não aplicadas: {e}"
        import json
        job = os.path.join(tempfile.gettempdir(), f"sindri_rdworks_{os.getpid()}.json")
        with open(job, "w", encoding="utf-8") as fh:
            json.dump({"config": cfg, "values": {str(k): v for k, v in values.items()},
                       "exe": exe, "file": os.path.abspath(path)}, fh)
        try:
            laser.launch_elevated_helper(job)
        except OSError as e:
            return False, f"Velocidade/potência não aplicadas: {e}"
        return True, ""

    def _open_in_rdworks(self, files: list[str], groups: list | None = None,
                         values: dict | None = None) -> str:
        """Abre o DXF exportado no RDWorks. Devolve uma frase para a mensagem final."""
        targets = files_to_open(files)
        if not targets:
            return ""
        st = settings()
        exe = find_rdworks(st.value("rdworks/exe", "") or None)
        if not exe:
            QMessageBox.information(self, "Onde está o RDWorks?",
                                    "Não encontrei o RDWorks neste computador.\n"
                                    "Mostre onde está o RDWorksV8.exe (só precisa fazer isso uma vez).")
            exe, _ = QFileDialog.getOpenFileName(self, "Localizar o RDWorks",
                                                 os.environ.get("ProgramFiles(x86)", "C:\\"),
                                                 "Programa (*.exe)")
            if not exe:
                return "O RDWorks não foi aberto (programa não localizado)."
        st.setValue("rdworks/exe", exe)
        path = targets[0]
        QApplication.clipboard().setText(os.path.abspath(path))
        opened, laser_msg = self._apply_laser(exe, path, groups or [], values)
        try:
            if not opened:
                launch(exe, path)
        except OSError as e:
            if not os.path.isfile(exe):
                st.remove("rdworks/exe")
            return f"Não foi possível abrir o RDWorks: {e}"
        msg = (f"Abrindo {os.path.basename(path)} no RDWorks "
               "(o Windows pode pedir permissão — o RDWorks roda como administrador).")
        msg += ("\nSe ele abrir vazio: Arquivo › Importar (Ctrl+I), Ctrl+V e Enter "
                "— o caminho do arquivo já está copiado.")
        applied = [g for g in (groups or []) if self.laser_values().get(g.key)]
        if laser_msg:
            msg += "\n⚠ " + laser_msg
        elif applied:
            msg += "\nVelocidade/potência preenchidas no RDWorks: " + "; ".join(
                f"{g.material or 'sem material'} {self.laser_values()[g.key][0]:g} mm/s "
                f"{self.laser_values()[g.key][1]:g}%" for g in applied) + "."
        elif values:
            msg += f"\nVelocidade/potência preenchidas no RDWorks em {len(values)} camada(s)."
        return msg

    def _report_header(self) -> list[str]:
        return self._request_header() + self._laser_report_lines()

    def _request_header(self) -> list[str]:
        i = self.request_info
        if not i:
            return []
        if i.get("batch"):
            lines = [f"Lote com {len(i.get('requests', []))} solicitações · Materiais: "
                     f"{', '.join(i.get('materials', []))}"]
            for r in i.get("requests", []):
                lines.append(f"  nº {r.get('code', '')} · RM {r.get('rm', '')} · {r.get('nome', '')}"
                             + (f" · {r['projeto']}" if r.get("projeto") else ""))
            return lines
        return [f"Solicitação nº {i.get('code', '')} · RM {i.get('rm', '')} · {i.get('nome', '')}",
                "  ·  ".join(x for x in (f"Projeto: {i['projeto']}" if i.get("projeto") else "",
                                         f"Professor: {i['professor']}" if i.get("professor") else "",
                                         f"Materiais: {', '.join(i.get('materials', []))}") if x)]

    def _report_requests(self) -> list[dict]:
        i = self.request_info
        if not i:
            return []
        return list(i.get("requests", [])) if i.get("batch") else [i]
