"""Converte um DXF exportado pelo Sindri em SVG ou PDF vetorial, na medida real (mm).

Serve para mandar o encaixe para outros programas (LightBurn, Inkscape, Illustrator, CorelDRAW) ou para
imprimir. Cada entidade vira um caminho só com traço (sem preenchimento), na cor da camada — é pela cor que
os programas de laser separam corte e gravação. Arcos, círculos e curvas viram curvas de Bézier exatas.

O "AI" gerado é um PDF com extensão .ai (o formato que o Illustrator salva como "compatível com PDF"):
abre no Illustrator e no próprio Sindri. Sem Qt.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

FORMATS = {                     # chave: (extensão, descrição)
    "dxf2000": (".dxf", "DXF R2000 (RDWorks, LightBurn, AutoCAD)"),
    "dxf12": (".dxf", "DXF R12 (programas antigos)"),
    "svg": (".svg", "SVG (Inkscape, LightBurn, navegador)"),
    "pdf": (".pdf", "PDF vetorial (imprimir, Illustrator, CorelDRAW)"),
    "ai": (".ai", "AI — Illustrator (compatível com PDF)"),
}
PT_PER_MM = 72.0 / 25.4
STROKE_MM = 0.1                 # espessura do traço: só para enxergar (programas de laser ignoram)
_SKIP = {"TEXT", "MTEXT", "ATTRIB", "ATTDEF", "DIMENSION", "HATCH", "IMAGE", "POINT", "VIEWPORT"}


@dataclass
class VectorDrawing:
    paths: list = field(default_factory=list)       # [(rgb, [[("M", (x, y)) | ("L", p) | ("C", c1, c2, p)]…])]
    min_x: float = 0.0
    min_y: float = 0.0
    width: float = 0.0
    height: float = 0.0
    skipped: dict = field(default_factory=dict)


def _rgb(entity, doc) -> tuple[int, int, int]:
    from ezdxf.colors import aci2rgb
    try:
        if entity.dxf.hasattr("true_color"):
            v = entity.dxf.true_color
            return ((v >> 16) & 255, (v >> 8) & 255, v & 255)
    except Exception:  # pragma: no cover - entidade sem cor própria
        pass
    aci = entity.dxf.get("color", 256)
    if aci in (0, 256):                                  # BYBLOCK/BYLAYER: cor da camada
        layer = doc.layers.get(entity.dxf.get("layer", "0")) if entity.dxf.get("layer", "0") in doc.layers else None
        aci = abs(layer.dxf.color) if layer is not None else 7
    if aci == 7:
        return (0, 0, 0)                                 # "branco/preto" do CAD: preto no papel
    r, g, b = aci2rgb(int(aci))
    return (int(r), int(g), int(b))


def read_drawing(dxf_path: str) -> VectorDrawing:
    """Lê o DXF e devolve os caminhos (em mm) com a cor de cada um."""
    import ezdxf
    from ezdxf import path as ezpath
    doc = ezdxf.readfile(dxf_path)
    out = VectorDrawing()
    xs, ys = [], []
    for e in doc.modelspace():
        kind = e.dxftype()
        if kind in _SKIP:
            out.skipped[kind] = out.skipped.get(kind, 0) + 1
            continue
        try:
            p = ezpath.make_path(e)
        except (TypeError, ValueError, ezdxf.DXFError):
            out.skipped[kind] = out.skipped.get(kind, 0) + 1
            continue
        subs = []
        for sub in (p.sub_paths() if p.has_sub_paths else [p]):
            if not len(sub):
                continue
            seg = [("M", (sub.start.x, sub.start.y))]
            xs.append(sub.start.x)
            ys.append(sub.start.y)
            for cmd in sub.commands():
                t = cmd.type.name
                if t in ("LINE_TO", "MOVE_TO"):
                    seg.append(("L" if t == "LINE_TO" else "M", (cmd.end.x, cmd.end.y)))
                    pts = [cmd.end]
                elif t == "CURVE3_TO":                   # quadrática → cúbica exata
                    p0 = seg_end(seg)
                    c = cmd.ctrl
                    c1 = (p0[0] + 2 / 3 * (c.x - p0[0]), p0[1] + 2 / 3 * (c.y - p0[1]))
                    c2 = (cmd.end.x + 2 / 3 * (c.x - cmd.end.x), cmd.end.y + 2 / 3 * (c.y - cmd.end.y))
                    seg.append(("C", c1, c2, (cmd.end.x, cmd.end.y)))
                    pts = [c, cmd.end]
                else:                                    # CURVE4_TO
                    seg.append(("C", (cmd.ctrl1.x, cmd.ctrl1.y), (cmd.ctrl2.x, cmd.ctrl2.y), (cmd.end.x, cmd.end.y)))
                    pts = [cmd.ctrl1, cmd.ctrl2, cmd.end]
                xs.extend(v.x for v in pts)
                ys.extend(v.y for v in pts)
            if len(seg) > 1:
                subs.append(seg)
        if subs:
            out.paths.append((_rgb(e, doc), subs))
    if xs:
        # origem no canto da chapa quando o desenho está todo no positivo (é o caso dos arquivos de corte)
        out.min_x, out.min_y = min(0.0, min(xs)), min(0.0, min(ys))
        out.width, out.height = max(xs) - out.min_x, max(ys) - out.min_y
    return out


def seg_end(seg) -> tuple[float, float]:
    return seg[-1][-1]


def _num(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def write_svg(drawing: VectorDrawing, svg_path: str, title: str = "") -> str:
    """SVG em milímetros (1 unidade = 1 mm), Y para cima como no DXF."""
    w, h = max(drawing.width, 0.001), max(drawing.height, 0.001)
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             f'<svg xmlns="http://www.w3.org/2000/svg" width="{_num(w)}mm" height="{_num(h)}mm" '
             f'viewBox="0 0 {_num(w)} {_num(h)}">']
    if title:
        safe = title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        lines.append(f"<title>{safe}</title>")
    # vira o Y (SVG cresce para baixo) e leva a origem para o canto
    lines.append(f'<g transform="matrix(1 0 0 -1 {_num(-drawing.min_x)} {_num(h + drawing.min_y)})" '
                 f'fill="none" stroke-width="{STROKE_MM}" stroke-linecap="round" stroke-linejoin="round">')
    for rgb, subs in drawing.paths:
        d = []
        for seg in subs:
            for op in seg:
                if op[0] in ("M", "L"):
                    d.append(f"{op[0]}{_num(op[1][0])} {_num(op[1][1])}")
                else:
                    d.append("C" + " ".join(f"{_num(x)} {_num(y)}" for x, y in op[1:]))
        color = "#{:02x}{:02x}{:02x}".format(*rgb)
        lines.append(f'<path stroke="{color}" d="{" ".join(d)}"/>')
    lines += ["</g>", "</svg>", ""]
    _atomic_write(svg_path, "\n".join(lines).encode("utf-8"))
    return svg_path


def write_pdf(drawing: VectorDrawing, pdf_path: str, title: str = "") -> str:
    """PDF de uma página do tamanho do desenho (medida real), só traços."""
    w, h = max(drawing.width, 0.001) * PT_PER_MM, max(drawing.height, 0.001) * PT_PER_MM
    ops = [f"{_num(STROKE_MM * PT_PER_MM)} w 1 J 1 j"]

    def pt(p):
        return f"{_num((p[0] - drawing.min_x) * PT_PER_MM)} {_num((p[1] - drawing.min_y) * PT_PER_MM)}"
    for rgb, subs in drawing.paths:
        ops.append("{} {} {} RG".format(*(_num(c / 255) for c in rgb)))
        for seg in subs:
            for op in seg:
                if op[0] == "M":
                    ops.append(f"{pt(op[1])} m")
                elif op[0] == "L":
                    ops.append(f"{pt(op[1])} l")
                else:
                    ops.append(f"{pt(op[1])} {pt(op[2])} {pt(op[3])} c")
            ops.append("S")
    content = "\n".join(ops).encode("latin-1")
    safe_title = "".join(ch for ch in (title or "Sindri") if 32 <= ord(ch) < 127 and ch not in "()\\")
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_num(w)} {_num(h)}] /Contents 4 0 R "
            f"/Resources << >> >>".encode(),
            b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
            f"<< /Title ({safe_title}) /Producer (Sindri) >>".encode()]
    buf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(buf))
        buf += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(buf)
    buf += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    buf += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    buf += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R /Info {len(objs)} 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n").encode()
    _atomic_write(pdf_path, bytes(buf))
    return pdf_path


def convert(dxf_path: str, fmt: str, out_path: str, title: str = "") -> tuple[str, dict]:
    """DXF do Sindri → svg/pdf/ai. Devolve (caminho, {tipo: nº de entidades que ficaram de fora})."""
    drawing = read_drawing(dxf_path)
    if fmt == "svg":
        write_svg(drawing, out_path, title)
    elif fmt in ("pdf", "ai"):
        write_pdf(drawing, out_path, title)
    else:
        raise ValueError(f"formato desconhecido: {fmt}")
    return out_path, drawing.skipped


def _atomic_write(path: str, data: bytes):
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)
