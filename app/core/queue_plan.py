"""Fila do dia: propõe lotes a partir das solicitações da lista (sem Qt).

Entrada: um resumo por solicitação, na forma de dicionário:
    {"code": "8759", "data": "01/10/2026 00:07:20", "nome": "...",
     "materials": {"MDF 3mm": 1, "MDF 6mm": 2} | None,   # nº de arquivos DXF por material; None = ainda não lida
     "seal": "ok" | "atencao" | "bloqueado", "seal_reasons": [...], "open": False}

Regras:
* ordem de chegada (data de envio, depois o nº);
* ficam de fora as bloqueadas (⛔), as já abertas no Sindri, as sem DXF e as que ainda não foram lidas;
* cada solicitação entra no lote do seu material principal (o que tem mais arquivos DXF; empate = o 1º da
  tabela). Os outros materiais dela vão junto, em placas próprias (materiais nunca dividem placa);
* um lote tem no máximo ``max_per_lot`` solicitações; cheio, abre-se outro do mesmo material.
Os lotes saem na ordem da solicitação mais antiga de cada um.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .intranet import date_key

DEFAULT_MAX_PER_LOT = 8


@dataclass
class QueueLot:
    number: int                                   # 1, 2, 3… (ordem em que devem ser abertos)
    material: str                                 # material principal do lote
    codes: list[str] = field(default_factory=list)
    per_material: dict[str, tuple[int, int]] = field(default_factory=dict)   # material -> (solicitações, arquivos)
    full: bool = False
    reason: str = ""

    @property
    def files(self) -> int:
        return sum(n for _, n in self.per_material.values())


@dataclass
class QueueSkip:
    code: str
    reason: str


@dataclass
class QueuePlan:
    lots: list[QueueLot] = field(default_factory=list)
    skipped: list[QueueSkip] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)       # ainda não lidas (detalhes desconhecidos)
    max_per_lot: int = DEFAULT_MAX_PER_LOT

    @property
    def planned(self) -> int:
        return sum(len(lot.codes) for lot in self.lots)


def arrival_order(items: list[dict]) -> list[dict]:
    """Mais antiga primeiro (data de envio; empate pelo nº)."""
    def key(it):
        code = str(it.get("code", ""))
        return (date_key(it.get("data", "")), int(code) if code.isdigit() else 0, code)
    return sorted(items, key=key)


def main_material(materials: dict[str, int]) -> str:
    """Material com mais arquivos DXF (empate: o primeiro da tabela da solicitação)."""
    best, n_best = "", -1
    for m, n in materials.items():
        if n > n_best:
            best, n_best = m, n
    return best


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def plan_queue(items: list[dict], max_per_lot: int = DEFAULT_MAX_PER_LOT) -> QueuePlan:
    max_per_lot = max(1, int(max_per_lot or DEFAULT_MAX_PER_LOT))
    plan = QueuePlan(max_per_lot=max_per_lot)
    open_lot: dict[str, QueueLot] = {}             # material -> lote que ainda aceita solicitações
    for it in arrival_order(list(items)):
        code = str(it.get("code", "")).strip()
        if not code:
            continue
        if it.get("open"):
            plan.skipped.append(QueueSkip(code, "já está aberta no Sindri"))
            continue
        if it.get("seal") == "bloqueado":
            why = "; ".join(str(x) for x in it.get("seal_reasons") or []) or "material proibido"
            plan.skipped.append(QueueSkip(code, f"⛔ bloqueada: {why}"))
            continue
        mats = it.get("materials")
        if mats is None:
            plan.pending.append(code)
            continue
        mats = {str(m) or "Sem material": int(n) for m, n in dict(mats).items() if int(n) > 0}
        if not mats:
            plan.skipped.append(QueueSkip(code, "sem arquivo DXF para corte a laser"))
            continue
        main = main_material(mats)
        lot = open_lot.get(main)
        if lot is None or len(lot.codes) >= max_per_lot:
            lot = QueueLot(number=len(plan.lots) + 1, material=main)
            plan.lots.append(lot)
            open_lot[main] = lot
        lot.codes.append(code)
        for m, n in mats.items():
            reqs, files = lot.per_material.get(m, (0, 0))
            lot.per_material[m] = (reqs + 1, files + n)
        lot.full = len(lot.codes) >= max_per_lot
    for lot in plan.lots:
        parts = [f"{lot.material}: {_plural(len(lot.codes), 'solicitação', 'solicitações')} em ordem de chegada"]
        if lot.full:
            parts.append(f"lote cheio (máx. {max_per_lot})")
        others = [m for m in lot.per_material if m != lot.material]
        if others:
            parts.append("também " + ", ".join(others) + ", em placas próprias")
        lot.reason = " · ".join(parts)
    return plan


def plan_lines(plan: QueuePlan) -> list[str]:
    """Plano em texto curto (uma linha por lote), para o painel e para copiar."""
    out = []
    for lot in plan.lots:
        mats = "; ".join(f"{m}: {_plural(r, 'solicitação', 'solicitações')}, {_plural(f, 'arquivo', 'arquivos')}"
                         for m, (r, f) in lot.per_material.items())
        out.append(f"Lote {lot.number} · {', '.join(lot.codes)} · {mats}")
    return out


def summary_from_detail(code, row: Optional[dict], detail, seal=None, is_open: bool = False) -> dict:
    """Resumo de uma solicitação para ``plan_queue``. ``detail`` é um RequestDetail (ou None, se não lida);
    ``seal`` é o selo (status + motivos) já calculado para ela."""
    row = row or {}
    mats = None
    if detail is not None:
        mats = {m: len(fs) for m, fs in detail.materials().items()}
    return {"code": str(code), "data": row.get("data", ""), "nome": row.get("nome", ""),
            "materials": mats, "seal": getattr(seal, "status", "ok") if seal is not None else "ok",
            "seal_reasons": list(getattr(seal, "reasons", []) or []), "open": bool(is_open)}
