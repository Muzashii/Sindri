"""Materiais que não podem ir para o laser de CO2 (sem Qt).

PVC e vinil soltam cloro (gás tóxico que vira ácido clorídrico e corrói espelhos, guias e eletrônica),
policarbonato pega fogo e amarela, ABS solta fumaça tóxica, fibra de vidro e couro sintético soltam
fumaça tóxica e o corte fica ruim. A solicitação da intranet chega com o material em texto livre, então
o nome é conferido aqui; o banco de materiais pode marcar outros como "proibido".
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

OK, WARN, BLOCKED = "ok", "atencao", "bloqueado"
_RANK = {OK: 0, WARN: 1, BLOCKED: 2}

# (expressão no nome sem acentos e em minúsculas, motivo)
FORBIDDEN = [
    (r"\bpvc\b", "PVC solta cloro: gás tóxico que corrói espelhos, guias e eletrônica da máquina."),
    (r"\bvini[lc]\w*|\bvinyl\b", "Vinil (PVC) solta cloro: gás tóxico que corrói a máquina."),
    (r"\bpolicarbonato\b|\bpolycarbonate\b|\blexan\b|\bmakrolon\b",
     "Policarbonato pega fogo, amarela e solta fumaça tóxica no laser."),
    (r"\babs\b", "ABS solta fumaça tóxica (cianeto) e derrete em vez de cortar."),
    (r"\bfibra de vidro\b|\bfiberglass\b|\bfr-?4\b|\bg-?10\b",
     "Fibra de vidro/FR4 solta fumaça tóxica e a resina queima em vez de cortar."),
    (r"\bcouro (sintetico|ecologico|artificial)\b|\bcorino\b|\bkorino\b|\bcourvin\b|\bnapa sintetica\b",
     "Couro sintético costuma ser PVC ou PU: solta gases tóxicos."),
]
_ACRYLIC = r"\bacril\w*|\bpmma\b|\bplexi\w*"
_ACRYLIC_TYPE = r"\bcast\b|\bfundid\w*|\bextrud\w*|\bxt\b|\bextruded\b"


@dataclass(frozen=True)
class SafetyResult:
    status: str                  # ok | atencao | bloqueado
    reason: str = ""

    @property
    def blocked(self) -> bool:
        return self.status == BLOCKED


def normalize(name: str) -> str:
    s = unicodedata.normalize("NFD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s.lower()).strip()


def check_material(name: str, forbidden: bool = False, forbidden_reason: str = "") -> SafetyResult:
    """Confere o nome do material. ``forbidden``: marcado como proibido no banco de materiais."""
    if forbidden:
        return SafetyResult(BLOCKED, forbidden_reason or "Material marcado como proibido no banco de materiais.")
    n = normalize(name)
    if not n:
        return SafetyResult(OK)
    for pattern, reason in FORBIDDEN:
        if re.search(pattern, n):
            return SafetyResult(BLOCKED, reason)
    if re.search(_ACRYLIC, n) and not re.search(_ACRYLIC_TYPE, n):
        return SafetyResult(WARN, "Acrílico sem tipo informado: cast e extrudado gravam diferente "
                                  "(o extrudado deixa a gravação mais áspera). Informe qual é.")
    return SafetyResult(OK)


def worst(results) -> SafetyResult:
    """O pior resultado de uma lista (bloqueado > atenção > ok)."""
    out = SafetyResult(OK)
    for r in results:
        if _RANK[r.status] > _RANK[out.status]:
            out = r
    return out


def blocked_materials(names, lookup=None) -> list[tuple[str, str]]:
    """[(material, motivo)] dos materiais bloqueados. ``lookup(nome)`` devolve (proibido?, motivo) do banco."""
    out = []
    for m in dict.fromkeys(n for n in names if n is not None):
        flag, why = lookup(m) if lookup else (False, "")
        r = check_material(m, flag, why)
        if r.blocked:
            out.append((m or "sem material", r.reason))
    return out
