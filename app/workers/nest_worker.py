"""Execução do encaixe em segundo plano (a interface nunca congela).

A thread apenas coordena; o trabalho pesado roda em processos separados
(um por núcleo) dentro de `GeneticNester`.
"""
from __future__ import annotations

import threading
import traceback

from PySide6.QtCore import QThread, Signal

from ..core.models import NestParams, NestResult, Part, Placement
from ..core.optimizer import GeneticNester


class NestWorker(QThread):
    bestFound = Signal(object)      # NestResult
    progress = Signal(dict)
    failed = Signal(str)
    ready = Signal(object)          # informações iniciais (peças grandes demais etc.)

    def __init__(self, parts: list[Part], params: NestParams, locked: list[Placement],
                 workers: int | None = None, parent=None):
        super().__init__(parent)
        self.parts = parts
        self.params = params
        self.locked = locked
        self.workers = workers
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.result: NestResult | None = None

    def run(self):
        try:
            gn = GeneticNester(self.parts, self.params, locked=self.locked, workers=self.workers)
            self.ready.emit({"too_big": sorted({pid for pid, _ in gn.too_big}),
                             "instances": len(gn.instances)})
            self.result = gn.run(on_best=self._best, on_progress=self.progress.emit,
                                 stop_event=self.stop_event, pause_event=self.pause_event)
        except Exception as e:  # nunca derrubar a interface
            self.failed.emit(f"{e}\n\n{traceback.format_exc()}")

    def _best(self, res: NestResult):
        self.bestFound.emit(res)

    def stop(self):
        self.stop_event.set()
        self.pause_event.clear()

    def set_paused(self, paused: bool):
        if paused:
            self.pause_event.set()
        else:
            self.pause_event.clear()
