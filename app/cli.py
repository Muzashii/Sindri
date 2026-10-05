"""Linha de comando: encaixe sem interface.

Exemplo:
    python -m app.cli peças.dxf --placa 600x400 --tempo 30 --saida resultado/
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import math

from .core.dxf_export import export_sheets
from .core.models import NestParams
from .core.optimizer import nest
from .core.part_builder import import_files
from .core.validate import validate_layout


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="sindri", description="Encaixe automático de peças DXF.")
    ap.add_argument("arquivos", nargs="+", help="Arquivos .dxf de entrada")
    ap.add_argument("--placa", default="600x400", help="Largura x altura em mm (padrão 600x400)")
    ap.add_argument("--margem", type=float, default=5.0)
    ap.add_argument("--espaco", type=float, default=2.0)
    ap.add_argument("--rotacoes", type=int, default=4, help="Número de passos de rotação (4 = 90°)")
    ap.add_argument("--espelhar", action="store_true")
    ap.add_argument("--sem-part-in-part", action="store_true")
    ap.add_argument("--tempo", type=float, default=20.0, help="Tempo de otimização em segundos")
    ap.add_argument("--processos", type=int, default=None)
    ap.add_argument("--versao", choices=["R12", "R2000"], default="R2000")
    ap.add_argument("--contorno-placa", action="store_true")
    ap.add_argument("--saida", default="saida_nest")
    ap.add_argument("--nome", default=None)
    ap.add_argument("--permitir-parcial", action="store_true", help="Exportar somente as peças que couberam (retorno 4)")
    ap.add_argument("--com-textos", action="store_true", help="Manter textos (gravação) do DXF")
    ap.add_argument("--unidade", choices=["auto", "mm", "cm", "pol", "m"], default="auto",
                    help="Unidade do DXF (auto = a declarada no arquivo)")
    a = ap.parse_args(argv)

    try:
        w, h = (float(v) for v in a.placa.lower().replace("×", "x").split("x"))
    except ValueError:
        print("Formato de placa inválido. Use, por exemplo, 600x400.")
        return 2
    params = NestParams(sheet_width=w, sheet_height=h, margin=a.margem, spacing=a.espaco,
                        rotation_steps=a.rotacoes, allow_mirror=a.espelhar,
                        part_in_part=not a.sem_part_in_part,
                        ignore_text=not a.com_textos,
                        units_override={"auto": -1, "mm": 4, "cm": 5, "pol": 1, "m": 6}[a.unidade])
    try:
        params.validate()
        if not math.isfinite(a.tempo) or a.tempo <= 0 or (a.processos is not None and a.processos < 0):
            raise ValueError("Tempo deve ser positivo e processos não pode ser negativo.")
    except ValueError as e:
        print(f"Parâmetros inválidos: {e}")
        return 2
    rep = import_files(a.arquivos, params.join_tolerance, params.curve_tolerance, **params.import_kwargs())
    for wmsg in rep.warnings:
        print("Aviso:", wmsg)
    if not rep.parts:
        print("Nenhuma peça encontrada.")
        return 1
    total = sum(p.quantity for p in rep.parts)
    print(f"{len(rep.parts)} peça(s) diferentes, {total} no total.")
    t0 = time.time()

    def on_best(r):
        print(f"  {time.time() - t0:6.1f}s  placas: {r.sheets_used}  aproveitamento: "
              f"{100 * r.utilization:.1f}%  sem lugar: {len(r.unplaced)}")

    res = nest(rep.parts, params, time_limit=a.tempo, workers=a.processos, on_best=on_best)
    pmap = {p.id: p for p in rep.parts}
    issues = validate_layout(pmap, res.placements, params)
    if issues:
        print("ATENÇÃO — problemas na verificação final:")
        for i in issues:
            print("  -", i)
        return 3
    incomplete = len(res.placements) < total or len(rep.files) < len(a.arquivos)
    if incomplete and (not a.permitir_parcial or not res.placements):
        print("Encaixe incompleto; nenhum arquivo exportado. Use --permitir-parcial para exportar as peças que couberam.")
        return 4
    base = a.nome or os.path.splitext(os.path.basename(a.arquivos[0]))[0]
    files = export_sheets(rep.parts, res.placements, params, a.saida, base, a.versao,
                          combined=len({p.sheet_index for p in res.placements}) > 1,
                          sheet_outline=a.contorno_placa)
    for f in files:
        print("Gerado:", f)
    for pid, inst in res.unplaced:
        print(f"Não coube: {pmap[pid].name} #{inst + 1}")
    return 4 if incomplete else 0


if __name__ == "__main__":
    sys.exit(main())
