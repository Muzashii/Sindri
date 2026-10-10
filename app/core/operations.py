"""Operação de laser de cada primitiva: corte, vinco ou gravação (sem Qt).

A cor do DXF sozinha não diz o que a linha é. No modo "uma cor por material" o Sindri juntava todas as
cores de um material na cor de corte, e uma linha de gravação azul virava corte (um círculo de gravação
virava furo). Aqui cada primitiva recebe uma *operação*:

* ``corte_externo`` — o contorno da peça (sempre corte, seja qual for a cor);
* ``corte_interno`` — furos e rasgos;
* ``vinco`` — score: marca a superfície sem atravessar;
* ``gravacao_vetorial`` — gravação seguindo a linha (modo corte com pouca potência);
* ``gravacao_raster`` — gravação de área (modo scan com intervalo).

O técnico escolhe a operação de cada cor do arquivo (por material). O padrão é: as cores que aparecem no
contorno externo das peças são corte; as outras são gravação vetorial. Arquivo de aluno com tudo preto
continua todo corte; arquivo com duas cores quase sempre tem gravação.
"""
from __future__ import annotations

from typing import Iterable, Optional

from .models import Part, Prim

CUT_OUTER = "corte_externo"
CUT_INNER = "corte_interno"
SCORE = "vinco"
ENGRAVE = "gravacao_vetorial"
RASTER = "gravacao_raster"
OPERATIONS = (CUT_OUTER, CUT_INNER, SCORE, ENGRAVE, RASTER)

# o que o técnico escolhe para uma cor do arquivo (o contorno externo é sempre corte)
CUT = "corte"
COLOR_OPS = (CUT, SCORE, ENGRAVE, RASTER)
# camada do RDWorks: os dois cortes dividem a mesma camada (mesma velocidade/potência)
LAYER_OPS = (CUT, SCORE, ENGRAVE, RASTER)

LABELS = {
    CUT: "Corte",
    CUT_OUTER: "Corte externo",
    CUT_INNER: "Corte interno",
    SCORE: "Vinco (score)",
    ENGRAVE: "Gravação vetorial",
    RASTER: "Gravação raster (scan)",
}

# ordem dentro da peça: gravar e vincar com a chapa ainda presa, depois furos, por último o contorno
_ORDER = {ENGRAVE: 0, RASTER: 0, SCORE: 1, CUT_INNER: 2, CUT_OUTER: 3}

ColorOps = dict  # {(material, cor ACI do arquivo): operação de COLOR_OPS}


def is_text(p: Prim) -> bool:
    return p.kind in ("TEXT", "MTEXT")


def layer_op(op: str) -> str:
    """Camada de laser de uma operação: corte externo e interno saem juntos."""
    return CUT if op in (CUT_OUTER, CUT_INNER, CUT) else op


def file_colors(parts: Iterable[Part]) -> dict[tuple[str, int], dict]:
    """{(material, cor): {"count", "layers", "outer"}} das primitivas que não são texto."""
    from .laser import export_aci
    out: dict[tuple[str, int], dict] = {}
    for part in parts:
        outer = set(part.outer_prim_idx)
        for i, p in enumerate(part.prims):
            if is_text(p):
                continue
            k = (part.material or "", export_aci(p))
            info = out.setdefault(k, {"count": 0, "layers": [], "outer": 0})
            info["count"] += 1
            if i in outer:
                info["outer"] += 1
            if p.layer and p.layer not in info["layers"]:
                info["layers"].append(p.layer)
    return out


# nome da camada que deixa claro o que a cor é (quando todas as camadas daquela cor concordam)
_LAYER_HINTS = [
    (RASTER, ("raster", "scan", "preench", "fill", "hachur")),
    (SCORE, ("vinco", "score", "dobra", "fold")),
    (ENGRAVE, ("grav", "engrav", "marca", "mark", "texto", "text", "etch")),
    (CUT, ("corte", "cut", "furo", "hole", "contorno", "outline", "recorte")),
]


def layer_hint(layer: str) -> Optional[str]:
    import unicodedata
    n = "".join(c for c in unicodedata.normalize("NFD", layer or "") if not unicodedata.combining(c)).lower()
    for op, words in _LAYER_HINTS:
        if any(w in n for w in words):
            return op
    return None


def default_color_ops(parts: Iterable[Part]) -> ColorOps:
    """Operação sugerida para cada cor: a cor do contorno externo é corte; as outras, gravação vetorial —
    a não ser que o nome da camada diga outra coisa ("FUROS" = corte, "VINCO" = vinco…). Um material sem
    nenhum contorno externo (só linhas abertas) fica todo como corte."""
    colors = file_colors(parts)
    cut_by_mat: dict[str, set[int]] = {}
    for (mat, aci), info in colors.items():
        if info["outer"]:
            cut_by_mat.setdefault(mat, set()).add(aci)
    out: ColorOps = {}
    for (mat, aci), info in colors.items():
        cut = cut_by_mat.get(mat)
        if not cut or aci in cut:
            out[(mat, aci)] = CUT
            continue
        hints = {layer_hint(ln) for ln in info["layers"]}
        out[(mat, aci)] = hints.pop() if len(hints) == 1 and None not in hints else ENGRAVE
    return out


def merge_color_ops(parts: Iterable[Part], chosen: Optional[ColorOps]) -> ColorOps:
    """Padrão para as cores novas + o que o técnico já escolheu (só para as cores que existem)."""
    parts = list(parts)
    out = default_color_ops(parts)
    for k, v in (chosen or {}).items():
        if k in out and v in COLOR_OPS:
            out[k] = v
    return out


def needs_confirmation(parts: Iterable[Part]) -> bool:
    """Algum material tem mais de uma cor (fora textos)? Então vale o técnico conferir as operações."""
    by_mat: dict[str, set[int]] = {}
    for mat, aci in file_colors(parts):
        by_mat.setdefault(mat, set()).add(aci)
    return any(len(c) > 1 for c in by_mat.values())


def prim_operations(part: Part, color_ops: Optional[ColorOps] = None) -> list[str]:
    """Operação de cada primitiva da peça (mesma ordem de ``part.prims``)."""
    from .laser import export_aci
    outer = set(part.outer_prim_idx)
    if color_ops is None:
        color_ops = default_color_ops([part])
    mat = part.material or ""
    ops = []
    for i, p in enumerate(part.prims):
        if is_text(p):
            ops.append(ENGRAVE)
        elif i in outer:
            ops.append(CUT_OUTER)
        else:
            op = color_ops.get((mat, export_aci(p)), CUT)
            ops.append(CUT_INNER if op == CUT else op)
    return ops


def order_key(op: str) -> int:
    return _ORDER.get(op, 2)


def layer_ops_in_use(parts: Iterable[Part], color_ops: Optional[ColorOps]) -> dict[str, list[str]]:
    """{material: [camadas de laser usadas]} na ordem de LAYER_OPS."""
    used: dict[str, set[str]] = {}
    for part in parts:
        if part.quantity <= 0:
            continue
        for p, op in zip(part.prims, prim_operations(part, color_ops)):
            if is_text(p):
                continue
            used.setdefault(part.material or "", set()).add(layer_op(op))
    return {m: [o for o in LAYER_OPS if o in s] for m, s in used.items()}


def color_ops_to_json(color_ops: ColorOps) -> list:
    return [[m, int(a), op] for (m, a), op in sorted(color_ops.items(), key=lambda kv: (kv[0][0], kv[0][1]))]


def color_ops_from_json(data) -> ColorOps:
    out: ColorOps = {}
    for row in data or []:
        try:
            m, a, op = row
            if op in COLOR_OPS:
                out[(str(m), int(a))] = op
        except (TypeError, ValueError):
            continue
    return out
