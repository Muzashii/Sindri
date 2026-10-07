"""Aba Caixa: gravar o DXF da caixa e mandar as peças para o encaixe."""
from __future__ import annotations

import os
from typing import Optional
from urllib.parse import quote

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFileDialog, QMessageBox

from ...core.boxgen import default_name, write_dxf
from ..dialogs import settings


def box_folder() -> str:
    """Pasta onde ficam os DXFs das caixas mandadas para o encaixe (ao lado de "Solicitações")."""
    from ...core.intranet import default_base_folder
    return os.path.join(os.path.dirname(default_base_folder()), "Caixas")


def unique_path(folder: str, stem: str, ext: str = ".dxf") -> str:
    path, k = os.path.join(folder, stem + ext), 2
    while os.path.exists(path):
        path = os.path.join(folder, f"{stem}_{k}{ext}")
        k += 1
    return path


class BoxMixin:
    def _ask_join_box(self) -> Optional[bool]:
        """Já há peças abertas: juntar a caixa a elas? True = juntar, False = substituir, None = cancelar."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("Enviar caixa para o encaixe")
        box.setText("Já há peças abertas na aba Encaixe.")
        box.setInformativeText("“Juntar” coloca as peças da caixa junto com as atuais (o encaixe atual é mantido "
                               "e as novas entram quando você encaixar de novo).\n"
                               "“Substituir” fecha o que está aberto e abre só a caixa.")
        b_add = box.addButton("Juntar", QMessageBox.AcceptRole)
        b_new = box.addButton("Substituir", QMessageBox.DestructiveRole)
        box.addButton("Cancelar", QMessageBox.RejectRole)
        box.setDefaultButton(b_add)
        box.exec()
        if box.clickedButton() == b_add:
            return True
        if box.clickedButton() == b_new:
            return False
        return None

    def send_box_to_nest(self) -> bool:
        bp = self.box_panel
        bp.regenerate()
        r = bp.result
        if r is None:
            return False
        add = False
        if self.files:
            add = self._ask_join_box()
            if add is None:
                return False
        p = r.params
        try:
            path = write_dxf(r, unique_path(box_folder(), default_name(p)))
        except OSError as e:
            QMessageBox.critical(self, "Caixa", f"Não foi possível gravar o DXF da caixa:\n{e}")
            return False
        key = os.path.abspath(path)
        if not self.load_files([path], add=add, multipliers={key: max(1, int(p.quantity))},
                               materials={key: p.material_name()}, keep_layout=add):
            return False
        self.set_mode(0)
        n = r.count() * max(1, int(p.quantity))
        what = f"{p.quantity} caixas" if p.quantity > 1 else "A caixa"
        self.show_banner(f"{what} ({n} peças de <b>{p.material_name()}</b>) "
                         f"{'foram' if p.quantity > 1 else 'foi'} para o encaixe. "
                         f"<a href='open:{quote(os.path.dirname(path))}'>Abrir pasta do DXF</a>", "ok")
        if add:
            self.statusBar().showMessage("Peças da caixa adicionadas. Clique em Encaixar para incluí-las nas placas.",
                                         10000)
        else:
            self._apply_material_preset()
            if not self.too_big:
                QTimer.singleShot(300, self.start_nest)
        return True

    def save_box_dxf(self) -> Optional[str]:
        bp = self.box_panel
        bp.regenerate()
        r = bp.result
        if r is None:
            return None
        st = settings()
        folder = st.value("box/last_dir", "") or os.path.expanduser("~")
        path, _ = QFileDialog.getSaveFileName(self, "Salvar DXF da caixa",
                                              os.path.join(folder, default_name(r.params) + ".dxf"),
                                              "Desenhos DXF (*.dxf)")
        if not path:
            return None
        if not path.lower().endswith(".dxf"):
            path += ".dxf"
        try:
            write_dxf(r, path)
        except OSError as e:
            QMessageBox.critical(self, "Caixa", f"Não foi possível gravar o DXF:\n{e}")
            return None
        st.setValue("box/last_dir", os.path.dirname(path))
        bp.show_message(f"DXF salvo: <b>{os.path.basename(path)}</b> — "
                        f"<a href='open:{quote(os.path.dirname(path))}'>abrir pasta</a>", "ok")
        return path
