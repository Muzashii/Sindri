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
            exe = exe_from_command(cmd) if cmd else None
            if exe:
                found.append(exe)
    for name in EXE_NAMES:
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            v = read(root, rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{name}")
            if v:
                found.append(os.path.expandvars(str(v)).strip('"'))
    return found


def exe_from_command(cmd: str):
    """'"C:\\Program Files (x86)\\RDWorksV8\\RDWorksV8.exe" "%1"' (com ou sem aspas) -> caminho do .exe."""
    import re
    m = re.match(r'\s*"?([^"]+?\.exe)', os.path.expandvars(cmd or ""), re.IGNORECASE)
    return m.group(1) if m else None


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
    """Qual DXF abrir: o arquivo com todas as placas de todos os materiais (o de nome mais curto,
    sem a etiqueta do material), senão a primeira placa."""
    dxfs = [f for f in files if f.lower().endswith(".dxf")]
    combined = sorted((f for f in dxfs if f.lower().endswith("_todas_placas.dxf")),
                      key=lambda f: len(os.path.basename(f)))
    return combined[:1] or dxfs[:1]


def _shell_execute(exe: str, path: str) -> None:
    """Abre pelo Windows (ShellExecute): mostra o pedido de permissão (UAC) quando o programa exige
    administrador — o RDWorks exige, e por isso subprocess dá 'WinError 740 ... requer elevação'."""
    import ctypes
    from ctypes import wintypes
    fn = ctypes.windll.shell32.ShellExecuteW
    fn.restype = ctypes.c_void_p
    fn.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                   ctypes.c_int]
    target = os.path.abspath(path)
    try:                                  # RDWorks é um programa antigo: caminho curto (8.3) evita acentos
        buf = ctypes.create_unicode_buffer(1024)
        if ctypes.windll.kernel32.GetShortPathNameW(target, buf, 1024):
            target = buf.value
    except Exception:
        pass
    r = fn(None, "open", exe, f'"{target}"', os.path.dirname(exe) or None, 1) or 0
    if r <= 32:
        if r == 5:      # acesso negado: o usuário recusou o pedido de permissão
            raise OSError("o pedido de permissão do Windows foi recusado")
        raise OSError(f"o Windows não conseguiu abrir o programa (código {r})")


def launch(exe: str, path: str) -> None:
    """Abre o RDWorks com o arquivo. Diretório de trabalho = pasta do programa (ele lê configs de lá)."""
    if sys.platform == "win32":
        _shell_execute(exe, path)
        return
    subprocess.Popen([exe, os.path.abspath(path)], cwd=os.path.dirname(exe) or None, close_fds=True)
