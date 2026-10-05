"""Atualização automática a partir do GitHub (sem Qt).

Ao abrir, o Sindri pergunta ao GitHub qual é o último commit da branch principal e compara com a
versão instalada (arquivo ``versao.txt`` na pasta do programa, ou o ``git`` se a pasta for um clone).
Se houver versão nova, baixa o .zip do commit, confere o conteúdo, guarda uma cópia dos arquivos que
serão trocados em ``_backup_atualizacao`` e copia os novos por cima. As pastas ``.venv`` e ``libs``
(bibliotecas instaladas) e os arquivos do usuário nunca são tocados.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from dataclasses import dataclass
from typing import Callable, Optional

REPO = "Muzashii/Sindri"
BRANCH = "main"
API_URL = f"https://api.github.com/repos/{REPO}/commits/{BRANCH}"
ZIP_URL = "https://codeload.github.com/" + REPO + "/zip/{sha}"
VERSION_FILE = "versao.txt"
BACKUP_DIR = "_backup_atualizacao"
# nunca sobrescrever/apagar: ambiente Python, bibliotecas, dados locais
PROTECTED = {".venv", "libs", ".git", BACKUP_DIR, VERSION_FILE, "sindri_erro.log"}
UA = {"User-Agent": "Sindri-updater", "Accept": "application/vnd.github+json"}


@dataclass
class RemoteVersion:
    sha: str
    date: str
    message: str

    @property
    def short(self) -> str:
        return self.sha[:7]


def app_root() -> str:
    """Pasta onde estão sindri.py, executar.bat e a pasta app/."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def is_git_checkout(root: str) -> bool:
    return os.path.isdir(os.path.join(root, ".git"))


def local_version(root: Optional[str] = None) -> Optional[str]:
    root = root or app_root()
    if is_git_checkout(root):
        try:
            out = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"], capture_output=True, text=True,
                                 timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if out.returncode == 0:
                return out.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        with open(os.path.join(root, VERSION_FILE), encoding="utf-8") as fh:
            return fh.read().strip().split()[0] or None
    except (OSError, IndexError):
        return None


def latest_version(timeout: float = 6.0) -> RemoteVersion:
    req = urllib.request.Request(API_URL, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
    c = data.get("commit", {})
    msg = (c.get("message") or "").strip().splitlines()
    return RemoteVersion(sha=data["sha"], date=(c.get("committer") or {}).get("date", ""),
                         message=msg[0] if msg else "")


def update_available(timeout: float = 6.0, root: Optional[str] = None) -> Optional[RemoteVersion]:
    """Versão nova no GitHub, ou None se já está atualizado. Erros de rede sobem como exceção."""
    remote = latest_version(timeout)
    local = local_version(root)
    return None if local and local == remote.sha else remote


def _download(url: str, progress: Optional[Callable[[int], None]] = None, timeout: float = 30.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA["User-Agent"]})
    buf = io.BytesIO()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        while True:
            chunk = r.read(64 * 1024)
            if not chunk:
                break
            buf.write(chunk)
            if progress:
                progress(buf.tell())
    return buf.getvalue()


def _safe_members(z: zipfile.ZipFile) -> tuple[str, list[zipfile.ZipInfo]]:
    """Confere o .zip: uma pasta raiz, sem caminhos perigosos, e com os arquivos do Sindri."""
    names = [i.filename for i in z.infolist()]
    top = names[0].split("/")[0] + "/"
    members = []
    for info in z.infolist():
        n = info.filename
        if not n.startswith(top) or ".." in n.split("/") or n.startswith("/") or ":" in n:
            raise ValueError(f"arquivo inesperado no pacote: {n}")
        if not info.is_dir():
            members.append(info)
    rel = {m.filename[len(top):] for m in members}
    if "sindri.py" not in rel or "app/main.py" not in rel:
        raise ValueError("o pacote baixado não parece ser o Sindri")
    return top, members


def apply_zip(data: bytes, root: str, sha: str) -> dict:
    """Instala o conteúdo do .zip na pasta do programa. Devolve um resumo."""
    z = zipfile.ZipFile(io.BytesIO(data))
    top, members = _safe_members(z)
    backup = os.path.join(root, BACKUP_DIR)
    shutil.rmtree(backup, ignore_errors=True)
    changed, reqs_changed = [], False
    for info in members:
        rel = info.filename[len(top):]
        first = rel.split("/")[0]
        if first in PROTECTED or not rel:
            continue
        dest = os.path.join(root, *rel.split("/"))
        new = z.read(info)
        try:
            with open(dest, "rb") as fh:
                if fh.read() == new:
                    continue                      # igual: não mexe
            os.makedirs(os.path.dirname(os.path.join(backup, *rel.split("/"))), exist_ok=True)
            shutil.copy2(dest, os.path.join(backup, *rel.split("/")))
        except FileNotFoundError:
            pass
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        tmp = dest + ".novo"
        with open(tmp, "wb") as fh:
            fh.write(new)
        os.replace(tmp, dest)
        changed.append(rel)
        if rel in ("requirements.txt", "executar.bat"):
            reqs_changed = True
    with open(os.path.join(root, VERSION_FILE), "w", encoding="utf-8") as fh:
        fh.write(sha + "\n")
    return {"changed": changed, "needs_setup": reqs_changed, "backup": backup}


def install_update(remote: RemoteVersion, root: Optional[str] = None,
                   progress: Optional[Callable[[int], None]] = None) -> dict:
    """Baixa e instala a versão ``remote``. Num clone git usa ``git pull`` (não mistura os dois jeitos)."""
    root = root or app_root()
    if is_git_checkout(root):
        out = subprocess.run(["git", "-C", root, "pull", "--ff-only"], capture_output=True, text=True,
                             timeout=120, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if out.returncode != 0:
            raise RuntimeError(out.stderr.strip() or out.stdout.strip() or "git pull falhou")
        return {"changed": ["(git pull)"], "needs_setup": True, "backup": ""}
    data = _download(ZIP_URL.format(sha=remote.sha), progress)
    return apply_zip(data, root, remote.sha)


def restart(root: Optional[str] = None) -> None:
    """Abre o Sindri de novo pelo executar.bat (que instala bibliotecas novas, se houver)."""
    root = root or app_root()
    bat = os.path.join(root, "executar.bat")
    if sys.platform == "win32" and os.path.isfile(bat):
        flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        subprocess.Popen(["cmd", "/c", "start", "", "/min", bat], cwd=root, creationflags=flags, close_fds=True)
    else:
        subprocess.Popen([sys.executable, os.path.join(root, "sindri.py")], cwd=root, close_fds=True)


def can_self_update() -> bool:
    """O .exe (PyInstaller) não se atualiza copiando os .py."""
    return not getattr(sys, "frozen", False)

