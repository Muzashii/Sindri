"""Build Windows isolado de DLLs de outras ferramentas presentes no PATH."""
import argparse
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distpath", default="dist", help="Pasta de saída do executável")
    args = parser.parse_args()
    if sys.platform != "win32":
        raise SystemExit("Este build deve ser executado no Windows.")
    import PySide6
    import shiboken6
    root = Path(__file__).resolve().parent.parent
    qt = Path(PySide6.__file__).parent
    shiboken = Path(shiboken6.__file__).parent
    windows = Path(os.environ["SystemRoot"])
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join(map(str, [Path(sys.executable).parent, qt, shiboken,
                                           Path(sys.base_prefix), Path(sys.base_prefix) / "DLLs",
                                           windows / "System32", windows]))
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
               "--distpath", args.distpath,
               "--name", "Sindri", "--icon", str(root / "assets/sindri.ico"),
               "--collect-data", "ezdxf", "--collect-submodules", "ezdxf",
               "--hidden-import", "pyclipper", "--hidden-import", "shapely",
               "--exclude-module", "tkinter", "--exclude-module", "matplotlib"]
    # O Python pode incluir uma versão de VC Runtime anterior à exigida pelo Qt.
    for name in ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll", "msvcp140_1.dll",
                 "msvcp140_2.dll", "msvcp140_codecvt_ids.dll", "concrt140.dll"):
        path = qt / name
        if path.is_file():
            command.extend(["--add-binary", f"{path};."])
    command.append(str(root / "sindri.py"))
    return subprocess.call(command, cwd=root, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
