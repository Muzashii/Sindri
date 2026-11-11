"""Atualização automática a partir do GitHub (sem Qt).

Ao abrir, o Sindri pergunta ao GitHub qual é a maior versão marcada (tag ``vMAIOR.MENOR.CORREÇÃO``) e
compara com a versão instalada (arquivo ``versao.txt`` na pasta do programa — ``sha`` ou ``sha tag`` —,
ou o ``git`` se a pasta for um clone). Só o que o dono do repositório marcou com uma tag chega aos
computadores do laboratório; um commit qualquer enviado ao ``main`` não é mais instalado sozinho.

Reserva: enquanto o repositório não tiver nenhuma tag ``v*``, vale o comportamento antigo (último commit
do ``main``), para nenhuma instalação ficar sem atualização até a primeira tag ser publicada.

Se houver versão nova, baixa o .zip do commit, confere o conteúdo, guarda uma cópia dos arquivos que
serão trocados em ``_backup_atualizacao`` e copia os novos por cima. As pastas ``.venv`` e ``libs``
(bibliotecas instaladas) e os arquivos do usuário nunca são tocados.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
import tempfile
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Callable, Optional

from .fileutil import log_exception

REPO = "Muzashii/Sindri"
BRANCH = "main"
API_URL = f"https://api.github.com/repos/{REPO}/commits/{BRANCH}"
TAGS_URL = f"https://api.github.com/repos/{REPO}/tags"
COMPARE_URL = "https://api.github.com/repos/" + REPO + "/compare/{base}...{head}"
TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
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
    tag: str = ""              # "v1.4.0" quando a versão vem de uma tag; vazio = ponta do main (reserva)

    @property
    def short(self) -> str:
        return self.sha[:7]

    @property
    def number(self) -> str:
        """'1.4.0' (sem o v) ou vazio quando não há tag."""
        return self.tag[1:] if parse_tag(self.tag) else ""


def parse_tag(name: Optional[str]) -> Optional[tuple[int, int, int]]:
    """'v1.10.0' -> (1, 10, 0); qualquer outro formato (v1.2, 1.2.3, exe-7, v1.2.3-rc1) -> None."""
    m = TAG_RE.match((name or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def app_root() -> str:
    """Pasta onde estão sindri.py, executar.bat e a pasta app/."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def is_git_checkout(root: str) -> bool:
    return os.path.isdir(os.path.join(root, ".git"))


def _version_file(root: str) -> list[str]:
    try:
        with open(os.path.join(root, VERSION_FILE), encoding="utf-8") as fh:
            return fh.read().split()
    except OSError:
        return []


def local_version(root: Optional[str] = None) -> Optional[str]:
    """sha instalado: do ``git`` num clone, senão a 1ª palavra do ``versao.txt`` ("sha" ou "sha tag")."""
    root = root or app_root()
    if is_git_checkout(root):
        try:
            out = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"], capture_output=True, text=True,
                                 timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if out.returncode == 0:
                return out.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            log_exception("atualizador: git rev-parse (usa o versao.txt)")
    words = _version_file(root)
    return words[0] if words else None


def local_tag(root: Optional[str] = None) -> str:
    """Tag instalada ("v1.4.0"), guardada como 2ª palavra do ``versao.txt``; vazio se não houver."""
    words = _version_file(root or app_root())
    return words[1] if len(words) > 1 and parse_tag(words[1]) else ""


def _get_json(url: str, timeout: float):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def latest_main(timeout: float = 6.0) -> RemoteVersion:
    """Último commit do ``main`` (comportamento antigo, usado só enquanto não existir tag ``v*``)."""
    data = _get_json(API_URL, timeout)
    c = data.get("commit", {})
    msg = (c.get("message") or "").strip().splitlines()
    return RemoteVersion(sha=data["sha"], date=(c.get("committer") or {}).get("date", ""),
                         message=msg[0] if msg else "")


def latest_tag(timeout: float = 6.0) -> Optional[RemoteVersion]:
    """Maior tag ``vMAIOR.MENOR.CORREÇÃO`` do repositório (comparação numérica), ou None se não há nenhuma.

    Lê as 100 tags mais recentes da API; tags fora do formato (``exe-7``, ``v1.2``, ``v1.2.3-rc1``) são
    ignoradas."""
    data = _get_json(TAGS_URL + "?per_page=100", timeout)
    best = None
    for t in data if isinstance(data, list) else []:
        if not isinstance(t, dict):
            continue
        key, sha = parse_tag(t.get("name")), (t.get("commit") or {}).get("sha")
        if key is not None and sha and (best is None or key > best[0]):
            best = (key, t["name"].strip(), sha)
    if best is None:
        return None
    return RemoteVersion(sha=best[2], date="", message="", tag=best[1])


def latest_version(timeout: float = 6.0) -> RemoteVersion:
    """Versão a oferecer: a maior tag; sem nenhuma tag ``v*`` no repositório, a ponta do ``main``."""
    return latest_tag(timeout) or latest_main(timeout)


def _installed_is_ahead(tag_sha: str, local_sha: str, timeout: float) -> bool:
    """A instalação (sem tag no versao.txt, ou clone git) já contém o commit da tag e está à frente dele?

    Pergunta ao GitHub (``compare``). Se o GitHub não conhece o commit instalado (404/422), não dá para
    afirmar que está à frente: a tag é oferecida. Outros erros de rede sobem."""
    try:
        data = _get_json(COMPARE_URL.format(base=tag_sha, head=local_sha), timeout)
    except urllib.error.HTTPError as e:
        if e.code in (404, 422):
            return False
        raise
    return isinstance(data, dict) and data.get("status") in ("ahead", "identical")


def update_available(timeout: float = 6.0, root: Optional[str] = None) -> Optional[RemoteVersion]:
    """Versão nova no GitHub, ou None se já está atualizado. Erros de rede sobem como exceção.

    Nunca rebaixa: se a instalada é igual ou mais nova que a maior tag, não oferece nada."""
    remote = latest_version(timeout)
    root = root or app_root()
    local = local_version(root)
    if local and local == remote.sha:
        return None
    wanted = parse_tag(remote.tag)
    if wanted is None:                       # reserva (sem tags): como sempre foi
        return remote
    have = None if is_git_checkout(root) else parse_tag(local_tag(root))
    if have is not None:
        return remote if wanted > have else None
    if local and _installed_is_ahead(remote.sha, local, timeout):
        return None                          # instalada pelo main (ou clone de desenvolvimento) já passou da tag
    return remote


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


def apply_zip(data: bytes, root: str, sha: str, tag: str = "") -> dict:
    """Prepara e faz backup antes de trocar; reverte todas as trocas se ocorrer falha.

    ``versao.txt`` fica com "sha" (instalação pela ponta do main) ou "sha tag" (versão marcada)."""
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
                     "build_exe.bat", "dxfnest.py", "requirements-build.txt"}
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
            stamp_line = f"{sha} {tag}" if parse_tag(tag) else sha
            payloads.append((VERSION_FILE, (stamp_line + "\n").encode("utf-8")))
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
                json.dump({"version": sha, "tag": tag, "files": [{"path": r, "existed": old is not None}
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
    return apply_zip(data, root, remote.sha, remote.tag)


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
    """Somente a distribuição por pasta usa troca de arquivos; wheel/exe têm outro instalador."""
    root = app_root()
    return (not getattr(sys, "frozen", False) and os.path.isfile(os.path.join(root, "sindri.py"))
            and os.path.isfile(os.path.join(root, "executar.bat")))
