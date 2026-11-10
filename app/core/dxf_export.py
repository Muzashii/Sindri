"""Exportação do encaixe para DXF (RDWorks).

* Exporta a geometria ORIGINAL (arcos, círculos, polilinhas com bulge, splines)
  apenas girada/espelhada/transladada — nunca a versão discretizada
  (exceção: R12 não tem ELLIPSE/SPLINE, que viram POLYLINE fina).
* Unidades em mm, origem da placa em (0, 0).
* Camadas e cores preservadas (cor explícita em cada entidade).
"""
from __future__ import annotations

import math
import os
from functools import lru_cache
from typing import Optional

import ezdxf
from ezdxf import bbox as ezbbox

from .geometry import Transform, flatten_prim, transform_prim
from .models import NestParams, Part, Placement, Prim

VERSIONS = {"R12": "R12", "R2000": "R2000"}
PLATE_LAYER = "PLACA"
LABEL_LAYER = "NUMEROS"      # nº da solicitação gravado em cada peça


def placed_prims(part: Part, pl: Placement, dx: float = 0.0, inner_first: bool = True,
                 ops: Optional[list[str]] = None, tab_width: Optional[float] = None, kerf: float = 0.0,
                 info: Optional[dict] = None) -> list[Prim]:
    return [pr for pr, _ in placed_prims_ops(part, pl, dx, inner_first, ops, tab_width, kerf, info)]


def cut_prims_local(part: Part, ops: list[str], tab_width: Optional[float] = None,
                    kerf: float = 0.0) -> tuple[list[tuple[Prim, str]], list[tuple], list[str], dict]:
    """Primitivas da peça como vão para o corte, em coordenadas LOCAIS e na ordem original:
    [(primitiva, operação)], [(p0, p1) de cada micro-ponte reta], avisos e contagens.

    * ``part.kerf_comp`` + ``kerf`` > 0: corte deslocado em kerf/2 (veja kerf.py);
    * ``part.tabs`` > 0: contorno externo interrompido nas micro-pontes (veja tabs.py).
    Sem nenhum dos dois devolve as primitivas originais, intactas."""
    from .operations import CUT_INNER
    prims, outer = list(part.prims), list(part.outer_prim_idx)
    items_ops = list(ops)
    notes: list[str] = []
    counts = {"tabs": 0, "kerf": 0}
    gaps: list[tuple] = []
    name = part.name.split(" (")[0]
    if getattr(part, "kerf_comp", False):
        from . import kerf as _kerf
        try:
            if not kerf or kerf <= 0:
                raise _kerf.KerfError("sem kerf")
            if part.is_open:
                raise _kerf.KerfError("contorno externo aberto")
            inner = [i for i, op in enumerate(items_ops) if op == CUT_INNER]
            repl, outer = _kerf.compensate(prims, outer, inner, float(kerf))
            prims = [q for r in repl for q in r]
            items_ops = [op for r, op in zip(repl, items_ops) for _ in r]
            counts["kerf"] = 1
        except _kerf.KerfError as e:
            prims, outer, items_ops = list(part.prims), list(part.outer_prim_idx), list(ops)
            notes.append(f"{name}: exportada SEM compensação de kerf — {_kerf.reason_text(e)}.")
    want = int(getattr(part, "tabs", 0) or 0)
    if want > 0 and not part.is_open:
        from . import tabs as _tabs
        width = _tabs.DEFAULT_WIDTH if not tab_width or tab_width <= 0 else float(tab_width)
        repl, made = _tabs.apply_tabs(prims, outer, want, width)
        prims = [q for r in repl for q in r]
        items_ops = [op for r, op in zip(repl, items_ops) for _ in r]
        counts["tabs"] = len(made)
        gaps = [(t.p0, t.p1) for t in made if t.straight]
        if len(made) < want:
            notes.append(f"{name}: " + ("não há trecho no contorno que comporte micro-ponte."
                                        if not made else f"só couberam {len(made)} de {want} micro-pontes."))
    return list(zip(prims, items_ops)), gaps, notes, counts


def placed_prims_ops(part: Part, pl: Placement, dx: float = 0.0, inner_first: bool = True,
                     ops: Optional[list[str]] = None, tab_width: Optional[float] = None, kerf: float = 0.0,
                     info: Optional[dict] = None) -> list[tuple[Prim, str]]:
    """Primitivas da peça já posicionadas, com a operação de cada uma. Com ``inner_first`` a ordem é:
    gravação, vinco, furos e, por último, o contorno (a peça só se solta no fim).
    Micro-pontes (``part.tabs``) e compensação de kerf (``part.kerf_comp`` com ``kerf`` > 0) entram aqui.
    ``info`` (opcional) recebe: "gaps" (trechos das pontes, já posicionados — para a remoção de trechos
    repetidos não cortar a ponte pela peça vizinha), "notes" (avisos), "tabs" e "kerf_parts" (contagens)."""
    from .operations import order_key, prim_operations
    tf = Transform(pl.rotation, pl.mirrored, pl.x + dx, pl.y)
    if ops is None:
        ops = prim_operations(part)
    if getattr(part, "tabs", 0) or getattr(part, "kerf_comp", False):
        cache = info.setdefault("_local", {}) if info is not None else {}
        key = (part.id, tab_width, kerf)
        if key not in cache:
            cache[key] = cut_prims_local(part, ops, tab_width, kerf)
        items, gaps, notes, counts = cache[key]
        if info is not None:
            info.setdefault("gaps", []).extend((tf.pt(a), tf.pt(b)) for a, b in gaps)
            for n in notes:
                if n not in info.setdefault("notes", []):
                    info["notes"].append(n)
            info["tabs"] = info.get("tabs", 0) + counts["tabs"]
            info["kerf_parts"] = info.get("kerf_parts", 0) + counts["kerf"]
    else:
        items = list(zip(part.prims, ops))
    idx = list(range(len(items)))
    if inner_first:
        idx.sort(key=lambda i: (order_key(items[i][1]), i))
    return [(transform_prim(items[i][0], tf), items[i][1]) for i in idx]


def part_kerf(part: Part, params, material_kerf: Optional[dict] = None) -> float:
    """Kerf (mm) do material da peça: ``material_kerf`` {material: kerf} (do banco de materiais) ou, na
    falta, ``params.material_sheets[material]["kerf"]``. 0 = não cadastrado (não compensa)."""
    mat = part.material or ""
    v = (material_kerf or {}).get(mat)
    if v is None:
        v = (getattr(params, "material_sheets", None) or {}).get(mat, {}).get("kerf", 0.0)
    try:
        v = float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return v if math.isfinite(v) and v > 0 else 0.0


def _kerf_notes(pmap: dict, placements, params, material_kerf, info: dict) -> None:
    """Avisos de espaçamento menor que o kerf para os materiais com peça compensada na exportação."""
    from .kerf import spacing_note
    used = {pl.part_id for pl in placements}
    for mat in sorted({pmap[i].material or "" for i in used if i in pmap and getattr(pmap[i], "kerf_comp", False)}):
        part = next(p for p in pmap.values() if (p.material or "") == mat)
        k = part_kerf(part, params, material_kerf)
        spacing = params.for_material(mat).spacing if hasattr(params, "for_material") else 0.0
        note = spacing_note(spacing, k, mat)
        if note and note not in info.setdefault("notes", []):
            info["notes"].append(note)


START_CORNERS = {"inferior_esquerdo": "canto inferior esquerdo (origem do DXF)",
                 "superior_direito": "canto superior direito (home comum da Ruida)"}


def start_point(corner: str, width: float, height: float) -> tuple[float, float]:
    """Ponto da placa onde o caminho de corte começa (perto do home da cabeça)."""
    return (width, height) if corner == "superior_direito" else (0.0, 0.0)


ORDER_MODES = {"caminho": "Caminho mais curto",
               "pequenas": "Pequenas primeiro, longe da exaustão (grandes por último)"}


def order_placements(parts: dict[str, Part], placements: list[Placement], nearest: bool = True,
                     start: tuple[float, float] = (0.0, 0.0), mode: str = "caminho") -> list[Placement]:
    """Caminho do vizinho mais próximo partindo de ``start`` (reduz deslocamentos do laser). Peças dentro
    de furos de outra são cortadas antes da hospedeira.
    ``mode="pequenas"``: peças pequenas primeiro (por faixa de tamanho) e, entre parecidas, as mais longe
    da exaustão (que fica no fundo da máquina = em cima no desenho); as grandes saem por último, então a
    chapa perde rigidez mais tarde e peça solta tem menos chance de bater no bico."""
    from .validate import placed_geometry
    rest = list(range(len(placements)))
    envelopes = [placed_geometry(parts[p.part_id].outer, p) for p in placements]
    dependencies = {i: {j for j in rest if i != j and placements[i].sheet_index == placements[j].sheet_index
                        and envelopes[i].area > envelopes[j].area and envelopes[i].covers(envelopes[j])}
                    for i in rest}
    out = []
    cx, cy = float(start[0]), float(start[1])
    import math as _m
    size_band = [int(_m.log2(max(1.0, envelopes[i].area / 100.0))) for i in range(len(placements))]
    while rest:
        eligible = [i for i in rest if not dependencies[i].intersection(rest)]
        if mode == "pequenas":
            k = min(eligible, key=lambda i: (size_band[i], round(placements[i].y / 50.0),
                                             (placements[i].x - cx) ** 2 + (placements[i].y - cy) ** 2))
        elif nearest:
            k = min(eligible, key=lambda i: (placements[i].x - cx) ** 2 + (placements[i].y - cy) ** 2)
        else:
            k = eligible[0]
        rest.remove(k)
        pl = placements[k]
        out.append(pl)
        cx, cy = pl.x, pl.y
    return out


_ACI_TABLE = None


@lru_cache(maxsize=4096)
def _nearest_aci(rgb: tuple) -> int:
    return _nearest_aci_impl(rgb)


def nearest_aci(rgb) -> int:
    return _nearest_aci(tuple(rgb))


def _nearest_aci_impl(rgb) -> int:
    """Cor ACI mais próxima de um RGB (R12/R2000 não guardam cor RGB; o RDWorks usa a cor)."""
    global _ACI_TABLE
    if _ACI_TABLE is None:
        _ACI_TABLE = [(i, ezdxf.colors.aci2rgb(i)) for i in range(1, 256)]
    r, g, b = rgb
    return min(_ACI_TABLE, key=lambda t: (t[1][0] - r) ** 2 + (t[1][1] - g) ** 2 + (t[1][2] - b) ** 2)[0]


def _attribs(p: Prim, version: str) -> dict:
    color = int(p.color) if 1 <= int(p.color) <= 255 else 7
    if p.rgb:
        # R12/R2000 não suportam true color: usa a ACI mais próxima (o RDWorks separa camadas pela cor)
        color = nearest_aci(p.rgb)
    return {"layer": p.layer or "0", "color": color}


def _ensure_layer(doc, name: str, color: int):
    if name not in doc.layers:
        doc.layers.add(name, color=color if 1 <= color <= 255 else 7)


def write_prim(msp, p: Prim, version: str):
    doc = msp.doc
    _ensure_layer(doc, p.layer or "0", p.color)
    at = _attribs(p, version)
    d = p.data
    k = p.kind
    if k == "LINE":
        msp.add_line(d["s"], d["e"], dxfattribs=at)
    elif k == "CIRCLE":
        msp.add_circle(d["c"], d["r"], dxfattribs=at)
    elif k == "ARC":
        msp.add_arc(d["c"], d["r"], d["a0"] % 360.0, d["a1"] % 360.0, dxfattribs=at)
    elif k == "POLY":
        pts = [(v[0], v[1], v[2] if len(v) > 2 else 0.0) for v in d["pts"]]
        if version == "R12":
            msp.add_polyline2d(pts, format="xyb", close=bool(d.get("closed")), dxfattribs=at)
        else:
            msp.add_lwpolyline(pts, format="xyb", close=bool(d.get("closed")), dxfattribs=at)
    elif k == "ELLIPSE":
        if version == "R12":
            _write_flat(msp, p, at)
        else:
            msp.add_ellipse(d["c"], major_axis=d["major"], ratio=d["ratio"], start_param=d["t0"],
                            end_param=d["t1"], dxfattribs=at)
    elif k == "SPLINE":
        if version == "R12":
            _write_flat(msp, p, at)
        else:
            if d.get("ctrl"):
                sp = msp.add_spline(dxfattribs=at)
                sp.dxf.degree = int(d["degree"])
                sp.control_points = [(x, y, 0) for x, y in d["ctrl"]]
                if d.get("knots") and len(d["knots"]) == len(d["ctrl"]) + int(d["degree"]) + 1:
                    sp.knots = d["knots"]
                if d.get("weights") and len(d["weights"]) == len(d["ctrl"]):
                    sp.weights = d["weights"]
                if d.get("closed"):
                    sp.closed = True
            else:
                msp.add_spline([(x, y, 0) for x, y in d["fit"]], dxfattribs=at)
    elif k == "TEXT":
        at = dict(at)
        at.update({"height": d["h"], "rotation": d.get("rot", 0.0), "width": d.get("width", 1.0)})
        t = msp.add_text(d["text"], dxfattribs=at)
        t.dxf.insert = d["p"]
        if d.get("halign") or d.get("valign"):
            t.dxf.halign = int(d.get("halign", 0))
            t.dxf.valign = int(d.get("valign", 0))
            t.dxf.align_point = d.get("p2") or d["p"]
        if d.get("mirror"):
            t.dxf.text_generation_flag = 2
    elif k == "MTEXT":
        if version == "R12":
            # R12 não tem MTEXT: uma linha TEXT por linha de texto
            try:
                from ezdxf.tools.text import plain_mtext
                lines = plain_mtext(d["text"], split=True)
            except Exception:
                lines = d["text"].replace("\\P", "\n").split("\n")
            a = math.radians(d.get("rot", 0.0))
            for i, ln in enumerate(lines):
                off = -(i + 1) * d["h"] * 1.6 + 0.6 * d["h"]
                x = d["p"][0] - math.sin(a) * off
                y = d["p"][1] + math.cos(a) * off
                tat = dict(at)
                tat.update({"height": d["h"], "rotation": d.get("rot", 0.0), "insert": (x, y)})
                msp.add_text(ln, dxfattribs=tat)
        else:
            mat = dict(at)
            mat.update({"insert": d["p"], "char_height": d["h"], "rotation": d.get("rot", 0.0),
                        "attachment_point": int(d.get("attach", 1))})
            if d.get("width"):
                mat["width"] = d["width"]
            msp.add_mtext(d["text"], dxfattribs=mat)
    else:  # pragma: no cover
        raise ValueError(k)


def _write_flat(msp, p: Prim, at):
    pts = flatten_prim(p, 0.01)
    closed = len(pts) > 2 and math.hypot(*(pts[0] - pts[-1])) < 1e-6
    if closed:
        pts = pts[:-1]
    msp.add_polyline2d([(float(x), float(y)) for x, y in pts], close=closed, dxfattribs=at)


def _new_doc(version: str):
    doc = ezdxf.new(VERSIONS.get(version, "R2000"))
    doc.header["$INSUNITS"] = 4
    doc.header["$MEASUREMENT"] = 1
    return doc


def _finish(doc, path: str):
    try:
        ext = ezbbox.extents(doc.modelspace(), fast=True)
        if ext.has_data:
            doc.header["$EXTMIN"] = (ext.extmin.x, ext.extmin.y, 0)
            doc.header["$EXTMAX"] = (ext.extmax.x, ext.extmax.y, 0)
    except Exception:
        pass
    doc.saveas(path)


def _plate(msp, params, dx: float):
    """Contorno da placa (camada cinza). ``params``: NestParams (chapa inteira) ou SheetSpec (pode ser
    retalho: desenha o contorno dele e os buracos)."""
    _ensure_layer(msp.doc, PLATE_LAYER, 8)
    at = {"layer": PLATE_LAYER, "color": 8}
    outline = getattr(params, "outline", ())
    if outline:
        for ring in [outline, *getattr(params, "holes", ())]:
            pts = [(x + dx, y) for x, y in ring]
            for i in range(len(pts)):
                msp.add_line(pts[i], pts[(i + 1) % len(pts)], dxfattribs=at)
        return
    w = getattr(params, "sheet_width", None) or params.width
    h = getattr(params, "sheet_height", None) or params.height
    pts = [(dx, 0), (dx + w, 0), (dx + w, h), (dx, h)]
    for i in range(4):
        msp.add_line(pts[i], pts[(i + 1) % 4], dxfattribs=at)


# fonte de traços (vetores) para escrever "PLACA n" — vira linha no RDWorks em qualquer versão
_STROKES = {
    "0": [[(0, 0), (1, 0), (1, 2), (0, 2), (0, 0)]],
    "1": [[(0.5, 0), (0.5, 2), (0.2, 1.7)]],
    "2": [[(0, 2), (1, 2), (1, 1), (0, 1), (0, 0), (1, 0)]],
    "3": [[(0, 2), (1, 2), (1, 0), (0, 0)], [(0, 1), (1, 1)]],
    "4": [[(0, 2), (0, 1), (1, 1)], [(1, 2), (1, 0)]],
    "5": [[(1, 2), (0, 2), (0, 1), (1, 1), (1, 0), (0, 0)]],
    "6": [[(1, 2), (0, 2), (0, 0), (1, 0), (1, 1), (0, 1)]],
    "7": [[(0, 2), (1, 2), (1, 0)]],
    "8": [[(0, 0), (1, 0), (1, 2), (0, 2), (0, 0)], [(0, 1), (1, 1)]],
    "9": [[(1, 1), (0, 1), (0, 2), (1, 2), (1, 0), (0, 0)]],
    "P": [[(0, 0), (0, 2), (1, 2), (1, 1), (0, 1)]],
    "L": [[(0, 2), (0, 0), (1, 0)]],
    "A": [[(0, 0), (0, 2), (1, 2), (1, 0)], [(0, 1), (1, 1)]],
    "C": [[(1, 0), (0, 0), (0, 2), (1, 2)]],
    "B": [[(0, 0), (0, 2), (0.8, 2), (0.8, 1), (1, 1), (1, 0), (0, 0)], [(0, 1), (0.8, 1)]],
    "D": [[(0, 0), (0, 2), (0.6, 2), (1, 1.6), (1, 0.4), (0.6, 0), (0, 0)]],
    "E": [[(1, 2), (0, 2), (0, 0), (1, 0)], [(0, 1), (0.7, 1)]],
    "F": [[(1, 2), (0, 2), (0, 0)], [(0, 1), (0.7, 1)]],
    "G": [[(1, 2), (0, 2), (0, 0), (1, 0), (1, 1), (0.5, 1)]],
    "H": [[(0, 0), (0, 2)], [(1, 0), (1, 2)], [(0, 1), (1, 1)]],
    "I": [[(0.5, 0), (0.5, 2)], [(0.2, 2), (0.8, 2)], [(0.2, 0), (0.8, 0)]],
    "J": [[(1, 2), (1, 0), (0, 0), (0, 0.5)]],
    "K": [[(0, 0), (0, 2)], [(1, 2), (0, 1), (1, 0)]],
    "M": [[(0, 0), (0, 2), (0.5, 1.2), (1, 2), (1, 0)]],
    "N": [[(0, 0), (0, 2), (1, 0), (1, 2)]],
    "O": [[(0, 0), (1, 0), (1, 2), (0, 2), (0, 0)]],
    "Q": [[(0, 0), (1, 0), (1, 2), (0, 2), (0, 0)], [(0.6, 0.4), (1.1, -0.2)]],
    "R": [[(0, 0), (0, 2), (1, 2), (1, 1), (0, 1), (1, 0)]],
    "S": [[(1, 2), (0, 2), (0, 1), (1, 1), (1, 0), (0, 0)]],
    "T": [[(0, 2), (1, 2)], [(0.5, 2), (0.5, 0)]],
    "U": [[(0, 2), (0, 0), (1, 0), (1, 2)]],
    "V": [[(0, 2), (0.5, 0), (1, 2)]],
    "W": [[(0, 2), (0.25, 0), (0.5, 1), (0.75, 0), (1, 2)]],
    "X": [[(0, 0), (1, 2)], [(0, 2), (1, 0)]],
    "Y": [[(0, 2), (0.5, 1), (1, 2)], [(0.5, 1), (0.5, 0)]],
    "Z": [[(0, 2), (1, 2), (0, 0), (1, 0)]],
    "-": [[(0.2, 1), (0.8, 1)]],
    ".": [[(0.4, 0), (0.6, 0)]],
    ",": [[(0.5, 0.1), (0.4, -0.3)]],
    "/": [[(0, 0), (1, 2)]],
    " ": [],
}


def _plain(text: str) -> str:
    """Sem acentos (a fonte de traços só tem A-Z, 0-9 e alguns sinais)."""
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c))


def stroke_lines(text: str, x: float, y: float, height: float) -> list[list[tuple[float, float]]]:
    """Traços do texto (fonte de linhas): uma lista de pontos por traço."""
    sy, sx = height / 2.0, height * 0.55
    out = []
    cx = x
    for ch in _plain(text).upper():
        for line in _STROKES.get(ch, []):
            out.append([(cx + px * sx, y + py * sy) for px, py in line])
        cx += sx + height * 0.3
    return out


def _stroke_text(msp, text: str, x: float, y: float, height: float, layer: str, color: int):
    """Escreve texto com linhas (sem fonte): largura de cada caractere = metade da altura."""
    at = {"layer": layer, "color": color}
    for pts in stroke_lines(text, x, y, height):
        if msp.doc.dxfversion == "AC1009":            # R12 não tem LWPOLYLINE
            for a, b in zip(pts, pts[1:]):
                msp.add_line(a, b, dxfattribs=at)
        else:
            msp.add_lwpolyline(pts, dxfattribs=at)


def text_size(text: str, height: float) -> tuple[float, float]:
    """Largura × altura do texto escrito por _stroke_text."""
    n = len(text)
    return (n * height * 0.55 + max(0, n - 1) * height * 0.3, height)


def label_spot(solid, text: str, height: float, margin: float = 0.8, min_height: float = 1.5):
    """Lugar para gravar ``text`` dentro da peça já posicionada, o mais perto possível do canto
    inferior esquerdo, sem encostar nas bordas nem nos furos. Devolve (x, y, altura) do canto
    inferior esquerdo do texto, ou None se a peça for pequena demais."""
    from shapely.geometry import Point
    from shapely.ops import nearest_points
    if solid is None or solid.is_empty:
        return None
    x0, y0, _, _ = solid.bounds
    h = float(height)
    while h >= min_height - 1e-9:
        w, hh = text_size(text, h)
        r = 0.5 * (w * w + hh * hh) ** 0.5 + margin
        inner = solid.buffer(-r)
        if not inner.is_empty:
            c = nearest_points(inner, Point(x0, y0))[0]
            return (c.x - w / 2, c.y - hh / 2, h)
        h *= 0.75
    return None


def material_tag(material: str) -> str:
    """'MDF 3mm' -> 'MDF3mm' (para nomes de arquivo)."""
    import re
    return re.sub(r"[^\w\-]+", "", material.replace(" ", "")) or "material"


def sheet_material(pmap: dict, placements: list[Placement], sheet: int) -> str:
    from .sheets import SheetIndex
    return SheetIndex(pmap, placements).material.get(sheet, "")


def sheet_groups(pmap: dict, placements: list[Placement]) -> list[tuple[str, list[int]]]:
    """[(material, [índices das placas])] na ordem das placas."""
    from .sheets import SheetIndex
    return SheetIndex(pmap, placements).groups


def export_sheets(parts: list[Part] | dict[str, Part], placements: list[Placement], params: NestParams,
                  out_dir: str, base_name: str = "projeto", version: str = "R2000",
                  combined: bool = False, sheet_outline: bool = False, inner_first: bool = True,
                  sort_path: bool = True, gap: float = 20.0, material_kerf: Optional[dict] = None,
                  stats: Optional[dict] = None) -> list[str]:
    """Escreve um DXF por placa (e opcionalmente um arquivo com todas lado a lado).
    ``material_kerf``: {material: kerf em mm} para as peças com compensação de kerf ligada."""
    pmap = parts if isinstance(parts, dict) else {p.id: p for p in parts}
    os.makedirs(out_dir, exist_ok=True)
    sheets = sorted({pl.sheet_index for pl in placements})
    files = []

    tab_width = getattr(params, "tab_width", None)
    info: dict = {}
    _kerf_notes(pmap, placements, params, material_kerf, info)

    def emit(msp, pls, dx):
        seq = order_placements(pmap, pls, nearest=sort_path) if inner_first or sort_path else pls
        from .overlap import remove_overlaps
        info["gaps"] = []
        prims = [pr for pl in seq
                 for pr in placed_prims(pmap[pl.part_id], pl, dx, inner_first, None, tab_width,
                                        part_kerf(pmap[pl.part_id], params, material_kerf), info)]
        for pr in remove_overlaps(prims, keep_gaps=info["gaps"])[0]:
            write_prim(msp, pr, version)

    groups = sheet_groups(pmap, placements)
    for mat, sis in groups:
        tag = f"_{material_tag(mat)}" if mat else ""
        for k, si in enumerate(sis):
            doc = _new_doc(version)
            msp = doc.modelspace()
            if sheet_outline:
                _plate(msp, params, 0.0)
            emit(msp, [pl for pl in placements if pl.sheet_index == si], 0.0)
            path = os.path.join(out_dir, f"{base_name}{tag}_placa{k + 1:02d}.dxf")
            _finish(doc, path)
            files.append(path)
    if combined and sheets:
        for mat, sis in groups:
            tag = f"_{material_tag(mat)}" if mat else ""
            doc = _new_doc(version)
            msp = doc.modelspace()
            for n, si in enumerate(sis):
                dx = n * (params.sheet_width + gap)
                if sheet_outline:
                    _plate(msp, params, dx)
                emit(msp, [pl for pl in placements if pl.sheet_index == si], dx)
            path = os.path.join(out_dir, f"{base_name}{tag}_todas_placas.dxf")
            _finish(doc, path)
            files.append(path)
        if len(groups) > 1:
            files.append(export_all_sheets(pmap, placements, params, out_dir, base_name, version,
                                           sheet_outline, inner_first, sort_path, gap,
                                           material_kerf=material_kerf))
    if stats is not None and info.get("notes"):
        stats["notes"] = list(info["notes"])
    return files


def export_all_sheets(parts: list[Part] | dict[str, Part], placements: list[Placement], params: NestParams,
                      out_dir: str, base_name: str = "projeto", version: str = "R2000",
                      sheet_outline: bool = False, inner_first: bool = True, sort_path: bool = True,
                      gap: float = 20.0, color_map: Optional[dict] = None,
                      only_sheets: Optional[set] = None, file_suffix: str = "_todas_placas",
                      remove_overlap: bool = True, stats: Optional[dict] = None,
                      part_labels: Optional[dict] = None, label_aci: int = 1,
                      label_height: float = 3.0, op_colors: Optional[dict] = None,
                      color_ops: Optional[dict] = None, start_corner: str = "inferior_esquerdo",
                      sheet_remnants: Optional[dict] = None, order_mode: str = "caminho",
                      material_kerf: Optional[dict] = None, tab_width: Optional[float] = None) -> str:
    """Um arquivo só (<nome>_todas_placas.dxf) com TODAS as placas de todos os materiais lado a lado,
    na mesma ordem do relatório (materiais separados por um espaço maior). É o que abre no RDWorks.
    ``color_map``: {(material, cor ACI original): cor ACI a gravar} — separa materiais em camadas.
    ``only_sheets``: exporta só essas placas (índices), mantendo o nº delas ("PLACA 3").
    ``op_colors``: {(material, camada de laser): cor} — modo "uma cor por material": cada primitiva vai para
    a cor da SUA operação (corte, vinco, gravação), nunca a gravação na cor de corte.
    ``color_ops``: {(material, cor do arquivo): operação} escolhida pelo técnico (padrão: veja operations).
    ``material_kerf``: {material: kerf em mm} (banco de materiais) para as peças com "Compensar kerf".
    ``tab_width``: largura das micro-pontes (padrão: ``params.tab_width``).
    ``stats`` recebe também "tabs" (pontes feitas), "kerf_parts" (peças compensadas) e "notes" (avisos)."""
    if tab_width is None:
        tab_width = getattr(params, "tab_width", None)
    info: dict = {}
    pmap = parts if isinstance(parts, dict) else {p.id: p for p in parts}
    os.makedirs(out_dir, exist_ok=True)
    doc = _new_doc(version)
    msp = doc.modelspace()
    dx = 0.0
    from .sheets import SheetIndex
    idx = SheetIndex(pmap, placements)
    from .sheetspec import sheet_specs
    specs = sheet_specs(params, idx.material, sheet_remnants)
    first = True
    solid_cache: dict = {}
    ops_cache: dict = {}
    from .laser import export_aci
    from .operations import is_text, layer_op, prim_operations
    for mat, sis in idx.groups:
        sis = [si for si in sis if only_sheets is None or si in only_sheets]
        if not sis:
            continue
        if not first:
            dx += 3 * gap
        first = False
        for si in sis:
            n_sheet = idx.number[si]
            spec = specs[si]
            if sheet_outline:
                _plate(msp, spec, dx)
                # nº da placa acima dela, na mesma camada cinza do contorno (desativar no RDWorks)
                hh = min(40.0, max(12.0, 0.05 * spec.height))
                label = f"PLACA {n_sheet}" + (f" - {mat}" if mat else "") + (" - RETALHO" if spec.is_remnant else "")
                _stroke_text(msp, label, dx, spec.height + hh * 0.6, hh, PLATE_LAYER, 8)
            pls = [pl for pl in placements if pl.sheet_index == si]
            sx, sy = start_point(start_corner, spec.width, spec.height)
            seq = order_placements(pmap, pls, nearest=sort_path, start=(sx, sy), mode=order_mode) \
                if inner_first or sort_path or order_mode != "caminho" else pls
            sheet_prims = []
            info["gaps"] = []
            for pl in seq:
                part = pmap[pl.part_id]
                if part.id not in ops_cache:
                    ops_cache[part.id] = prim_operations(part, color_ops)
                for pr, op in placed_prims_ops(part, pl, dx, inner_first, ops_cache[part.id], tab_width,
                                               part_kerf(part, params, material_kerf), info):
                    target = None
                    if op_colors is not None:
                        if not is_text(pr):
                            target = op_colors.get((part.material or "", layer_op(op)))
                    elif color_map:
                        target = color_map.get((part.material or "", export_aci(pr)))
                    if target:
                        pr.color, pr.rgb = target, None
                    sheet_prims.append(pr)
            if part_labels:
                # números primeiro: gravar antes de cortar (a peça solta pode se mexer)
                from .validate import fine_solid, placed_geometry
                if LABEL_LAYER not in doc.layers:
                    doc.layers.add(LABEL_LAYER, color=label_aci)
                for pl in seq:
                    text = part_labels.get(pl.part_id)
                    if not text:
                        continue
                    if pl.part_id not in solid_cache:
                        solid_cache[pl.part_id] = fine_solid(pmap[pl.part_id])
                    g = placed_geometry(solid_cache[pl.part_id], pl)
                    from shapely import affinity
                    spot = label_spot(affinity.translate(g, dx, 0), text, label_height)
                    if spot is None:
                        if stats is not None:
                            stats["labels_skipped"] = stats.get("labels_skipped", 0) + 1
                        continue
                    _stroke_text(msp, text, spot[0], spot[1], spot[2], LABEL_LAYER, label_aci)
                    if stats is not None:
                        stats["labels"] = stats.get("labels", 0) + 1
            if remove_overlap:
                from .overlap import remove_overlaps
                sheet_prims, n_rm = remove_overlaps(sheet_prims, keep_gaps=info["gaps"])
                if stats is not None:
                    stats["overlaps"] = stats.get("overlaps", 0) + n_rm
            for pr in sheet_prims:
                write_prim(msp, pr, version)
            dx += spec.width + gap
    if stats is not None:
        _kerf_notes(pmap, [pl for pl in placements if only_sheets is None or pl.sheet_index in only_sheets],
                    params, material_kerf, info)
        for k in ("tabs", "kerf_parts"):
            if info.get(k):
                stats[k] = stats.get(k, 0) + info[k]
        for n in info.get("notes", []):
            if n not in stats.setdefault("notes", []):
                stats["notes"].append(n)
    path = os.path.join(out_dir, f"{base_name}{file_suffix}.dxf")
    _finish(doc, path)
    return path


def sheet_file_suffix(number: int, material: str = "") -> str:
    """'_placa03_MDF3mm' — nome do arquivo de corte de uma placa."""
    return f"_placa{number:02d}" + (f"_{material_tag(material)}" if material else "")


def export_cut_files(parts: list[Part] | dict[str, Part], placements: list[Placement], params: NestParams,
                     out_dir: str, base_name: str, only_sheets: Optional[set] = None, **kw) -> dict[int, str]:
    """Um DXF por placa, pronto para cortar: origem (0, 0) no canto da chapa e SEM o contorno da placa
    (não há camada cinza para esquecer de desligar). Devolve {índice da placa: caminho}.
    ``kw`` vai para export_all_sheets (cores, números, ordem de corte…)."""
    from .sheets import SheetIndex
    pmap = parts if isinstance(parts, dict) else {p.id: p for p in parts}
    idx = SheetIndex(pmap, placements)
    kw.pop("sheet_outline", None)
    kw.pop("file_suffix", None)
    stats = kw.pop("stats", None)
    out: dict[int, str] = {}
    for si in idx.ordered:
        if only_sheets is not None and si not in only_sheets:
            continue
        sub_stats: dict = {}
        out[si] = export_all_sheets(pmap, placements, params, out_dir, base_name, sheet_outline=False,
                                    only_sheets={si},
                                    file_suffix=sheet_file_suffix(idx.number[si], idx.material.get(si, "")),
                                    stats=sub_stats, **kw)
        if stats is not None:
            stats.setdefault("per_sheet", {})[si] = sub_stats
            for k, v in sub_stats.items():
                if isinstance(v, (int, float)):
                    stats[k] = stats.get(k, 0) + v
            for n in sub_stats.get("notes", []):
                if n not in stats.setdefault("notes", []):
                    stats["notes"].append(n)
    return out
