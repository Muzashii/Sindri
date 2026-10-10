"""Compara dois arquivos `config` do RDWorks e mostra o que mudou em cada camada.

Serve para descobrir onde o RDWorks guarda campos que o Sindri ainda não grava (saída sim/não, modo,
passadas, sopro…). Na máquina do laboratório:

1. copie o `config` da pasta do RDWorks para `antes.cfg`;
2. abra o RDWorks, mude SÓ um campo de UMA camada (ex.: camada azul, saída = NÃO) e feche o programa;
3. copie o `config` de novo para `depois.cfg`;
4. rode:  python tools/config_diff.py antes.cfg depois.cfg

A saída diz em que tabela, camada e posição dentro do registro os bytes mudaram (e o valor como inteiro
e como double). Anote no DECISIONS.md antes de fazer o Sindri gravar o campo.
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.core.laser import config_version, find_tables  # noqa: E402


def locate(tables, pos: int):
    """(tabela, camada, deslocamento no registro) de um byte, ou None se estiver fora das tabelas.
    O registro começa 8 bytes antes da velocidade (cor RGB + 5 bytes)."""
    for ti, t in enumerate(tables):
        start = t.offset - 8
        end = start + t.count * t.stride
        if start <= pos < end:
            layer, off = divmod(pos - start, t.stride)
            return ti, layer, off
    return None


def diff(a: bytes, b: bytes) -> list[str]:
    tables = find_tables(a)
    out = [f"config: versão {config_version(a) or '?'} -> {config_version(b) or '?'}; "
           f"{len(a)} -> {len(b)} bytes; {len(tables)} tabela(s) de camadas"]
    for i, t in enumerate(tables):
        out.append(f"  tabela {i}: velocidade da camada 0 em {t.offset}, registro de {t.stride} bytes, "
                   f"{t.count} camadas")
    if len(a) != len(b):
        out.append("tamanhos diferentes: comparando só o trecho comum")
    runs, i, n = [], 0, min(len(a), len(b))
    while i < n:
        if a[i] != b[i]:
            j = i
            while j < n and a[j] != b[j]:
                j += 1
            runs.append((i, j))
            i = j
        else:
            i += 1
    if not runs:
        out.append("nenhum byte diferente")
    for s, e in runs:
        where = locate(tables, s)
        place = (f"tabela {where[0]}, camada {where[1]}, byte {where[2]} do registro" if where
                 else "fora das tabelas de camadas")
        line = f"bytes {s}..{e - 1} ({place}): {a[s:e].hex(' ')} -> {b[s:e].hex(' ')}"
        if where:
            rec = where[2] - where[2] % 4
            base = s - (where[2] - rec)
            if base + 4 <= n:
                line += (f" | int32 {struct.unpack_from('<i', a, base)[0]} -> "
                         f"{struct.unpack_from('<i', b, base)[0]}")
            rec8 = s - (where[2] % 8)
            if rec8 + 8 <= n:
                line += (f" | double {struct.unpack_from('<d', a, rec8)[0]:g} -> "
                         f"{struct.unpack_from('<d', b, rec8)[0]:g}")
        out.append(line)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("antes")
    ap.add_argument("depois")
    args = ap.parse_args(argv)
    a, b = Path(args.antes).read_bytes(), Path(args.depois).read_bytes()
    print("\n".join(diff(a, b)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
