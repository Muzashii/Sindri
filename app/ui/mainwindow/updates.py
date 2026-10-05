"""Procurar e instalar atualizações do GitHub (sem travar a tela)."""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QMessageBox

from ...core import updater
from ..prefs import get_bool, set_bool


class _Bridge(QObject):
    """Leva o resultado da thread de rede para a thread da interface."""
    checked = Signal(object, object, bool)     # versão nova (ou None), erro (ou None), silencioso?
    installed = Signal(object, object)         # resumo (ou None), erro (ou None)
    progress = Signal(int)


class UpdatesMixin:
    def _upd_bridge(self) -> _Bridge:
        if not hasattr(self, "_updb"):
            self._updb = _Bridge(self)
            self._updb.checked.connect(self._update_checked)
            self._updb.installed.connect(self._update_installed)
            self._updb.progress.connect(
                lambda n: self.statusBar().showMessage(f"Baixando atualização… {n / 1024:.0f} KB"))
        return self._updb

    def check_updates(self, silent: bool = True):
        """Pergunta ao GitHub se há versão nova (em segundo plano). Silencioso = só avisa se houver."""
        if not updater.can_self_update():
            if not silent:
                QMessageBox.information(self, "Atualizações",
                                        "Esta versão (.exe) não se atualiza sozinha. Baixe a nova no GitHub.")
            return
        if silent and not get_bool("update/auto_check", True):
            return
        b = self._upd_bridge()
        if not silent:
            self.statusBar().showMessage("Procurando atualizações…", 4000)

        def work():
            try:
                b.checked.emit(updater.update_available(), None, silent)
            except Exception as e:               # sem internet / GitHub fora: nunca atrapalha
                b.checked.emit(None, e, silent)

        threading.Thread(target=work, daemon=True).start()

    def _update_checked(self, remote, error, silent: bool):
        if error is not None:
            if not silent:
                QMessageBox.warning(self, "Atualizações", f"Não foi possível consultar o GitHub:\n{error}")
            return
        if remote is None:
            if not silent:
                QMessageBox.information(self, "Atualizações", "Você já está com a versão mais recente do Sindri.")
            return
        self._pending_update = remote
        when = remote.date[:10].split("-")
        when = f"{when[2]}/{when[1]}" if len(when) == 3 else ""
        msg = remote.message.replace("<", "&lt;")[:90]
        self.show_banner(f"<b>Nova versão do Sindri</b> ({when} · {msg}). "
                         '<a href="update:">Atualizar agora</a> · <a href="later:">Depois</a>', "warn")

    def install_pending_update(self):
        remote = getattr(self, "_pending_update", None)
        if remote is None:
            return
        if self.worker is not None:
            QMessageBox.information(self, "Atualizar", "Pare o encaixe antes de atualizar.")
            return
        self.banner.hide()
        self.statusBar().showMessage("Baixando atualização…")
        b = self._upd_bridge()

        def work():
            try:
                b.installed.emit(updater.install_update(remote, progress=b.progress.emit), None)
            except Exception as e:
                b.installed.emit(None, e)

        threading.Thread(target=work, daemon=True).start()

    def _update_installed(self, summary, error):
        if error is not None:
            QMessageBox.warning(self, "Atualizar", f"A atualização não foi instalada:\n{error}\n\n"
                                "Nada foi alterado. Tente de novo mais tarde.")
            self.statusBar().clearMessage()
            return
        n = len(summary.get("changed", []))
        r = QMessageBox.question(self, "Sindri atualizado",
                                 f"Atualização instalada ({n} arquivo(s)).\n"
                                 "Reiniciar o Sindri agora para usar a versão nova?",
                                 QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if r == QMessageBox.Yes:
            self._restart_after_close = True
            self.close()                         # pergunta se quer salvar, como sempre
            if self.isVisible():                 # fechamento cancelado
                self._restart_after_close = False
                self.show_banner("Atualização instalada — reinicie o Sindri para usar a versão nova.", "ok")
        else:
            self.show_banner("Atualização instalada — ela vale na próxima vez que abrir o Sindri.", "ok")

    def toggle_auto_update(self, on: bool):
        set_bool("update/auto_check", on)
