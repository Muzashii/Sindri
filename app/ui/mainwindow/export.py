"""Exportação para o RDWorks (DXF com todas as placas + relatório PDF)."""
from __future__ import annotations

import os
import tempfile

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

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

    def export(self, ask: bool = False):
        """Exporta direto com as opções da última vez (Ctrl+E). Ctrl+Shift+E abre a janela de opções."""
        if self.worker is not None:                 # exportar durante o encaixe: para e usa a melhor solução
            self.stop_nest(wait=True)
        if not self.placements:
            return
        p = self.settings_panel.params()
        # problemas numa única confirmação (só aparece se houver algum)
        problems = []
        self._mark_collisions()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            issues = validate_layout(self.pmap, self.placements, p)
        finally:
            QApplication.restoreOverrideCursor()
        if issues:
            QMessageBox.warning(self, "Encaixe inválido", "Corrija o encaixe antes de exportar:\n\n" + "\n".join(issues[:8]))
            return
        missing = sum(pt.quantity for pt in self.parts) - len(self.placements)
        if missing > 0:
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
        base, k = o["base"], 2
        while any(os.path.exists(os.path.join(o["folder"], f"{base}{suf}"))
                  for suf in ("_todas_placas.dxf", "_relatorio.pdf")):
            base = f"{o['base']}_{k}"
            k += 1
        o["base"] = base
        st.setValue("export/last_dir", o["folder"])
        self._compact_sheets()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            os.makedirs(o["folder"], exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".sindri-export-", dir=o["folder"]) as stage:
                dxf = export_all_sheets(self.parts, self.placements, p, stage, o["base"], o["version"],
                                        sheet_outline=o["outline"], inner_first=o["inner"], sort_path=o["path"])
                res = NestResult(self.placements, self.n_sheets, self._utilization(), 0.0, self.unplaced)
                staged_pdf = os.path.join(stage, f"{o['base']}_relatorio.pdf")
                export_pdf(staged_pdf, o["base"], self.pmap, res, p, header=self._report_header(),
                           requests=self._report_requests())
                files = []
                try:
                    for source in (dxf, staged_pdf):
                        target = os.path.join(o["folder"], os.path.basename(source))
                        if os.path.exists(target):
                            raise FileExistsError(target)
                        os.rename(source, target)
                        files.append(target)
                except OSError:
                    for target in files:
                        os.remove(target)
                    raise
                pdf = files[1]
            hist = get_list_value(st.value("export/history", []))
            st.setValue("export/history", (hist + [f for f in files if f not in hist])[-500:])
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Erro ao exportar", f"Não foi possível salvar os arquivos:\n{e}")
            return
        QApplication.restoreOverrideCursor()
        self._redraw(keep_view=True)
        opened = self._open_in_rdworks(files) if o.get("open_rdworks") else ""
        from urllib.parse import quote
        links = (f' · <a href="open:{quote(o["folder"])}">Abrir pasta</a>'
                 f' · <a href="open:{quote(pdf)}">Abrir relatório</a>'
                 ' · <a href="opts:">Opções de exportação…</a>')
        txt = f"<b>Exportado:</b> {os.path.basename(files[0])} + relatório" + links
        if opened:
            txt += "<br>" + opened.replace("\n", "<br>")
        if o["outline"]:
            txt += "<br><b>RDWorks:</b> na camada cinza (contorno e nº das placas) coloque <b>saída = NÃO</b>."
        self.show_banner(txt, "ok")
        self.statusBar().showMessage(f"Exportado em {o['folder']}", 8000)
        self.schedule_autosave()

    def _open_in_rdworks(self, files: list[str]) -> str:
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
        try:
            launch(exe, path)
        except OSError as e:
            if not os.path.isfile(exe):
                st.remove("rdworks/exe")
            return f"Não foi possível abrir o RDWorks: {e}"
        msg = (f"Abrindo {os.path.basename(path)} no RDWorks "
               "(o Windows pode pedir permissão — o RDWorks roda como administrador).")
        msg += ("\nSe ele abrir vazio: Arquivo › Importar (Ctrl+I), Ctrl+V e Enter "
                "— o caminho do arquivo já está copiado.")
        return msg

    def _report_header(self) -> list[str]:
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
