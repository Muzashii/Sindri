"""Benchmark determinístico do encaixe: python tools/bench_encaixe.py [--json saida.json]

Conjunto FIXO de lotes (fixtures de tests/fixtures + lotes sintéticos gerados por semente), rodados com
semente fixa, ``workers=0`` e um número fixo de gerações (nunca por tempo). Serve para comparar o
algoritmo genético ANTES e DEPOIS de uma mudança: o mesmo código dá sempre os mesmos números.

Por lote imprime: peças, placas, fitness (menor é melhor) e aproveitamento — a média das sementes e o
pior nº de placas. ``validate_layout`` confere cada resultado (nenhuma sobreposição).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ezdxf  # noqa: E402

from app.core.models import NestParams  # noqa: E402
from app.core.optimizer import GeneticNester  # noqa: E402
from app.core.part_builder import import_files  # noqa: E402
from app.core.validate import validate_layout  # noqa: E402

FIX = ROOT / "tests" / "fixtures"
SEEDS = (11, 23, 37)
GENERATIONS = 12
POPULATION = 10


# ------------------------------------------------------------------ formas sintéticas (em DXF, mm)
def _rect(msp, x, y, w, h):
    msp.add_lwpolyline([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], close=True)


def _ell(msp, x, y, w, h, tw, th):
    msp.add_lwpolyline([(x, y), (x + w, y), (x + w, y + th), (x + tw, y + th), (x + tw, y + h), (x, y + h)],
                       close=True)


def _rounded(msp, x, y, w, h, r):
    b = math.tan(math.pi / 8)                       # bulge de um arco de 90°
    msp.add_lwpolyline([(x + r, y, 0), (x + w - r, y, b), (x + w, y + r, 0), (x + w, y + h - r, b),
                        (x + w - r, y + h, 0), (x + r, y + h, b), (x, y + h - r, 0), (x, y + r, b)],
                       format="xyb", close=True)


def _half_disc(msp, x, y, r):
    msp.add_line((x, y), (x + 2 * r, y))
    msp.add_arc((x + r, y), r, 0, 180)


def _ring(msp, x, y, r_out, r_in):
    msp.add_circle((x + r_out, y + r_out), r_out)
    msp.add_circle((x + r_out, y + r_out), r_in)


def _disc(msp, x, y, r):
    msp.add_circle((x + r, y + r), r)


def _synthetic(kind: str, n: int, seed: int, path: str, types: int = 12) -> None:
    """Escreve ``n`` peças do tipo pedido, uma ao lado da outra (bem separadas). As medidas vêm de um
    catálogo sorteado de ``types`` modelos (lote real repete peças; o importador junta as iguais)."""
    rng = random.Random(seed)
    catalog = []
    for _ in range(types):
        k = kind if kind != "misto" else rng.choice(("ret", "ret", "L", "L", "arco", "anel", "disco"))
        if k == "ret":
            spec = (k, rng.choice((20, 30, 45, 60, 80, 120)) + rng.randint(0, 9),
                    rng.choice((15, 25, 40, 70)) + rng.randint(0, 9))
        elif k == "L":
            w, h = rng.randint(40, 110), rng.randint(40, 110)
            spec = (k, w, h, rng.randint(12, w // 2), rng.randint(12, h // 2))
        elif k == "arco":
            spec = (("redondo", rng.randint(30, 100), rng.randint(24, 70), rng.randint(4, 10))
                    if rng.random() < 0.5 else ("meio", rng.randint(12, 40)))
        elif k == "anel":
            r = rng.randint(25, 55)
            spec = (k, r, r - rng.randint(6, 12))
        else:  # disco pequeno (cabe dentro dos anéis)
            spec = (k, rng.randint(6, 16))
        catalog.append(spec)
    doc = ezdxf.new("R2000")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    x = 0.0
    for i in range(n):
        spec = catalog[i % len(catalog)] if i < len(catalog) else rng.choice(catalog)
        k = spec[0]
        if k == "ret":
            w = spec[1]
            _rect(msp, x, 0, spec[1], spec[2])
        elif k == "L":
            w = spec[1]
            _ell(msp, x, 0, *spec[1:])
        elif k == "redondo":
            w = spec[1]
            _rounded(msp, x, 0, *spec[1:])
        elif k == "meio":
            w = 2 * spec[1]
            _half_disc(msp, x, 0, spec[1])
        elif k == "anel":
            w = 2 * spec[1]
            _ring(msp, x, 0, spec[1], spec[2])
        else:
            w = 2 * spec[1]
            _disc(msp, x, 0, spec[1])
        x += w + 30
    doc.saveas(path)


def lots(tmp: str) -> list[tuple[str, list[str], NestParams, dict]]:
    """[(nome, arquivos, parâmetros, multiplicadores por arquivo)] — sempre os mesmos."""
    def P(**kw):
        return NestParams(population=POPULATION, stop_after_seconds=0, **kw)

    lab, var, fur = (str(FIX / f) for f in ("exemplo_lab.dxf", "variados.dxf", "furos.dxf"))
    out = [
        ("fx exemplo_lab", [lab], P(), {}),
        ("fx exemplo_lab 300x200", [lab], P(sheet_width=300, sheet_height=200), {}),
        ("fx variados x6", [var], P(sheet_width=300, sheet_height=200), {var: 6}),
        ("fx furos x12 + variados x2", [fur, var], P(sheet_width=300, sheet_height=200), {fur: 12, var: 2}),
    ]
    for name, kind, n, seed, types, kw in (
            ("sint retangulos 80", "ret", 80, 101, 14, dict(sheet_width=400, sheet_height=300)),
            ("sint L 60", "L", 60, 102, 10, dict(sheet_width=600, sheet_height=400)),
            ("sint arcos 60", "arco", 60, 103, 8, dict(sheet_width=400, sheet_height=300)),
            ("sint aneis+discos 70", "anel", 30, 104, 5, dict(sheet_width=600, sheet_height=400)),
            ("sint misto 100", "misto", 100, 105, 12, dict(sheet_width=600, sheet_height=400)),
            ("sint misto 120 (900x600)", "misto", 120, 106, 14, dict(sheet_width=900, sheet_height=600)),
    ):
        path = os.path.join(tmp, f"{kind}_{seed}.dxf")
        _synthetic(kind, n, seed, path, types)
        files = [path]
        if kind == "anel":                              # discos pequenos para aproveitar os furos
            extra = os.path.join(tmp, f"disco_{seed}.dxf")
            _synthetic("disco", 40, seed + 1000, extra, 4)
            files.append(extra)
        out.append((name, files, P(**kw), {}))
    return out


def run(seeds=SEEDS, generations=GENERATIONS, only: str = "") -> list[dict]:
    rows = []
    with tempfile.TemporaryDirectory(prefix="sindri-bench-") as tmp:
        for name, files, params, mult in lots(tmp):
            if only and only not in name:
                continue
            parts = import_files(files, multipliers=mult).parts
            pmap = {p.id: p for p in parts}
            n_parts = sum(p.quantity for p in parts)
            res_seeds = []
            t0 = time.perf_counter()
            for s in seeds:
                gn = GeneticNester(parts, params, seed=s, workers=0)
                r = gn.run(max_generations=generations)
                issues = validate_layout(pmap, r.placements, params)
                res_seeds.append({"seed": s, "sheets": r.sheets_used, "fitness": r.fitness,
                                  "utilization": r.utilization, "unplaced": len(r.unplaced),
                                  "evaluated": r.evaluated, "invalid": len(issues)})
            k = len(res_seeds)
            rows.append({"lot": name, "parts": n_parts,
                         "sheets_mean": sum(r["sheets"] for r in res_seeds) / k,
                         "sheets_max": max(r["sheets"] for r in res_seeds),
                         "fitness_mean": sum(r["fitness"] for r in res_seeds) / k,
                         "utilization_mean": sum(r["utilization"] for r in res_seeds) / k,
                         "unplaced": sum(r["unplaced"] for r in res_seeds),
                         "invalid": sum(r["invalid"] for r in res_seeds),
                         "seconds": time.perf_counter() - t0, "seeds": res_seeds})
            print_row(rows[-1], header=len(rows) == 1)
    return rows


def print_row(r: dict, header: bool = False) -> None:
    if header:
        print(f"{'lote':28s} {'peças':>5s} {'placas':>6s} {'pior':>4s} {'fitness':>9s} {'aprov.':>7s} "
              f"{'s/lugar':>7s} {'inval.':>6s} {'tempo':>6s}")
    print(f"{r['lot']:28s} {r['parts']:5d} {r['sheets_mean']:6.2f} {r['sheets_max']:4d} {r['fitness_mean']:9.4f} "
          f"{100 * r['utilization_mean']:6.1f}% {r['unplaced']:7d} {r['invalid']:6d} {r['seconds']:5.0f}s",
          flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", help="grava os números neste arquivo")
    ap.add_argument("--generations", type=int, default=GENERATIONS)
    ap.add_argument("--seeds", type=int, nargs="*", default=list(SEEDS))
    ap.add_argument("--only", default="", help="só os lotes cujo nome contém este texto")
    ap.add_argument("--local-moves", type=int, help="filhos por geração feitos por inserção/inversão em volta "
                                                    "dos melhores (0 = desligado, como era antes)")
    ap.add_argument("--insert-share", type=float, help="fração desses movimentos que é inserção")
    a = ap.parse_args()
    if a.local_moves is not None:
        GeneticNester.LOCAL_MOVES = a.local_moves
    if a.insert_share is not None:
        GeneticNester.INSERT_SHARE = a.insert_share
    rows = run(tuple(a.seeds), a.generations, a.only)
    tot = {"sheets": sum(r["sheets_mean"] for r in rows), "fitness": sum(r["fitness_mean"] for r in rows),
           "util": sum(r["utilization_mean"] for r in rows) / max(1, len(rows))}
    print(f"{'AGREGADO':28s} {'':5s} {tot['sheets']:6.2f} {'':4s} {tot['fitness']:9.4f} {100 * tot['util']:6.1f}%")
    if a.json:
        Path(a.json).write_text(json.dumps({"generations": a.generations, "seeds": list(a.seeds),
                                            "population": POPULATION, "rows": rows, "total": tot},
                                           indent=1, ensure_ascii=False), encoding="utf-8")
    return 1 if any(r["invalid"] for r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
