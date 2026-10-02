"""Leitura de DXF e normalização para primitivas em milímetros.

* Explode blocos (INSERT/MINSERT), inclusive aninhados, resolvendo camada "0"
  e cor BYBLOCK pelo bloco pai.
* Converte unidades ($INSUNITS) para mm.
* Normaliza extrusão para +Z (ezdxf.upright) para que arcos e polilinhas
  fiquem no sistema de coordenadas do mundo.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Optional

import ezdxf
from ezdxf import recover
from ezdxf.math import Matrix44
from ezdxf import units as ezunits

from .models import Prim

try:
    from ezdxf.upright import upright as _upright
except Exception:  # pragma: no cover
    _upright = None


class DXFImportError(Exception):
    """Erro amigável de importação."""


UNIT_NAMES = {0: "sem unidade", 1: "polegadas", 2: "pés", 4: "milímetros", 5: "centímetros",
              6: "metros", 8: "micropolegadas", 9: "mils", 10: "jardas", 14: "decímetros"}

SUPPORTED = {"LINE", "ARC", "CIRCLE", "LWPOLYLINE", "POLYLINE", "SPLINE", "ELLIPSE",
             "INSERT", "TEXT", "MTEXT"}


@dataclass
class RawFile:
    path: str
    prims: list[Prim]
    unit_factor: float
    unit_note: str
    warnings: list[str] = field(default_factory=list)
    declared_units: int = 0
    suggested_units: Optional[int] = None   # unidade sugerida quando o tamanho parece absurdo
    size_mm: float = 0.0                    # maior dimensão do desenho (mm, após conversão)
    layers: dict = field(default_factory=dict)   # camada -> {"color": aci, "count": n, "texts": n}


# unidades oferecidas na interface (código $INSUNITS -> nome)
UNIT_CHOICES = [(4, "milímetros"), (5, "centímetros"), (1, "polegadas"), (6, "metros")]


def unit_factor(code: int) -> float:
    if code in (0, 4):
        return 1.0
    return ezunits.conversion_factor(code, ezunits.MM)


def read_dxf(path: str, units_override: Optional[int] = None, ignore_text: bool = False,
             excluded_layers: Optional[set] = None) -> RawFile:
    """Lê um DXF e devolve suas primitivas normalizadas em mm.

    units_override: código $INSUNITS a usar no lugar do declarado no arquivo (None = usar o do arquivo).
    """
    name = os.path.basename(path)
    if not os.path.isfile(path):
        raise DXFImportError(f"Arquivo não encontrado: {name}")
    try:
        doc = ezdxf.readfile(path)
    except IOError as e:
        raise DXFImportError(f"Não foi possível abrir \"{name}\": {e}") from e
    except ezdxf.DXFStructureError:
        try:
            doc, auditor = recover.readfile(path)
        except Exception as e:
            raise DXFImportError(
                f"\"{name}\" não parece ser um DXF válido ou está corrompido.") from e
    except Exception as e:
        raise DXFImportError(f"\"{name}\" não pôde ser lido: {e}") from e

    warnings: list[str] = []
    declared = int(doc.header.get("$INSUNITS", 0) or 0)
    insunits = declared
    if units_override is not None and units_override >= 0:
        insunits = int(units_override)
        factor = unit_factor(insunits)
        note = f"{name}: unidade definida manualmente como {UNIT_NAMES.get(insunits, insunits)}."
    elif insunits in (0,):
        factor = 1.0
        note = f"{name}: unidade não definida no arquivo — assumindo milímetros."
        warnings.append(note)
    else:
        try:
            factor = ezunits.conversion_factor(insunits, ezunits.MM)
            note = f"{name}: unidades em {UNIT_NAMES.get(insunits, insunits)} (convertidas para mm)."
        except Exception:
            factor = 1.0
            note = f"{name}: unidade {insunits} desconhecida — assumindo milímetros."
            warnings.append(note)

    prims: list[Prim] = []
    skipped: dict[str, int] = {}
    msp = doc.modelspace()
    for e in msp:
        try:
            _collect(e, doc, prims, skipped, parent_layer=None, parent_color=7, parent_rgb=None,
                     factor=factor, depth=0)
        except Exception as ex:  # entidade problemática não derruba a importação
            key = f"{e.dxftype()} (com erro)"
            skipped[key] = skipped.get(key, 0) + 1
    for t, n in skipped.items():
        warnings.append(f"{name}: {n} entidade(s) {t} ignorada(s).")
    layers: dict = {}
    for p in prims:
        info = layers.setdefault(p.layer, {"color": p.color, "count": 0, "texts": 0})
        info["count"] += 1
        if p.kind in ("TEXT", "MTEXT"):
            info["texts"] += 1
    excl = set(excluded_layers or ())
    n_txt = sum(1 for p in prims if p.kind in ("TEXT", "MTEXT"))
    prims = [p for p in prims if p.layer not in excl and
             not (ignore_text and p.kind in ("TEXT", "MTEXT"))]
    if ignore_text and n_txt:
        warnings.append(f"{name}: {n_txt} texto(s) ignorado(s) (opção “Ignorar textos”).")
    if not prims:
        warnings.append(f"{name}: nenhuma geometria suportada encontrada.")
    size = _drawing_size(prims)
    suggested = None
    if prims and (units_override is None or units_override < 0):
        suggested = suggest_units(size / factor, factor)
        if suggested is not None:
            warnings.append(
                f"{name}: o arquivo diz estar em {UNIT_NAMES.get(declared, 'unidade indefinida')}, "
                f"mas assim o desenho teria {size / 1000:.1f} m de largura. Provavelmente está em "
                f"{UNIT_NAMES[suggested]}. Ajuste em Parâmetros > Unidade do DXF.")
    return RawFile(path, prims, factor, note, warnings, declared, suggested, size, layers)


def _drawing_size(prims: list[Prim]) -> float:
    from .geometry import flatten_prim
    xs0, ys0, xs1, ys1 = [], [], [], []
    for p in prims:
        try:
            pts = flatten_prim(p, 1.0)
        except Exception:
            continue
        if len(pts):
            xs0.append(pts[:, 0].min()); xs1.append(pts[:, 0].max())
            ys0.append(pts[:, 1].min()); ys1.append(pts[:, 1].max())
    if not xs0:
        return 0.0
    return float(max(max(xs1) - min(xs0), max(ys1) - min(ys0)))


def suggest_units(raw_size: float, current_factor: float) -> Optional[int]:
    """Se o desenho fica absurdo (> 20 m ou < 2 mm) na unidade atual, sugere outra plausível."""
    cur = raw_size * current_factor
    if 2.0 <= cur <= 6000.0 or raw_size <= 0:
        return None
    for code in (4, 1, 5, 6):   # mm, polegadas, cm, m
        f = unit_factor(code)
        if abs(f - current_factor) < 1e-12:
            continue
        if 10.0 <= raw_size * f <= 5000.0:
            return code
    return None


def _resolve_color(e, doc, parent_color: int, parent_rgb, layer_name: str):
    aci = int(e.dxf.get("color", 256))
    rgb = None
    tc = e.dxf.get("true_color", None)
    if tc is not None:
        rgb = ezdxf.colors.int2rgb(tc)
        rgb = (int(rgb[0]), int(rgb[1]), int(rgb[2]))
    if aci == 0:  # BYBLOCK
        return parent_color, (rgb or parent_rgb)
    if aci == 256:  # BYLAYER
        try:
            layer = doc.layers.get(layer_name)
            lc = abs(int(layer.dxf.color)) or 7
            lrgb = None
            if layer.dxf.hasattr("true_color"):
                v = ezdxf.colors.int2rgb(layer.dxf.true_color)
                lrgb = (int(v[0]), int(v[1]), int(v[2]))
            return lc, (rgb or lrgb)
        except Exception:
            return 7, rgb
    return aci, rgb


def _collect(e, doc, out, skipped, parent_layer, parent_color, parent_rgb, factor, depth):
    t = e.dxftype()
    layer = e.dxf.get("layer", "0")
    if parent_layer is not None and layer == "0":
        layer = parent_layer
    color, rgb = _resolve_color(e, doc, parent_color, parent_rgb, layer)

    if t == "INSERT":
        if depth > 32:
            skipped["INSERT (aninhamento excessivo)"] = skipped.get("INSERT (aninhamento excessivo)", 0) + 1
            return
        inserts = list(e.multi_insert()) if e.mcount > 1 else [e]
        for ins in inserts:
            for ve in ins.virtual_entities():
                _collect(ve, doc, out, skipped, layer, color, rgb, factor, depth + 1)
        # atributos (ATTRIB) também são texto
        for att in getattr(e, "attribs", []):
            try:
                p = _text_prim(att, factor, is_mtext=False)
                if p:
                    p.layer, p.color, p.rgb = layer, color, rgb
                    out.append(p)
            except Exception:
                pass
        return
    if t not in SUPPORTED:
        skipped[t] = skipped.get(t, 0) + 1
        return

    if t in ("TEXT", "MTEXT"):
        p = _text_prim(e, factor, is_mtext=(t == "MTEXT"))
        if p:
            p.layer, p.color, p.rgb = layer, color, rgb
            out.append(p)
        return

    # trabalha numa cópia para normalizar sem alterar o documento
    try:
        ent = e.copy()
    except Exception:
        ent = e
    if abs(factor - 1.0) > 1e-12:
        ent.transform(Matrix44.scale(factor, factor, factor))
    if _upright is not None and t in ("ARC", "CIRCLE", "ELLIPSE", "LWPOLYLINE"):
        try:
            _upright(ent)
        except Exception:
            pass
    prims = _entity_to_prims(ent)
    for p in prims:
        p.layer, p.color, p.rgb = layer, color, rgb
        out.append(p)


def _xy(v):
    return [float(v[0]), float(v[1])]


def _entity_to_prims(e) -> list[Prim]:
    t = e.dxftype()
    if t == "LINE":
        s, en = e.dxf.start, e.dxf.end
        if (s - en).magnitude < 1e-9:
            return []
        return [Prim("LINE", {"s": _xy(s), "e": _xy(en)})]
    if t in ("CIRCLE", "ARC"):
        ext = e.dxf.extrusion
        if abs(ext.z - 1.0) > 1e-9:
            # extrusão não normalizada (ex.: 3D inclinado): usar caminho discretizado
            return _fallback_path(e)
        c = e.dxf.center
        r = float(e.dxf.radius)
        if r <= 0:
            return []
        if t == "CIRCLE":
            return [Prim("CIRCLE", {"c": _xy(c), "r": r})]
        return [Prim("ARC", {"c": _xy(c), "r": r, "a0": float(e.dxf.start_angle) % 360.0,
                             "a1": float(e.dxf.end_angle) % 360.0})]
    if t == "LWPOLYLINE":
        if abs(e.dxf.extrusion.z - 1.0) > 1e-9:
            return _fallback_path(e)
        pts = [[float(x), float(y), float(b)] for x, y, b in e.get_points("xyb")]
        pts = _dedupe_poly_pts(pts)
        if len(pts) < 2:
            return []
        return [Prim("POLY", {"pts": pts, "closed": bool(e.closed)})]
    if t == "POLYLINE":
        if e.is_2d_polyline:
            ext = e.dxf.extrusion
            if abs(ext.z - 1.0) > 1e-9:
                return _fallback_path(e)
            elev = 0.0
            pts = []
            for v in e.vertices:
                flags = v.dxf.get("flags", 0)
                if flags & 16:  # ponto de controle de spline: ignorar
                    continue
                loc = v.dxf.location
                pts.append([float(loc.x), float(loc.y), float(v.dxf.get("bulge", 0.0))])
            pts = _dedupe_poly_pts(pts)
            if len(pts) < 2:
                return []
            return [Prim("POLY", {"pts": pts, "closed": bool(e.is_closed)})]
        if e.is_3d_polyline:
            pts = [[float(v.dxf.location.x), float(v.dxf.location.y), 0.0] for v in e.vertices]
            pts = _dedupe_poly_pts(pts)
            if len(pts) < 2:
                return []
            return [Prim("POLY", {"pts": pts, "closed": bool(e.is_closed)})]
        return []  # malhas/faces: sem uso no corte
    if t == "ELLIPSE":
        if abs(e.dxf.extrusion.z - 1.0) > 1e-9:
            return _fallback_path(e)
        return [Prim("ELLIPSE", {"c": _xy(e.dxf.center), "major": _xy(e.dxf.major_axis),
                                 "ratio": float(e.dxf.ratio),
                                 "t0": float(e.dxf.start_param), "t1": float(e.dxf.end_param)})]
    if t == "SPLINE":
        ctrl = [_xy(v) for v in e.control_points]
        fit = [_xy(v) for v in e.fit_points]
        if len(ctrl) < 2 and len(fit) < 2:
            return []
        weights = [float(w) for w in e.weights] if len(e.weights) else []
        return [Prim("SPLINE", {"degree": int(e.dxf.degree), "ctrl": ctrl,
                                "knots": [float(k) for k in e.knots], "weights": weights,
                                "fit": fit if not ctrl else [], "closed": bool(e.closed)})]
    return []


def _fallback_path(e) -> list[Prim]:
    from ezdxf import path as ezpath
    p = ezpath.make_path(e)
    pts = [[float(v.x), float(v.y), 0.0] for v in p.flattening(0.01)]
    if len(pts) < 2:
        return []
    return [Prim("POLY", {"pts": pts, "closed": False})]


def _dedupe_poly_pts(pts):
    out = []
    for p in pts:
        if out and abs(out[-1][0] - p[0]) < 1e-9 and abs(out[-1][1] - p[1]) < 1e-9:
            out[-1][2] = p[2]
            continue
        out.append(p)
    return out


def _text_prim(e, factor: float, is_mtext: bool) -> Optional[Prim]:
    """Converte TEXT/MTEXT em primitiva com caixa delimitadora aproximada."""
    try:
        if is_mtext:
            text = e.text
            plain = e.plain_text()
            p = e.dxf.insert
            h = float(e.dxf.get("char_height", 2.5))
            rot = float(e.get_rotation()) if hasattr(e, "get_rotation") else float(e.dxf.get("rotation", 0.0))
            width = float(e.dxf.get("width", 0.0) or 0.0)
            attach = int(e.dxf.get("attachment_point", 1))
            lines = plain.split("\n") or [""]
            w = max(width, max(len(ln) for ln in lines) * h * 0.7) if lines else h
            hh = len(lines) * h * 1.6
            # caixa a partir do ponto de anexação
            col = (attach - 1) % 3   # 0 esq, 1 centro, 2 dir
            row = (attach - 1) // 3  # 0 topo, 1 meio, 2 base
            x0 = -w * (col / 2.0)
            y1 = hh * (row / 2.0)
            corners = [(x0, y1 - hh), (x0 + w, y1 - hh), (x0 + w, y1), (x0, y1)]
            data = {"text": text, "h": h, "width": width, "attach": attach}
        else:
            text = e.dxf.get("text", "")
            plain = e.plain_text() if hasattr(e, "plain_text") else text
            p = e.dxf.insert
            h = float(e.dxf.get("height", 2.5))
            rot = float(e.dxf.get("rotation", 0.0))
            wf = float(e.dxf.get("width", 1.0) or 1.0)
            halign = int(e.dxf.get("halign", 0))
            valign = int(e.dxf.get("valign", 0))
            p2 = e.dxf.get("align_point", None)
            w = max(1, len(plain)) * h * 0.7 * wf
            if halign in (1, 4):
                x0 = -w / 2
            elif halign == 2:
                x0 = -w
            else:
                x0 = 0.0
            if halign in (3, 5) and p2 is not None:  # alinhado/ajustado: entre p e p2
                w = max(w * 0.1, (p2 - p).magnitude)
                x0 = 0.0
            if valign == 3:
                y0 = -h
            elif valign == 2:
                y0 = -h / 2
            elif valign == 1:
                y0 = 0.0
            else:
                y0 = -0.25 * h
            corners = [(x0, y0), (x0 + w, y0), (x0 + w, y0 + h * 1.25), (x0, y0 + h * 1.25)]
            data = {"text": text, "h": h, "width": wf, "halign": halign, "valign": valign,
                    "p2": _xy(p2) if (p2 is not None and (halign or valign)) else None,
                    "mirror": bool(int(e.dxf.get("text_generation_flag", 0)) & 2)}
        a = math.radians(rot)
        ca, sa = math.cos(a), math.sin(a)
        bbox = [[(p.x + ca * x - sa * y) * factor, (p.y + sa * x + ca * y) * factor] for x, y in corners]
        data["p"] = [p.x * factor, p.y * factor]
        if data.get("p2") is not None:
            data["p2"] = [data["p2"][0] * factor, data["p2"][1] * factor]
        data["h"] = data["h"] * factor
        if is_mtext and data.get("width"):
            data["width"] = data["width"] * factor
        data["rot"] = rot % 360.0
        data["bbox"] = bbox
        if not str(plain).strip():
            return None
        return Prim("MTEXT" if is_mtext else "TEXT", data)
    except Exception:
        return None
