"""Otimização por algoritmo genético (como no SVGnest/Deepnest).

Indivíduo = lista de genes (instância, rotação, espelhado) + critério de posicionamento.
Fitness (menor é melhor) vem do decodificador (placement.Decoder):
    100·(peças sem lugar) + 2·(placas extras) + área ocupada na última placa ...

A avaliação roda em paralelo (um processo por núcleo). Cada processo mantém seu
próprio cache de NFP. Este módulo NÃO importa Qt.
"""
from __future__ import annotations

import math
import multiprocessing as mp
import os
import random
import threading
import time
from concurrent.futures import ProcessPoolExecutor, FIRST_COMPLETED, wait
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np
from shapely import affinity

from .models import NestParams, NestResult, Part, Placement
from .nfp import PartShape, round_rot
from .placement import Decoder

Gene = tuple[int, float, bool]          # (índice da instância, rotação, espelhado)


@dataclass(frozen=True)
class Individual:
    genes: tuple[Gene, ...]
    criterion: str = "bbox"

    def key(self):
        return (self.genes, self.criterion)


def part_rotations(part: Part, params: NestParams) -> list[float]:
    if part.rotation_locked:
        return [0.0]
    return params.rotations()


def shapes_from_parts(parts: list[Part], params: NestParams) -> dict[str, PartShape]:
    shapes = {}
    for p in parts:
        outer = np.asarray(p.outer.exterior.coords)[:-1]
        holes = [np.asarray(h.exterior.coords)[:-1] for h in p.holes]
        net = p.outer.area - sum(h.area for h in p.holes)
        shapes[p.id] = PartShape(p.id, outer, holes, net, part_rotations(p, params), p.material)
    return shapes


# ---------------------------------------------------------------------------
# Avaliação (processo de trabalho)
# ---------------------------------------------------------------------------
_W: dict = {}


def _worker_init(shapes, params, locked, instances, cache_state=None):
    _W["decoder"] = Decoder(shapes, params)
    if cache_state is not None:
        _W["decoder"].cache.import_state(cache_state)
    _W["locked"] = locked
    _W["instances"] = instances


def _decode(decoder: Decoder, ind: Individual, instances, locked):
    order = [instances[g[0]] for g in ind.genes]
    rots = [g[1] for g in ind.genes]
    mirrors = [g[2] for g in ind.genes]
    return decoder.decode(order, rots, mirrors, ind.criterion, locked)


def _worker_eval(ind: Individual):
    res = _decode(_W["decoder"], ind, _W["instances"], _W["locked"])
    return ind, res


# ---------------------------------------------------------------------------
class GeneticNester:
    def __init__(self, parts: list[Part], params: NestParams,
                 locked: Optional[list[Placement]] = None, seed: Optional[int] = None,
                 workers: Optional[int] = None):
        self.params = params
        self.parts = {p.id: p for p in parts}
        self.locked = list(locked or [])
        self.rng = random.Random(seed)
        self.shapes = shapes_from_parts(parts, params)
        self.decoder = Decoder(self.shapes, params)
        self.workers = (max(1, (os.cpu_count() or 2) - 1) if workers is None else workers)

        locked_keys = {(lp.part_id, lp.instance) for lp in self.locked}
        mirror_opts = (False, True) if params.allow_mirror else (False,)
        self.too_big: list[tuple[str, int]] = []
        self.instances: list[tuple[str, int]] = []
        for p in sorted(parts, key=lambda q: -q.area):
            fits = self.decoder.fits_sheet(p.id, self.shapes[p.id].rotations, mirror_opts)
            for i in range(max(0, int(p.quantity))):
                if (p.id, i) in locked_keys:
                    continue
                if not fits:
                    self.too_big.append((p.id, i))
                else:
                    self.instances.append((p.id, i))
        self.best: Optional[NestResult] = None
        self.best_ind: Optional[Individual] = None
        self.generation = 0
        self.evaluated = 0
        self._seen: dict = {}

    # ------------------------------------------------------------------
    def _best_rotation(self, pid: str) -> float:
        rots = self.shapes[pid].rotations
        poly = self.parts[pid].outer
        best, best_key = rots[0], None
        for r in rots:
            g = affinity.rotate(poly, r, origin=(0, 0))
            x0, y0, x1, y1 = g.bounds
            k = (round((x1 - x0) * (y1 - y0), 1), round(y1 - y0, 1))
            if best_key is None or k < best_key:
                best, best_key = r, k
        return best

    def first_individual(self) -> Individual:
        rot_of = {pid: self._best_rotation(pid) for pid in {i[0] for i in self.instances}}
        genes = tuple((k, rot_of[self.instances[k][0]], False) for k in range(len(self.instances)))
        return Individual(genes, "bbox")

    def mutate(self, ind: Individual, rate: Optional[float] = None) -> Individual:
        rate = self.params.mutation_rate if rate is None else rate
        genes = list(ind.genes)
        n = len(genes)
        for i in range(n):
            if self.rng.random() < rate and n > 1:
                j = i + 1 if i + 1 < n else i - 1
                genes[i], genes[j] = genes[j], genes[i]
            if self.rng.random() < rate:
                k, r, m = genes[i]
                rots = self.shapes[self.instances[k][0]].rotations
                genes[i] = (k, self.rng.choice(rots), m)
            if self.params.allow_mirror and self.rng.random() < rate:
                k, r, m = genes[i]
                genes[i] = (k, r, not m)
        crit = ind.criterion
        if self.rng.random() < rate:
            crit = "left" if crit == "bbox" else "bbox"
        return Individual(tuple(genes), crit)

    def crossover(self, a: Individual, b: Individual) -> Individual:
        n = len(a.genes)
        if n < 2:
            return a
        i, j = sorted(self.rng.sample(range(n + 1), 2))
        seg = a.genes[i:j]
        used = {g[0] for g in seg}
        rest = [g for g in b.genes if g[0] not in used]
        genes = tuple(rest[:i]) + tuple(seg) + tuple(rest[i:])
        crit = a.criterion if self.rng.random() < 0.5 else b.criterion
        return Individual(genes, crit)

    def tournament(self, scored: list[tuple[float, Individual]], k: int = 3) -> Individual:
        cand = self.rng.sample(scored, min(k, len(scored)))
        return min(cand, key=lambda t: t[0])[1]

    # ------------------------------------------------------------------
    def _to_result(self, res, ind: Individual) -> NestResult:
        return NestResult(res.placements, res.sheets_used, res.utilization, res.fitness,
                          list(res.unplaced) + list(self.too_big), self.generation, self.evaluated,
                          list(res.sheet_materials))

    def _consider(self, ind: Individual, res, on_best) -> None:
        self.evaluated += 1
        self._seen[ind.key()] = res.fitness
        if self.best is None or res.fitness < self.best.fitness - 1e-9:
            self.best = self._to_result(res, ind)
            self.best_ind = ind
            if on_best:
                on_best(self.best)

    def evaluate_local(self, ind: Individual):
        return _decode(self.decoder, ind, self.instances, self.locked)

    def initial_population(self) -> list[Individual]:
        first = self.first_individual()
        pop = [first, Individual(first.genes, "left")]
        # variação: ordenado pela maior dimensão
        dims = {pid: max(self.parts[pid].size) for pid in self.parts}
        g2 = tuple(sorted(first.genes, key=lambda g: -dims[self.instances[g[0]][0]]))
        pop.append(Individual(g2, "bbox"))
        tries = 0
        while len(pop) < max(4, self.params.population) and tries < 1000:
            tries += 1
            pop.append(self.mutate(first, rate=max(0.2, self.params.mutation_rate * 2)))
        return pop[: max(4, self.params.population)]

    def next_generation(self, scored: list[tuple[float, Individual]]) -> list[Individual]:
        scored.sort(key=lambda t: t[0])
        size = max(4, self.params.population)
        new = [scored[0][1]]
        if len(scored) > 1:
            new.append(scored[1][1])
        tries = 0
        while len(new) < size and tries < size * 20:
            tries += 1
            child = self.crossover(self.tournament(scored), self.tournament(scored))
            child = self.mutate(child)
            if child.key() in self._seen and self.rng.random() < 0.8:
                child = self.mutate(child, rate=0.3)
            new.append(child)
        return new

    # ------------------------------------------------------------------
    def run(self, on_best: Optional[Callable[[NestResult], None]] = None,
            on_progress: Optional[Callable[[dict], None]] = None,
            stop_event: Optional[threading.Event] = None,
            pause_event: Optional[threading.Event] = None,
            time_limit: Optional[float] = None,
            max_generations: Optional[int] = None) -> Optional[NestResult]:
        stop_event = stop_event or threading.Event()
        t0 = time.time()

        def should_stop():
            if stop_event.is_set():
                return True
            if time_limit is not None and time.time() - t0 >= time_limit:
                return True
            return False

        if not self.instances:
            res = self.decoder.decode([], [], [], "bbox", self.locked)
            self.best = self._to_result(res, Individual(()))
            if on_best:
                on_best(self.best)
            return self.best

        # 1) solução rápida no próprio processo
        first = self.first_individual()
        self._consider(first, self.evaluate_local(first), on_best)
        if on_progress:
            on_progress(self._progress(t0))

        pop = self.initial_population()
        no_improve = 0
        limit_ni = self.params.max_generations_without_improvement

        executor = None
        if self.workers and self.workers > 1:
            ctx = mp.get_context("spawn")
            executor = ProcessPoolExecutor(max_workers=self.workers, mp_context=ctx,
                                           initializer=_worker_init,
                                           initargs=(self.shapes, self.params, self.locked,
                                                     self.instances,
                                                     self.decoder.cache.export_state()))
        try:
            while not should_stop():
                while pause_event is not None and pause_event.is_set() and not should_stop():
                    time.sleep(0.1)
                if should_stop():
                    break
                prev_best = self.best.fitness if self.best else math.inf
                scored: list[tuple[float, Individual]] = []
                todo = []
                for ind in pop:
                    if ind.key() in self._seen:
                        scored.append((self._seen[ind.key()], ind))
                    else:
                        todo.append(ind)
                if executor is None:
                    for ind in todo:
                        if should_stop():
                            break
                        res = self.evaluate_local(ind)
                        self._consider(ind, res, on_best)
                        scored.append((res.fitness, ind))
                        if on_progress:
                            on_progress(self._progress(t0))
                else:
                    futs = {executor.submit(_worker_eval, ind) for ind in todo}
                    while futs:
                        done, futs = wait(futs, timeout=0.2, return_when=FIRST_COMPLETED)
                        for f in done:
                            ind, res = f.result()
                            self._consider(ind, res, on_best)
                            scored.append((res.fitness, ind))
                        if done and on_progress:
                            on_progress(self._progress(t0))
                        if should_stop():
                            for f in futs:
                                f.cancel()
                            break
                if should_stop() or not scored:
                    break
                self.generation += 1
                if on_progress:
                    on_progress(self._progress(t0))
                if self.best.fitness < prev_best - 1e-9:
                    no_improve = 0
                else:
                    no_improve += 1
                if limit_ni and no_improve >= limit_ni:
                    break
                if max_generations is not None and self.generation >= max_generations:
                    break
                pop = self.next_generation(scored)
        finally:
            if executor is not None:
                executor.shutdown(wait=False, cancel_futures=True)
        if on_progress:
            on_progress(self._progress(t0, finished=True))
        return self.best

    def _progress(self, t0, finished=False) -> dict:
        return {"generation": self.generation, "evaluated": self.evaluated,
                "elapsed": time.time() - t0, "finished": finished,
                "best_fitness": self.best.fitness if self.best else None}


def nest(parts: list[Part], params: NestParams, time_limit: float = 10.0,
         max_generations: Optional[int] = None, workers: Optional[int] = None,
         locked: Optional[list[Placement]] = None, seed: Optional[int] = None,
         on_best=None) -> NestResult:
    """Encaixe síncrono (para testes e linha de comando)."""
    gn = GeneticNester(parts, params, locked=locked, seed=seed, workers=workers)
    return gn.run(on_best=on_best, time_limit=time_limit, max_generations=max_generations)
