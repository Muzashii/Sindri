"""Exportação para o RDWorks (DXF com todas as placas + relatório PDF)."""
from __future__ import annotations

import os
import tempfile

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from ...core import laser
from ...core.dxf_export import export_all_sheets
from ...core.models import NestResult
from ...core.rdworks import files_to_open, find_rdworks, launch
from ...core.validate import validate_layout
from ..dialogs import ExportDialog, settings
from ..prefs import get_list_value
from ..report import export_pdf


class ExportMixin:
    def export_with_options(self):
        self.export(ask=True)

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

    def export(self, ask: bool = False, sheet: int | None = None):
        """Exporta direto com as opções da última vez (Ctrl+E). Ctrl+Shift+E abre a janela de opções.
        ``sheet``: exporta só essa placa (um DXF com o nº dela, sem relatório)."""
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
            issues = validate_layout(self.pmap, check, p)
        finally:
            QApplication.restoreOverrideCursor()
        if issues:
            QMessageBox.warning(self, "Encaixe inválido", "Corrija o encaixe antes de exportar:\n\n" + "\n".join(issues[:8]))
            return
        missing = sum(pt.quantity for pt in self.parts) - len(self.placements)
        if missing > 0 and sheet is None:
            problems.append(f"{missing} peça(s) não estão no encaixe (ficam de fora do arquivo).")
        if problems:
            r = QMessageBox.warning(self, "Exportar mesmo assim?", "\n\n".join("• " + x for x in problems),
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r != QMessageBox.Yes:
                return
        st = settings()
        folder = st.value("export/last_dir", "") or ""
        base = self.request_label or os.path.splitext(os.path.basename(self.project_path or self.files[0]))[0]
        if ask or not folder or not os.path.isdir(folder):
            dlg = ExportDialog(folder or (os.path.dirname(self.files[0]) if self.files else os.getcwd()), base,
                               len({pl.sheet_index for pl in self.placements}), self)
            if not dlg.exec():
                return
            o = dlg.options()
        else:
            o = {"folder": folder, "base": base, "version": st.value("export/version", "R2000"),
                 "outline": st.value("export/outline2", "true") == "true",
                 "inner": st.value("export/inner", "true") == "true",
                 "path": st.value("export/path", "true") == "true",
                 "open_rdworks": st.value("export/open_rdworks", "true") == "true"}
        # nomes livres: em vez de perguntar, acrescenta _2, _3…
        if sheet_no is not None:
            from ...core.dxf_export import material_tag
            o["base"] = f"{o['base']}_placa{sheet_no}" + (f"_{material_tag(sheet_mat)}" if sheet_mat else "")
        suffix = "" if sheet_no is not None else "_todas_placas"
        base, k = o["base"], 2
        while any(os.path.exists(os.path.join(o["folder"], f"{base}{suf}"))
                  for suf in (f"{suffix}.dxf", "_relatorio.pdf")):
            base = f"{o['base']}_{k}"
            k += 1
        o["base"] = base
        st.setValue("export/last_dir", o["folder"])
        self._compact_sheets()
        only = None
        if sheet_no is not None:                     # índice da placa depois de renumerar
            idx = self.sheet_index()
            only = {si for si in idx.ordered if idx.number[si] == sheet_no}
        groups = self._laser_plan(only)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            os.makedirs(o["folder"], exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".sindri-export-", dir=o["folder"]) as stage:
                dxf = export_all_sheets(self.parts, self.placements, p, stage, o["base"], o["version"],
                                        sheet_outline=o["outline"], inner_first=o["inner"], sort_path=o["path"],
                                        color_map=laser.color_map(groups), only_sheets=only,
                                        file_suffix=suffix)
                sources = [dxf]
                if only is None:                     # relatório só na exportação completa
                    res = NestResult(self.placements, self.n_sheets, self._utilization(), 0.0, self.unplaced)
                    staged_pdf = os.path.join(stage, f"{o['base']}_relatorio.pdf")
                    export_pdf(staged_pdf, o["base"], self.pmap, res, p, header=self._report_header(),
                               requests=self._report_requests())
                    sources.append(staged_pdf)
                files = []
                try:
                    for source in sources:
                        target = os.path.join(o["folder"], os.path.basename(source))
                        if os.path.exists(target):
                            raise FileExistsError(target)
                        os.rename(source, target)
                        files.append(target)
                except OSError:
                    for target in files:
                        os.remove(target)
                    raise
                pdf = files[1] if len(files) > 1 else None
            hist = get_list_value(st.value("export/history", []))
            st.setValue("export/history", (hist + [f for f in files if f not in hist])[-500:])
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Erro ao exportar", f"Não foi possível salvar os arquivos:\n{e}")
            return
        QApplication.restoreOverrideCursor()
        self._redraw(keep_view=True)
        opened = self._open_in_rdworks(files, groups) if o.get("open_rdworks") else ""
        from urllib.parse import quote
        links = (f' · <a href="open:{quote(o["folder"])}">Abrir pasta</a>'
                 + (f' · <a href="open:{quote(pdf)}">Abrir relatório</a>' if pdf else "")
                 + ' · <a href="opts:">Opções de exportação…</a>')
        what = (f"só a placa {sheet_no}" + (f" · {sheet_mat}" if sheet_mat else "")
                if sheet_no is not None else "todas as placas + relatório")
        txt = f"<b>Exportado ({what}):</b> {os.path.basename(files[0])}" + links
        if opened:
            txt += "<br>" + opened.replace("\n", "<br>")
        if o["outline"]:
            txt += "<br><b>RDWorks:</b> na camada cinza (contorno e nº das placas) coloque <b>saída = NÃO</b>."
        self.show_banner(txt, "ok")
        self.statusBar().showMessage(f"Exportado em {o['folder']}", 8000)
        self.schedule_autosave()

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

    def _laser_palette(self):
        exe = find_rdworks(settings().value("rdworks/exe", "") or None)
        return laser.read_palette(exe) if exe else None

    def _laser_plan(self, only: set | None = None) -> list:
        """Camadas do arquivo exportado (material × cor) e a camada do RDWorks de cada uma."""
        placed = {pl.part_id for pl in self.placements if only is None or pl.sheet_index in only}
        groups = laser.groups_from_parts(pt for pt in self.parts if pt.id in placed)
        order = [m for m, _ in self.sheet_index().groups]
        return laser.plan_layers(groups, self._laser_palette(), order)

    def _laser_report_lines(self) -> list[str]:
        vals = self.laser_values()
        lines = []
        for g in self._laser_plan():
            v = vals.get(g.key)
            if v:
                lines.append(f"Laser · {g.material or 'sem material'} · {', '.join(g.layers) or 'camada'}: "
                             f"{v[0]:g} mm/s · {v[1]:g}%")
        return lines

    def _apply_laser(self, exe: str, path: str, groups: list) -> tuple[bool, str]:
        """Grava velocidade/potência no RDWorks antes de abrir. Devolve (já abriu o RDWorks?, aviso)."""
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

    def _open_in_rdworks(self, files: list[str], groups: list | None = None) -> str:
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
        opened, laser_msg = self._apply_laser(exe, path, groups or [])
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
