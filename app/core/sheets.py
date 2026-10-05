"""Índice das placas de um encaixe, calculado uma vez e usado por todo o programa.

Numeração "Placa 1, 2, 3…" = ordem por material (a mesma do arquivo 'todas as placas', do relatório,
do checklist e da tela)."""
from __future__ import annotations

from collections import Counter, defaultdict
from functools import cached_property

from .models import Part, Placement


def net_area(part: Part) -> float:
    """Área da peça sem os furos (cacheada no próprio objeto)."""
    v = part.__dict__.get("_net_area")
    if v is None:
        v = float(part.outer.area - sum(h.area for h in part.holes))
        part.__dict__["_net_area"] = v
    return v


class SheetIndex:
    def __init__(self, pmap: dict[str, Part], placements: list[Placement]):
        self.pmap = pmap
        self.placements = placements
        by: dict[int, list[Placement]] = defaultdict(list)
        for pl in placements:
            by[pl.sheet_index].append(pl)
        self.by_sheet: dict[int, list[Placement]] = dict(sorted(by.items()))

    @cached_property
    def material(self) -> dict[int, str]:
        out = {}
        for si, pls in self.by_sheet.items():
            mats = Counter(self.pmap[pl.part_id].material for pl in pls if pl.part_id in self.pmap)
            out[si] = mats.most_common(1)[0][0] if mats else ""
        return out

    @cached_property
    def groups(self) -> list[tuple[str, list[int]]]:
        """[(material, [índices das placas])] na ordem em que o material aparece."""
        out: list[tuple[str, list[int]]] = []
        pos: dict[str, int] = {}
        for si in self.by_sheet:
            m = self.material[si]
            if m not in pos:
                pos[m] = len(out)
                out.append((m, []))
            out[pos[m]][1].append(si)
        return out

    @cached_property
    def number(self) -> dict[int, int]:
        """índice interno -> nº mostrado (1..N)."""
        out, n = {}, 0
        for _m, sis in self.groups:
            for si in sis:
                n += 1
                out[si] = n
        return out

    @property
    def ordered(self) -> list[int]:
        """Índices das placas na ordem de exibição."""
        return [si for _m, sis in self.groups for si in sis]

    def count(self, si: int) -> int:
        return len(self.by_sheet.get(si, ()))

    def net_area(self, si: int) -> float:
        return sum(net_area(self.pmap[pl.part_id]) for pl in self.by_sheet.get(si, ()) if pl.part_id in self.pmap)

    @cached_property
    def sheets_of_part(self) -> dict[str, set[int]]:
        out: dict[str, set[int]] = defaultdict(set)
        for pl in self.placements:
            out[pl.part_id].add(pl.sheet_index)
        return dict(out)

    @cached_property
    def tags_of_sheet(self) -> dict[int, list[str]]:
        return {si: sorted({self.pmap[pl.part_id].tag for pl in pls if self.pmap[pl.part_id].tag})
                for si, pls in self.by_sheet.items()}


def checklist_progress(idx: SheetIndex, parts: list[Part], cut: set[int], done: set[str]):
    """Andamento do corte.

    Devolve (por peça {pid: (cópias em placas cortadas, cópias)},
             por solicitação {tag: (cópias feitas, cópias)},
             solicitações com todas as peças feitas)."""
    per_part: dict[str, tuple[int, int]] = {}
    per_tag: dict[str, tuple[int, int]] = {}
    for part in parts:
        copies = {pl.instance for pl in idx.placements if pl.part_id == part.id
                  and pl.sheet_index in cut and 0 <= pl.instance < part.quantity}
        per_part[part.id] = (len(copies), part.quantity)
        if part.tag:
            c, t = per_tag.get(part.tag, (0, 0))
            per_tag[part.tag] = (c + (part.quantity if part.id in done else len(copies)), t + part.quantity)
    by_tag: dict[str, list[str]] = defaultdict(list)
    for p in parts:
        if p.tag:
            by_tag[p.tag].append(p.id)
    done_tags = {t for t, ids in by_tag.items() if all(i in done for i in ids)}
    return per_part, per_tag, done_tags
