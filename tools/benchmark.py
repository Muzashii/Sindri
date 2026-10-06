"""Benchmark repetível: python tools/benchmark.py --output docs/benchmark.json."""
import argparse
import json
from pathlib import Path
import sys
import time
import tracemalloc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# Mesma ordem de DLLs do lançador no Windows.
import PySide6.QtWebEngineWidgets  # noqa: F401
from app.core.models import NestParams
from app.core.optimizer import GeneticNester
from app.core.part_builder import import_files
from app.core.validate import validate_layout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="*")
    parser.add_argument("--output", default="docs/benchmark.json")
    args = parser.parse_args()
    files = args.files or [str(ROOT / "tests/fixtures/exemplo_lab.dxf")]
    start = time.perf_counter()
    report = import_files(files)
    imported = time.perf_counter()
    params = NestParams(population=4, stop_after_seconds=0)
    tracemalloc.start()
    nester = GeneticNester(report.parts, params, seed=17, workers=0)
    first = []
    result = nester.run(on_best=lambda r: first.append(time.perf_counter() - imported), max_generations=2)
    elapsed = time.perf_counter() - imported
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    issues = validate_layout({p.id: p for p in report.parts}, result.placements, params)
    data = {"python": sys.version, "files": [Path(f).name for f in files], "seed": 17,
            "workers": 0, "population": 4, "max_generations": 2,
            "part_types": len(report.parts), "requested": sum(p.quantity for p in report.parts),
            "import_seconds": imported-start, "first_solution_seconds": first[0] if first else None,
            "optimization_seconds": elapsed, "python_peak_bytes": peak,
            "memory_note": "tracemalloc: apenas alocações Python durante encaixe; não mede toda a RAM nativa",
            "nfp_entries": len(nester.decoder.cache._nfp), "cache_hits": nester.decoder.cache.hits,
            "cache_misses": nester.decoder.cache.misses, "evaluated": result.evaluated,
            "sheets": result.sheets_used, "utilization": result.utilization, "unplaced": len(result.unplaced),
            "validation_issues": issues}
    Path(args.output).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(data, indent=2, ensure_ascii=True))
    return bool(issues)


if __name__ == "__main__":
    raise SystemExit(main())
