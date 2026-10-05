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
import tempfile
import uuid
from datetime import datetime, timezone
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
    if not names or len(names) > 10000 or sum(i.file_size for i in z.infolist()) > 200 * 1024 * 1024:
        raise ValueError("Pacote vazio ou maior que o limite de atualização.")
    top = names[0].split("/")[0] + "/"
    members = []
    for info in z.infolist():
        n = info.filename
        if not n.startswith(top) or ".." in n.split("/") or n.startswith("/") or ":" in n or "\\" in n:
            raise ValueError(f"arquivo inesperado no pacote: {n}")
        if not info.is_dir():
            members.append(info)
    rel = {m.filename[len(top):] for m in members}
    if "sindri.py" not in rel or "app/main.py" not in rel:
        raise ValueError("o pacote baixado não parece ser o Sindri")
    return top, members


def apply_zip(data: bytes, root: str, sha: str) -> dict:
    """Prepara e faz backup antes de trocar; reverte todas as trocas se ocorrer falha."""
    root = os.path.realpath(root)
    lock_path = os.path.join(root, ".sindri-update.lock")
    try:
        lock = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as e:
        raise RuntimeError("Há outra atualização em andamento (arquivo .sindri-update.lock).") from e
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
    backup = os.path.join(root, BACKUP_DIR, stamp)
    allowed_files = {"sindri.py", "executar.bat", "requirements.txt", "pyproject.toml", "readme.md",
                     "license", "decisions.md", "instalar.bat", "reparar.bat", "requirements-compat.txt",
                     "build_exe.bat", "dxfnest.py"}
    changed, applied = [], []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z, tempfile.TemporaryDirectory(prefix=".sindri-stage-", dir=root) as stage:
            top, members = _safe_members(z)
            payloads, seen = [], set()
            for info in members:
                rel = info.filename[len(top):]
                first = rel.split("/")[0].casefold()
                if first in {p.casefold() for p in PROTECTED}:
                    continue
                if first not in {"app", "assets"} and rel.casefold() not in allowed_files:
                    continue
                if rel.casefold() in seen or any(p.rstrip(" .") != p for p in rel.split("/")):
                    raise ValueError(f"Caminho duplicado ou ambíguo no pacote: {rel}")
                seen.add(rel.casefold())
                dest = os.path.realpath(os.path.join(root, *rel.split("/")))
                if os.path.commonpath([root, dest]) != root:
                    raise ValueError(f"Destino fora da instalação: {rel}")
                payloads.append((rel, z.read(info)))  # CRC e leitura antes da primeira troca
            payloads.append((VERSION_FILE, (sha + "\n").encode("utf-8")))
            plan = []
            for rel, new in payloads:
                dest = os.path.join(root, *rel.split("/"))
                previous = None
                if os.path.isfile(dest):
                    with open(dest, "rb") as fh:
                        if fh.read() == new:
                            continue
                    previous = os.path.join(backup, *rel.split("/"))
                    os.makedirs(os.path.dirname(previous), exist_ok=True)
                    shutil.copy2(dest, previous)
                staged = os.path.join(stage, *rel.split("/"))
                os.makedirs(os.path.dirname(staged), exist_ok=True)
                with open(staged, "wb") as fh:
                    fh.write(new)
                plan.append((rel, dest, staged, previous))
            os.makedirs(backup, exist_ok=True)
            with open(os.path.join(backup, "transaction.json"), "w", encoding="utf-8") as fh:
                json.dump({"version": sha, "files": [{"path": r, "existed": old is not None}
                                                      for r, _, _, old in plan]}, fh, indent=2)
            try:
                for rel, dest, staged, previous in plan:
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    os.replace(staged, dest)
                    applied.append((dest, previous))
                    if rel != VERSION_FILE:
                        changed.append(rel)
            except Exception as e:
                failures = []
                for dest, previous in reversed(applied):
                    try:
                        if previous is None:
                            os.remove(dest)
                        else:
                            restore = os.path.join(stage, "restore-" + uuid.uuid4().hex)
                            shutil.copy2(previous, restore)
                            os.replace(restore, dest)
                    except OSError as rollback_error:
                        failures.append(str(rollback_error))
                if failures:
                    raise RuntimeError(f"Atualização falhou e a restauração ficou incompleta. Backup: {backup}. "
                                       + "; ".join(failures)) from e
                raise RuntimeError(f"Atualização falhou; arquivos anteriores restaurados. Backup: {backup}. {e}") from e
        return {"changed": changed, "needs_setup": bool(set(changed) & {"requirements.txt", "executar.bat"}),
                "backup": backup}
    finally:
        os.close(lock)
        os.remove(lock_path)


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
