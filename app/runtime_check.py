"""Verifica as versões mínimas antes de iniciar ou dispensar a instalação."""
import importlib
from importlib.metadata import version
import re

MINIMUM = {"numpy": (1, 26), "shapely": (2, 0, 4), "pyclipper": (1, 3),
           "ezdxf": (1, 1), "pypdf": (4, 0), "PySide6": (6, 6)}


def check():
    for name, minimum in MINIMUM.items():
        installed = tuple(int(x) for x in re.match(r"\d+(?:\.\d+)*", version(name)).group().split("."))
        if installed < minimum or (name == "numpy" and installed >= (3,)):
            raise ImportError(f"Versão incompatível de {name}: {version(name)}; mínimo {'.'.join(map(str, minimum))}.")
    # Qt antes das bibliotecas nativas de geometria, como no lançador.
    for name in ("PySide6.QtWidgets", "PySide6.QtWebEngineWidgets", "numpy", "shapely.geometry", "pyclipper", "ezdxf", "pypdf"):
        importlib.import_module(name)


if __name__ == "__main__":
    check()
