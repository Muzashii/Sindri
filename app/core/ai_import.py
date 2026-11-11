"""Leitura de arquivos do Adobe Illustrator (.ai) para as mesmas primitivas do DXF, em milímetros.

Dois formatos existem com a mesma extensão:

* **.ai moderno** (Illustrator 9 em diante, salvo com "Criar arquivo compatível com PDF"): é um PDF. Os
  caminhos são lidos dos operadores de desenho das páginas (``m l c v y h re`` + pintura), aplicando a
  matriz de transformação. Medidas em pontos (1/72 pol).
* **.ai antigo** (Illustrator 8 ou anterior): PostScript em texto (``%!PS-Adobe``), com um subconjunto
  simples de operadores de caminho.

Linhas viram LINE/POLY; cada curva de Bézier vira uma SPLINE cúbica exata (4 pontos de controle), então
nada é discretizado. A cor do traço vira a cor da primitiva (é por ela que o Sindri separa corte de
gravação). Texto e imagens são ignorados com aviso.
"""
from __future__ import annotations

import os
import re
from typing import Optional

from .dxf_import import DXFImportError, RawFile, filter_prims, rgb_to_color
from .import_issues import CHECK, FIXED, ImportIssue
from .models import Prim

PT_MM = 25.4 / 72.0
_BEZIER_KNOTS = [0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0]
_EPS = 1e-6
_MAX_FORM_DEPTH = 8


# --------------------------------------------------------------------------------------------------
# Montagem de caminhos (comum aos dois formatos)
# --------------------------------------------------------------------------------------------------
class _Path:
    """Caminho em construção: subcaminhos de segmentos ("L", p0, p1) ou ("C", p0, p1, p2, p3)."""

    def __init__(self):
        self.subs: list[dict] = []
        self.cur = None
        self.start = None

    def move(self, p):
        self.cur = self.start = p
        self.subs.append({"segs": [], "closed": False})

    def _sub(self):
        if not self.subs or self.cur is None:
            self.move(self.cur or (0.0, 0.0))
        return self.subs[-1]

    def line(self, p):
        s = self._sub()
        if _dist(self.cur, p) > _EPS:
            s["segs"].append(("L", self.cur, p))
        self.cur = p

    def curve(self, p1, p2, p3):
        s = self._sub()
        s["segs"].append(("C", self.cur, p1, p2, p3))
        self.cur = p3

    def close(self):
        if not self.subs or self.start is None:
            return
        s = self.subs[-1]
        if self.cur is not None and _dist(self.cur, self.start) > _EPS:
            s["segs"].append(("L", self.cur, self.start))
        s["closed"] = True
        self.cur = self.start

    def rect(self, pts):
        self.move(pts[0])
        for p in pts[1:]:
            self.line(p)
        self.close()


def _dist(a, b) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def _emit(path: _Path, color, layer: str, out: list[Prim]):
    """Transforma os subcaminhos em primitivas (pontos já em mm)."""
    aci, rgb = rgb_to_color(color)
    for sub in path.subs:
        segs = sub["segs"]
        if not segs:
            continue
        if all(s[0] == "L" for s in segs):
            pts = [segs[0][1]] + [s[2] for s in segs]
            closed = sub["closed"] and len(pts) > 3
            if closed:
                pts = pts[:-1]
            if len(pts) == 2:
                out.append(Prim("LINE", {"s": list(pts[0]), "e": list(pts[1])}, layer, aci, rgb))
            else:
                out.append(Prim("POLY", {"pts": [[p[0], p[1], 0.0] for p in pts], "closed": closed},
                                layer, aci, rgb))
            continue
        for s in segs:
            if s[0] == "L":
                out.append(Prim("LINE", {"s": list(s[1]), "e": list(s[2])}, layer, aci, rgb))
            else:
                out.append(Prim("SPLINE", {"degree": 3, "ctrl": [list(p) for p in s[1:]],
                                           "knots": list(_BEZIER_KNOTS), "weights": [], "fit": [],
                                           "closed": False}, layer, aci, rgb))


def _gray(g):
    v = int(round(max(0.0, min(1.0, float(g))) * 255))
    return (v, v, v)


def _rgb(r, g, b):
    return tuple(int(round(max(0.0, min(1.0, float(v))) * 255)) for v in (r, g, b))


def _cmyk(c, m, y, k):
    c, m, y, k = (max(0.0, min(1.0, float(v))) for v in (c, m, y, k))
    return tuple(int(round(255 * (1 - v) * (1 - k))) for v in (c, m, y))


def _color_from(vals) -> Optional[tuple]:
    nums = []
    for v in vals:
        try:
            nums.append(float(v))
        except (TypeError, ValueError):
            pass                                    # nome de padrão (/P0): não é cor sólida
    if len(nums) == 1:
        return _gray(nums[0])
    if len(nums) == 3:
        return _rgb(*nums)
    if len(nums) == 4:
        return _cmyk(*nums)
    return None


# --------------------------------------------------------------------------------------------------
# .ai moderno (PDF)
# --------------------------------------------------------------------------------------------------
def _mul(m, n):
    """Matriz PDF m aplicada antes de n (ponto' = ponto · m · n)."""
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (a * A + b * C, a * B + b * D, c * A + d * C, c * B + d * D, e * A + f * C + E, e * B + f * D + F)


def _apply(m, x, y):
    a, b, c, d, e, f = m
    return (a * x + c * y + e, b * x + d * y + f)


class _PdfWalker:
    def __init__(self, reader):
        self.reader = reader
        self.prims: list[Prim] = []
        self.texts = 0
        self.images = 0
        self.fill_only = 0

    def run(self, stream, resources, ctm, depth=0):
        from pypdf.generic import ContentStream
        try:
            ops = ContentStream(stream, self.reader).operations
        except Exception as exc:  # conteúdo ilegível
            raise DXFImportError(f"não foi possível ler o desenho do arquivo ({exc}).") from exc
        state = {"ctm": ctm, "stroke": (0, 0, 0), "fill": (0, 0, 0), "layer": "0"}
        stack = []
        layers = []
        path = _Path()
        props = {}
        xobjs = {}
        try:
            res = resources.get_object() if hasattr(resources, "get_object") else resources
            props = (res.get("/Properties") or {})
            props = props.get_object() if hasattr(props, "get_object") else props
            xobjs = (res.get("/XObject") or {})
            xobjs = xobjs.get_object() if hasattr(xobjs, "get_object") else xobjs
        except Exception:
            props, xobjs = {}, {}

        def pt(x, y):
            px, py = _apply(state["ctm"], float(x), float(y))
            return (px * PT_MM, py * PT_MM)

        for operands, op in ops:
            op = op.decode("latin-1") if isinstance(op, bytes) else str(op)
            try:
                if op == "q":
                    stack.append(dict(state))
                elif op == "Q":
                    if stack:
                        state = stack.pop()
                elif op == "cm":
                    state["ctm"] = _mul(tuple(float(v) for v in operands[:6]), state["ctm"])
                elif op == "m":
                    path.move(pt(*operands[:2]))
                elif op == "l":
                    path.line(pt(*operands[:2]))
                elif op == "c":
                    path.curve(pt(*operands[0:2]), pt(*operands[2:4]), pt(*operands[4:6]))
                elif op == "v":
                    path.curve(path.cur or pt(*operands[0:2]), pt(*operands[0:2]), pt(*operands[2:4]))
                elif op == "y":
                    p3 = pt(*operands[2:4])
                    path.curve(pt(*operands[0:2]), p3, p3)
                elif op == "h":
                    path.close()
                elif op == "re":
                    x, y, w, h = (float(v) for v in operands[:4])
                    path.rect([pt(x, y), pt(x + w, y), pt(x + w, y + h), pt(x, y + h)])
                elif op in ("S", "s", "B", "B*", "b", "b*"):
                    if op in ("s", "b", "b*"):
                        path.close()
                    _emit(path, state["stroke"], state["layer"], self.prims)
                    path = _Path()
                elif op in ("f", "F", "f*"):
                    self.fill_only += 1
                    for sub in path.subs:           # preenchimento fecha o contorno
                        if sub["segs"]:
                            sub["closed"] = True
                            a, b = sub["segs"][-1][-1], sub["segs"][0][1]
                            if _dist(a, b) > _EPS:
                                sub["segs"].append(("L", a, b))
                    _emit(path, state["fill"], state["layer"], self.prims)
                    path = _Path()
                elif op == "n":
                    path = _Path()                  # caminho de recorte: não é desenho
                elif op == "G":
                    state["stroke"] = _gray(operands[0])
                elif op == "RG":
                    state["stroke"] = _rgb(*operands[:3])
                elif op == "K":
                    state["stroke"] = _cmyk(*operands[:4])
                elif op in ("SC", "SCN"):
                    state["stroke"] = _color_from(operands) or state["stroke"]
                elif op == "g":
                    state["fill"] = _gray(operands[0])
                elif op == "rg":
                    state["fill"] = _rgb(*operands[:3])
                elif op == "k":
                    state["fill"] = _cmyk(*operands[:4])
                elif op in ("sc", "scn"):
                    state["fill"] = _color_from(operands) or state["fill"]
                elif op == "BT":
                    self.texts += 1
                elif op in ("BDC", "BMC"):
                    layers.append(state["layer"])
                    if op == "BDC" and len(operands) >= 2 and str(operands[0]) == "/OC":
                        try:
                            ocg = props[operands[1]]
                            ocg = ocg.get_object() if hasattr(ocg, "get_object") else ocg
                            name = str(ocg.get("/Name") or "").strip()
                            if name:
                                state["layer"] = name
                        except Exception:
                            pass                    # sem nome de camada: fica na camada atual
                elif op == "EMC":
                    if layers:
                        state["layer"] = layers.pop()
                elif op == "Do":
                    xo = xobjs.get(operands[0]) if operands else None
                    xo = xo.get_object() if hasattr(xo, "get_object") else xo
                    if xo is None:
                        continue
                    sub = str(xo.get("/Subtype"))
                    if sub == "/Image":
                        self.images += 1
                    elif sub == "/Form" and depth < _MAX_FORM_DEPTH:
                        m = xo.get("/Matrix")
                        inner = _mul(tuple(float(v) for v in m), state["ctm"]) if m else state["ctm"]
                        self.run(xo, xo.get("/Resources") or resources, inner, depth + 1)
            except DXFImportError:
                raise
            except (IndexError, TypeError, ValueError):
                continue                            # operador malformado: ignora só ele


def _read_pdf(path: str) -> tuple[list[Prim], dict]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise DXFImportError("falta o componente de leitura de PDF (pypdf) para abrir arquivos .ai.") from exc
    try:
        reader = PdfReader(path)
        if reader.is_encrypted:
            reader.decrypt("")
        pages = list(reader.pages)
    except DXFImportError:
        raise
    except Exception as exc:
        raise DXFImportError(f"o arquivo está corrompido ou não é um .ai válido ({exc}).") from exc
    walker = _PdfWalker(reader)
    text = ""
    for page in pages:
        contents = page.get_contents()
        if contents is None:
            continue
        walker.run(contents, page.get("/Resources") or {}, (1.0, 0.0, 0.0, 1.0, 0.0, 0.0))
        if not walker.prims and not text:
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
    if not walker.prims and "without PDF Content" in text:
        raise DXFImportError(
            "foi salvo sem conteúdo compatível com PDF. No Illustrator, use Salvar como… e marque "
            "“Criar arquivo compatível com PDF”, ou exporte o desenho como DXF.")
    return walker.prims, {"texts": walker.texts, "images": walker.images, "fill_only": walker.fill_only,
                          "pages": len(pages)}


# --------------------------------------------------------------------------------------------------
# .ai antigo (PostScript do Illustrator ≤ 8)
# --------------------------------------------------------------------------------------------------
_NUM = re.compile(r"^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$")


def _read_postscript(data: bytes) -> tuple[list[Prim], dict]:
    text = data.decode("latin-1", errors="replace")
    start = text.find("%%EndSetup")
    body = text[start:] if start >= 0 else text
    prims: list[Prim] = []
    stats = {"texts": 0, "images": 0, "fill_only": 0, "pages": 1}
    stroke, fill = (0, 0, 0), (0, 0, 0)
    layer = "0"
    path = _Path()
    nums: list[float] = []

    def pt(i):
        return (nums[i] * PT_MM, nums[i + 1] * PT_MM)

    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith("%AI5_BeginLayer"):
            continue
        if line.startswith("%%Trailer"):
            break
        if not line or line.startswith("%"):
            continue
        if line.endswith(" Ln") and line.startswith("("):
            layer = line[1:line.rfind(")")] or "0"
            continue
        nums = []
        for tok in line.split():
            if _NUM.match(tok):
                nums.append(float(tok))
                continue
            try:
                if tok == "m" and len(nums) >= 2:
                    path.move(pt(0))
                elif tok in ("l", "L") and len(nums) >= 2:
                    path.line(pt(0))
                elif tok in ("c", "C") and len(nums) >= 6:
                    path.curve(pt(0), pt(2), pt(4))
                elif tok in ("v", "V") and len(nums) >= 4:
                    path.curve(path.cur or pt(0), pt(0), pt(2))
                elif tok in ("y", "Y") and len(nums) >= 4:
                    path.curve(pt(0), pt(2), pt(2))
                elif tok in ("S", "s", "B", "b"):
                    if tok in ("s", "b"):
                        path.close()
                    _emit(path, stroke, layer, prims)
                    path = _Path()
                elif tok in ("F", "f"):
                    stats["fill_only"] += 1
                    path.close()
                    _emit(path, fill, layer, prims)
                    path = _Path()
                elif tok in ("N", "n"):
                    path = _Path()
                elif tok == "K" and len(nums) >= 4:
                    stroke = _cmyk(*nums[-4:])
                elif tok == "k" and len(nums) >= 4:
                    fill = _cmyk(*nums[-4:])
                elif tok == "G" and nums:
                    stroke = _gray(nums[-1])
                elif tok == "g" and nums:
                    fill = _gray(nums[-1])
                elif tok == "XA" and len(nums) >= 3:
                    stroke = _rgb(*nums[-3:])
                elif tok == "Xa" and len(nums) >= 3:
                    fill = _rgb(*nums[-3:])
                elif tok in ("Tp", "TP", "To"):
                    stats["texts"] += 1
            except (IndexError, ValueError):
                pass
            nums = []
    return prims, stats


# --------------------------------------------------------------------------------------------------
def read_ai(path: str, ignore_text: bool = False, excluded_layers: Optional[set] = None) -> RawFile:
    """Lê um .ai e devolve o mesmo ``RawFile`` do DXF (primitivas em mm)."""
    name = os.path.basename(path)
    try:
        with open(path, "rb") as fh:
            head = fh.read(1024)
            fh.seek(0)
            data = fh.read() if not head.lstrip().startswith(b"%PDF") else b""
    except OSError as exc:
        raise DXFImportError(f"Não foi possível abrir \"{name}\": {exc}") from exc
    try:
        if head.lstrip().startswith(b"%PDF"):
            prims, stats = _read_pdf(path)
            kind = "Illustrator (PDF)"
        elif head.lstrip().startswith(b"%!PS"):
            prims, stats = _read_postscript(data)
            kind = "Illustrator antigo (PostScript)"
        else:
            raise DXFImportError("não é um arquivo do Illustrator que o Sindri reconheça. Exporte o "
                                 "desenho como DXF ou salve de novo como .ai compatível com PDF.")
    except DXFImportError as exc:
        raise DXFImportError(f"\"{name}\": {exc}") from exc

    warnings: list[str] = []
    issues: list = []

    def issue(kind_, severity, message, count=1):
        issues.append(ImportIssue(os.path.abspath(path), kind_, severity, message, count))

    if stats["texts"]:
        warnings.append(f"{name}: {stats['texts']} texto(s) do Illustrator ignorado(s). Para gravar o texto, "
                        "converta em contornos (Texto > Criar contornos) antes de salvar.")
        issue("ai_text", CHECK, f"{stats['texts']} texto(s) não convertido(s) em contornos foram ignorados.",
              stats["texts"])
    if stats["images"]:
        warnings.append(f"{name}: {stats['images']} imagem(ns) ignorada(s) (use a aba Gravação de foto).")
        issue("ai_image", FIXED, f"{stats['images']} imagem(ns) ignorada(s).", stats["images"])
    if stats["fill_only"]:
        warnings.append(f"{name}: {stats['fill_only']} forma(s) só com preenchimento, sem traço: o Sindri usou "
                        "o contorno do preenchimento. Confira as cores de corte e gravação.")
        issue("ai_fill", CHECK, f"{stats['fill_only']} forma(s) sem traço: usado o contorno do preenchimento.",
              stats["fill_only"])
    if stats.get("pages", 1) > 1:
        warnings.append(f"{name}: o arquivo tem {stats['pages']} pranchetas; todas foram importadas.")

    prims, layers, size = filter_prims(path, prims, ignore_text, excluded_layers, warnings, issues)
    note = f"{kind}: medidas em pontos convertidas para mm"
    return RawFile(path, prims, 1.0, note, warnings, 4, None, size, layers, issues,
                   {"format": "ai", "kind": kind})
