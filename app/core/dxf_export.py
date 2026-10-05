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

import ezdxf
from ezdxf import bbox as ezbbox

from .geometry import Transform, flatten_prim, transform_prim
from .models import NestParams, Part, Placement, Prim

VERSIONS = {"R12": "R12", "R2000": "R2000"}
PLATE_LAYER = "PLACA"


def placed_prims(part: Part, pl: Placement, dx: float = 0.0, inner_first: bool = True) -> list[Prim]:
    tf = Transform(pl.rotation, pl.mirrored, pl.x + dx, pl.y)
    outer = set(part.outer_prim_idx)
    idx = list(range(len(part.prims)))
    if inner_first:
        idx.sort(key=lambda i: (i in outer, i))
    return [transform_prim(part.prims[i], tf) for i in idx]


def order_placements(parts: dict[str, Part], placements: list[Placement]) -> list[Placement]:
    """Caminho do vizinho mais próximo partindo da origem (reduz deslocamentos do laser)."""
    rest = list(placements)
    out = []
    cx, cy = 0.0, 0.0
    while rest:
        k = min(range(len(rest)), key=lambda i: (rest[i].x - cx) ** 2 + (rest[i].y - cy) ** 2)
        pl = rest.pop(k)
        out.append(pl)
        cx, cy = pl.x, pl.y
    return out


_ACI_TABLE = None


def nearest_aci(rgb) -> int:
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


def _plate(msp, params: NestParams, dx: float):
    _ensure_layer(msp.doc, PLATE_LAYER, 8)
    w, h = params.sheet_width, params.sheet_height
    pts = [(dx, 0), (dx + w, 0), (dx + w, h), (dx, h)]
    at = {"layer": PLATE_LAYER, "color": 8}
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
    " ": [],
}


def _stroke_text(msp, text: str, x: float, y: float, height: float, layer: str, color: int):
    """Escreve texto com linhas (sem fonte): largura de cada caractere = metade da altura."""
    sy, sx = height / 2.0, height * 0.55
    at = {"layer": layer, "color": color}
    cx = x
    for ch in text.upper():
        for line in _STROKES.get(ch, []):
            pts = [(cx + px * sx, y + py * sy) for px, py in line]
            msp.add_lwpolyline(pts, dxfattribs=at)
        cx += sx + height * 0.3


def material_tag(material: str) -> str:
    """'MDF 3mm' -> 'MDF3mm' (para nomes de arquivo)."""
    import re
    return re.sub(r"[^\w\-]+", "", material.replace(" ", "")) or "material"


def sheet_material(pmap: dict, placements: list[Placement], sheet: int) -> str:
    mats = [pmap[pl.part_id].material for pl in placements if pl.sheet_index == sheet and pl.part_id in pmap]
    return max(set(mats), key=mats.count) if mats else ""


def sheet_groups(pmap: dict, placements: list[Placement]) -> list[tuple[str, list[int]]]:
    """[(material, [índices das placas])] na ordem das placas."""
    out: list[tuple[str, list[int]]] = []
    for si in sorted({pl.sheet_index for pl in placements}):
        m = sheet_material(pmap, placements, si)
        if out and out[-1][0] == m:
            out[-1][1].append(si)
        elif any(g[0] == m for g in out):
            next(g for g in out if g[0] == m)[1].append(si)
        else:
            out.append((m, [si]))
    return out


def export_sheets(parts: list[Part] | dict[str, Part], placements: list[Placement], params: NestParams,
                  out_dir: str, base_name: str = "projeto", version: str = "R2000",
                  combined: bool = False, sheet_outline: bool = False, inner_first: bool = True,
                  sort_path: bool = True, gap: float = 20.0) -> list[str]:
    """Escreve um DXF por placa (e opcionalmente um arquivo com todas lado a lado)."""
    pmap = parts if isinstance(parts, dict) else {p.id: p for p in parts}
    os.makedirs(out_dir, exist_ok=True)
    sheets = sorted({pl.sheet_index for pl in placements})
    files = []

    def emit(msp, pls, dx):
        seq = order_placements(pmap, pls) if sort_path else pls
        for pl in seq:
            for pr in placed_prims(pmap[pl.part_id], pl, dx, inner_first):
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
                                           sheet_outline, inner_first, sort_path, gap))
    return files


def export_all_sheets(parts: list[Part] | dict[str, Part], placements: list[Placement], params: NestParams,
                      out_dir: str, base_name: str = "projeto", version: str = "R2000",
                      sheet_outline: bool = False, inner_first: bool = True, sort_path: bool = True,
                      gap: float = 20.0) -> str:
    """Um arquivo só (<nome>_todas_placas.dxf) com TODAS as placas de todos os materiais lado a lado,
    na mesma ordem do relatório (materiais separados por um espaço maior). É o que abre no RDWorks."""
    pmap = parts if isinstance(parts, dict) else {p.id: p for p in parts}
    os.makedirs(out_dir, exist_ok=True)
    doc = _new_doc(version)
    msp = doc.modelspace()
    dx = 0.0
    for g, (mat, sis) in enumerate(sheet_groups(pmap, placements)):
        if g:
            dx += 3 * gap
        for si in sis:
            n_sheet = sum(len(x) for _, x in sheet_groups(pmap, placements)[:g]) + sis.index(si) + 1
            if sheet_outline:
                _plate(msp, params, dx)
                # nº da placa acima dela, na mesma camada cinza do contorno (desativar no RDWorks)
                hh = min(40.0, max(12.0, 0.05 * params.sheet_height))
                _stroke_text(msp, f"PLACA {n_sheet}", dx, params.sheet_height + hh * 0.6, hh, PLATE_LAYER, 8)
            pls = [pl for pl in placements if pl.sheet_index == si]
            seq = order_placements(pmap, pls) if sort_path else pls
            for pl in seq:
                for pr in placed_prims(pmap[pl.part_id], pl, dx, inner_first):
                    write_prim(msp, pr, version)
            dx += params.sheet_width + gap
    path = os.path.join(out_dir, f"{base_name}_todas_placas.dxf")
    _finish(doc, path)
    return path
