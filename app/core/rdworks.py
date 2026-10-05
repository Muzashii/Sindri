"""Abrir o DXF exportado direto no RDWorks (Windows) — sem Qt.

O RDWorks não tem uma interface de linha de comando documentada; o que funciona é o mesmo que o
Windows faz ao abrir um arquivo associado: executar ``RDWorksV8.exe "caminho\\arquivo.dxf"``.
Se a versão instalada ignorar o argumento, o programa ao menos abre e o caminho do arquivo fica
na área de transferência (Ctrl+I, Ctrl+V, Enter).
"""
from __future__ import annotations

import glob
import os
import subprocess
import sys
from typing import Iterable, Optional

EXE_NAMES = ("RDWorksV8.exe", "RDWorks.exe", "LaserWorks.exe", "LaserWorksV8.exe")


def _program_dirs() -> list[str]:
    out = []
    for var in ("ProgramFiles(x86)", "ProgramFiles", "ProgramW6432", "LOCALAPPDATA"):
        v = os.environ.get(var)
        if v and v not in out:
            out.append(v)
    drive = os.environ.get("SystemDrive", "C:") + os.sep
    out.append(drive)
    return out


def _from_registry() -> list[str]:
    """Programa associado aos projetos .rld (e App Paths), se houver."""
    if sys.platform != "win32":
        return []
    try:
        import winreg
    except ImportError:
        return []
    found = []

    def read(root, key, name=""):
        try:
            with winreg.OpenKey(root, key) as k:
                return winreg.QueryValueEx(k, name)[0]
        except OSError:
            return None

    for ext in (".rld", ".RLD"):
        prog = read(winreg.HKEY_CLASSES_ROOT, ext)
        if prog:
            cmd = read(winreg.HKEY_CLASSES_ROOT, rf"{prog}\shell\open\command")
            if cmd:
                exe = cmd.split('"')[1] if cmd.startswith('"') else cmd.split(" ")[0]
                found.append(exe)
    for name in EXE_NAMES:
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            v = read(root, rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{name}")
            if v:
                found.append(v.strip('"'))
    return found


def candidates() -> list[str]:
    """Lugares onde o RDWorks costuma ser instalado (sem repetir)."""
    paths = list(_from_registry())
    for base in _program_dirs():
        for pattern in ("*RDWorks*", "*RdWorks*", "*LaserWorks*", "*Ruida*", "*RDCAM*"):
            for d in glob.glob(os.path.join(base, pattern)):
                for name in EXE_NAMES:
                    paths.append(os.path.join(d, name))
                paths += glob.glob(os.path.join(d, "*", "RDWorks*.exe"))
                paths += glob.glob(os.path.join(d, "RDWorks*.exe"))
    out, seen = [], set()
    for p in paths:
        k = os.path.normcase(os.path.abspath(p))
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out


def find_rdworks(saved: Optional[str] = None, extra: Iterable[str] = ()) -> Optional[str]:
    for p in [saved, *extra, *candidates()]:
        if p and os.path.isfile(p):
            return p
    return None


def files_to_open(files: list[str]) -> list[str]:
    """Qual DXF abrir: o arquivo com todas as placas (se gerado), senão a primeira placa."""
    dxfs = [f for f in files if f.lower().endswith(".dxf")]
    combined = [f for f in dxfs if f.lower().endswith("_todas_placas.dxf")]
    return combined or dxfs[:1]


def launch(exe: str, path: str) -> subprocess.Popen:
    """Abre o RDWorks com o arquivo. Diretório de trabalho = pasta do programa (ele lê configs de lá)."""
    flags = 0
    if sys.platform == "win32":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return subprocess.Popen([exe, os.path.abspath(path)], cwd=os.path.dirname(exe) or None,
                            creationflags=flags, close_fds=True)
