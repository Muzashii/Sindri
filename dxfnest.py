"""Mantido só para atalhos antigos ("DXF Nest"): abre o Sindri."""
import os
import runpy
import sys

if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    here = os.path.dirname(os.path.abspath(__file__))
    sys.argv[0] = os.path.join(here, "sindri.py")
    runpy.run_path(sys.argv[0], run_name="__main__")
