"""Gerador de caixas com encaixe de dentes (finger joint) para corte a laser (sem Qt).

Cada placa da caixa é desenhada no seu tamanho externo. Na aresta entre duas placas, a faixa de
largura = espessura é dividida em trechos alternados (um número ímpar de dentes): os trechos de uma
placa são "dentes" (o contorno vai até a borda) e os da outra são "vãos" (o contorno recua uma
espessura). Os cubinhos dos cantos pertencem à placa de maior prioridade (base > tampa > frente/fundo
> laterais), o que deixa as três placas que se encontram num canto sempre de acordo.

As divisórias internas se cruzam por meia-madeira (rasgos até a metade da altura) e têm dentes
embaixo que entram em furos na base. A compensação de kerf aumenta cada peça em kerf/2 para fora
(dentes mais largos, rasgos e furos mais estreitos → encaixe justo).

Coordenadas: x = largura, y = profundidade, z = altura; origem no canto frontal-esquerdo-inferior.
"""
from __future__ import annotations

import math
import os
from dataclasses import asdict, dataclass, field, fields
from typing import Optional

from shapely.geometry import Polygon, box as rect
from shapely.ops import unary_union

LID_OPEN = "aberta"        # sem tampa
LID_CLOSED = "fechada"     # tampa com dentes, como as outras faces
LID_LIFT = "solta"         # tampa solta: placa de cima + placa-guia por baixo e furo para o dedo
LID_TYPES = (LID_OPEN, LID_CLOSED, LID_LIFT)
LID_NAMES = {LID_OPEN: "Aberta (sem tampa)", LID_CLOSED: "Fechada (tampa fixa)", LID_LIFT: "Tampa solta (de encaixe)"}

CUT_LAYER, CUT_ACI = "CORTE", 7        # preto: a primeira camada do RDWorks
TEXT_LAYER, TEXT_ACI = "GRAVACAO", 5   # azul: nome das peças (opcional)

FACE_NAMES = {"base": "Base", "tampa": "Tampa", "frente": "Frente", "fundo": "Fundo (traseira)",
              "esquerda": "Lateral esquerda", "direita": "Lateral direita"}
PRIORITY = {"base": 5, "tampa": 4, "frente": 3, "fundo": 3, "esquerda": 2, "direita": 2}


@dataclass
class BoxParams:
    width: float = 150.0             # X (mm)
    depth: float = 120.0             # Y (mm)
    height: float = 60.0             # Z (mm) — com tampa solta, inclui a tampa
    inner: bool = False              # True: as medidas acima são internas (espaço útil)
    thickness: float = 3.0           # espessura do material
    finger: float = 10.0             # largura aproximada de cada dente
    kerf: float = 0.0                # largura do corte do laser (0 = sem compensação)
    lid: str = LID_OPEN
    cols: int = 1                    # compartimentos ao longo da largura (1 = sem divisórias)
    rows: int = 1                    # compartimentos ao longo da profundidade
    divider_clearance: float = 0.2   # folga total no comprimento das divisórias
    lid_clearance: float = 0.5       # folga de cada lado da placa-guia da tampa solta
    finger_hole: float = 22.0        # diâmetro do furo para o dedo na tampa solta (0 = sem furo)
    engrave_names: bool = False      # escrever o nome em cada peça (camada de gravação)
    quantity: int = 1
    material: str = ""               # vazio = "MDF {espessura}mm"

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> "BoxParams":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (d or {}).items() if k in names})

    def material_name(self) -> str:
        return self.material.strip() or f"MDF {self.thickness:g}mm"


@dataclass
class Dims:
    """Medidas resolvidas (mm)."""
    W: float          # largura externa do corpo
    D: float          # profundidade externa do corpo
    H: float          # altura externa do corpo (sem a tampa solta)
    Wi: float         # interno
    Di: float
    Hi: float         # altura útil interna (abaixo da tampa / placa-guia)
    total_h: float    # altura externa total (com a tampa solta)


@dataclass
class Panel:
    name: str
    kind: str                                  # "base", "frente", …, "divisoria", "tampa_solta", "guia"
    poly: Polygon                              # contorno nominal (sem kerf), coordenadas locais (u, v)
    circles: list = field(default_factory=list)  # furos redondos [(cu, cv, r)] (saem como CIRCLE)
    origin: tuple = (0.0, 0.0, 0.0)            # posição 3D do ponto (u=0, v=0, s=0)
    U: tuple = (1.0, 0.0, 0.0)
    V: tuple = (0.0, 1.0, 0.0)
    N: tuple = (0.0, 0.0, 1.0)                 # direção da espessura
    explode: tuple = (0.0, 0.0, 0.0)           # para onde a peça se afasta na vista explodida
    splits: list = field(default_factory=list)  # só para a vista 3D: cortes em u (cruzamentos de divisórias)

    @property
    def label(self) -> str:
        """Texto gravado: peças iguais levam o mesmo (senão o encaixe não as agrupa)."""
        if self.kind in ("frente", "fundo"):
            return "Frente/Fundo"
        if self.kind in ("esquerda", "direita"):
            return "Lateral"
        if self.kind == "divisoria":
            return self.name.rstrip("0123456789")
        return self.name.split(" (")[0]

    def cut_poly(self, kerf: float) -> Polygon:
        if kerf <= 0:
            return self.poly
        g = self.poly.buffer(kerf / 2.0, join_style=2, mitre_limit=10.0)
        return _largest(g)

    def cut_circles(self, kerf: float) -> list:
        return [(u, v, max(0.05, r - max(0.0, kerf) / 2.0)) for u, v, r in self.circles]

    def to3d(self, u: float, v: float, s: float = 0.0) -> tuple:
        o, U, V, N = self.origin, self.U, self.V, self.N
        return (o[0] + u * U[0] + v * V[0] + s * N[0],
                o[1] + u * U[1] + v * V[1] + s * N[1],
                o[2] + u * U[2] + v * V[2] + s * N[2])


@dataclass
class BoxResult:
    params: BoxParams
    dims: Dims
    panels: list
    warnings: list = field(default_factory=list)

    @property
    def area(self) -> float:
        """Área de material (mm²), sem contar furos."""
        return sum(p.poly.area for p in self.panels)

    def count(self) -> int:
        return len(self.panels)


# ---------------------------------------------------------------------------------------------- medidas
def resolve_dims(p: BoxParams) -> Dims:
    t = float(p.thickness)
    guide = t if p.lid == LID_LIFT else 0.0          # a placa-guia ocupa t no alto do interior
    if p.inner:
        Wi, Di, Hi = float(p.width), float(p.depth), float(p.height)
        W, D = Wi + 2 * t, Di + 2 * t
        H = Hi + t + guide + (t if p.lid == LID_CLOSED else 0.0)
    else:
        W, D = float(p.width), float(p.depth)
        H = float(p.height) - (t if p.lid == LID_LIFT else 0.0)
        Wi, Di = W - 2 * t, D - 2 * t
        Hi = H - t - guide - (t if p.lid == LID_CLOSED else 0.0)
    total = H + (t if p.lid == LID_LIFT else 0.0)
    return Dims(W, D, H, Wi, Di, Hi, total)


def validate(p: BoxParams) -> list[str]:
    """Erros que impedem gerar a caixa (mensagens para o operador)."""
    err = []
    t = p.thickness
    if p.lid not in LID_TYPES:
        err.append("Tipo de tampa desconhecido.")
    if t <= 0:
        err.append("A espessura do material precisa ser maior que zero.")
        return err
    if p.kerf < 0 or p.kerf > t / 2:
        err.append("O kerf precisa ficar entre 0 e metade da espessura.")
    if p.finger < t:
        err.append(f"A largura do dente precisa ser pelo menos a espessura ({t:g} mm).")
    if p.cols < 1 or p.rows < 1 or p.cols > 20 or p.rows > 20:
        err.append("Use de 1 a 20 compartimentos em cada direção.")
    d = resolve_dims(p)
    if min(d.Wi, d.Di) < 3 * t or d.Hi < 2 * t:
        err.append("A caixa ficou pequena demais para esta espessura: aumente as medidas.")
    else:
        cw = (d.Wi - (p.cols - 1) * t) / p.cols
        cd = (d.Di - (p.rows - 1) * t) / p.rows
        if min(cw, cd) < 2 * t:
            err.append("Compartimentos estreitos demais: diminua o número de divisórias.")
        if p.lid == LID_LIFT and p.finger_hole > 0 and p.finger_hole > min(d.Wi, d.Di) - 4 * t:
            err.append("O furo para o dedo não cabe na tampa: diminua o diâmetro.")
    if p.quantity < 1:
        err.append("A quantidade precisa ser pelo menos 1.")
    return err


# ---------------------------------------------------------------------------------------------- geração
def _largest(g) -> Polygon:
    if g.geom_type == "Polygon":
        return g
    return max(getattr(g, "geoms", []), key=lambda x: x.area)


def finger_segments(length: float, t: float, finger: float) -> list[tuple[float, float]]:
    """Trechos [(a, b)] do meio da aresta (entre os cantos), em número ímpar."""
    mid = length - 2 * t
    if mid <= 1e-9:
        return []
    n = int(mid // finger)
    if n % 2 == 0:
        n -= 1
    n = max(1, n)
    step = mid / n
    return [(t + k * step, t + (k + 1) * step) for k in range(n)]


def _panel_outline(w: float, h: float, t: float, finger: float, me: str, edges: dict) -> Polygon:
    """Contorno de uma face da caixa.

    ``edges``: {"b"|"r"|"t"|"l": nome do vizinho ou None}. b = v=0, t = v=h, l = u=0, r = u=w."""
    poly = rect(0, 0, w, h)
    cut = []
    pr = PRIORITY[me]

    def strip(edge, a, b):
        if edge == "b":
            return rect(a, 0, b, t)
        if edge == "t":
            return rect(a, h - t, b, h)
        if edge == "l":
            return rect(0, a, t, b)
        return rect(w - t, a, w, b)

    for edge, other in edges.items():
        if other is None:
            continue
        length = w if edge in ("b", "t") else h
        mine_first = pr > PRIORITY[other] or (pr == PRIORITY[other] and me < other)
        for k, (a, b) in enumerate(finger_segments(length, t, finger)):
            mine = (k % 2 == 0) == mine_first
            if not mine:
                cut.append(strip(edge, a, b))
    corners = {("b", "l"): (0, 0), ("b", "r"): (w - t, 0), ("t", "l"): (0, h - t), ("t", "r"): (w - t, h - t)}
    for (e1, e2), (x, y) in corners.items():
        group = [me] + [n for n in (edges.get(e1), edges.get(e2)) if n]
        owner = max(group, key=lambda n: (PRIORITY[n], n == me))
        # empate de prioridade entre dois vizinhos diferentes não acontece (cada canto tem 1 de cada eixo)
        if owner != me:
            cut.append(rect(x, y, x + t, y + t))
    if cut:
        poly = poly.difference(unary_union(cut))
    return _largest(poly).simplify(0)


def generate(p: BoxParams) -> BoxResult:
    errs = validate(p)
    if errs:
        raise ValueError(errs[0])
    t, f = float(p.thickness), float(p.finger)
    d = resolve_dims(p)
    W, D, H = d.W, d.D, d.H
    has_top = p.lid == LID_CLOSED
    top = "tampa" if has_top else None
    panels: list[Panel] = []

    def face(name, w, h, edges, origin, U, V, N, ex):
        panels.append(Panel(FACE_NAMES[name], name, _panel_outline(w, h, t, f, name, edges), [],
                            origin, U, V, N, ex))

    X, Y, Z = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)
    face("base", W, D, {"b": "frente", "t": "fundo", "l": "esquerda", "r": "direita"},
         (0, 0, 0), X, Y, Z, (0, 0, -1))
    if has_top:
        face("tampa", W, D, {"b": "frente", "t": "fundo", "l": "esquerda", "r": "direita"},
             (0, 0, H - t), X, Y, Z, (0, 0, 1.3))
    face("frente", W, H, {"b": "base", "t": top, "l": "esquerda", "r": "direita"},
         (0, 0, 0), X, Z, Y, (0, -1, 0))
    face("fundo", W, H, {"b": "base", "t": top, "l": "esquerda", "r": "direita"},
         (0, D - t, 0), X, Z, Y, (0, 1, 0))
    face("esquerda", D, H, {"b": "base", "t": top, "l": "frente", "r": "fundo"},
         (0, 0, 0), Y, Z, X, (-1, 0, 0))
    face("direita", D, H, {"b": "base", "t": top, "l": "frente", "r": "fundo"},
         (W - t, 0, 0), Y, Z, X, (1, 0, 0))

    # ---------------- divisórias
    base = panels[0]
    hd = d.Hi                                         # altura das divisórias = altura útil
    cw = (d.Wi - (p.cols - 1) * t) / p.cols
    cd = (d.Di - (p.rows - 1) * t) / p.rows
    xs = [t + i * cw + (i - 1) * t for i in range(1, p.cols)]   # x inicial de cada divisória "X"
    ys = [t + j * cd + (j - 1) * t for j in range(1, p.rows)]
    c = max(0.0, p.divider_clearance) + 2 * max(0.0, p.kerf)
    base_holes = []

    def tabs_between(stops: list[float], length: float) -> list[tuple[float, float]]:
        """Um dente embaixo no meio de cada vão entre cruzamentos."""
        edges = [0.0] + stops + [length]
        out = []
        for a, b in zip(edges[:-1], edges[1:]):
            span = b - a
            tw = min(f, 0.4 * span)
            if tw >= t * 0.8:
                m = (a + b) / 2
                out.append((m - tw / 2, m + tw / 2))
        return out

    Ly = d.Di - c                                     # comprimento das divisórias "X" (ao longo de y)
    oy = t + c / 2
    cross_y = [y - oy for y in ys]                    # início de cada rasgo, em u
    for i, x in enumerate(xs):
        g = rect(0, 0, Ly, hd)
        slots = [rect(u, hd / 2, u + t, hd + 1) for u in cross_y]
        stops = [u + t / 2 for u in cross_y]
        tabs = [rect(a, -t, b, 0) for a, b in tabs_between(stops, Ly)]
        g = unary_union([g] + tabs).difference(unary_union(slots)) if slots else unary_union([g] + tabs)
        for a, b in tabs_between(stops, Ly):
            base_holes.append(rect(x, oy + a, x + t, oy + b))
        panels.append(Panel(f"Divisória A{i + 1}", "divisoria", _largest(g).simplify(0), [],
                            (x, oy, t), Y, Z, X, (0, 0, 1.6), stops))
    Lx = d.Wi - c
    ox = t + c / 2
    cross_x = [x - ox for x in xs]
    for j, y in enumerate(ys):
        g = rect(0, 0, Lx, hd)
        slots = [rect(u, -t - 1, u + t, hd / 2) for u in cross_x]
        stops = [u + t / 2 for u in cross_x]
        tabs = [rect(a, -t, b, 0) for a, b in tabs_between(stops, Lx)]
        g = unary_union([g] + tabs)
        if slots:
            g = g.difference(unary_union(slots))
        for a, b in tabs_between(stops, Lx):
            base_holes.append(rect(ox + a, y, ox + b, y + t))
        panels.append(Panel(f"Divisória B{j + 1}", "divisoria", _largest(g).simplify(0), [],
                            (ox, y, t), X, Z, Y, (0, 0, 1.6), stops))
    if base_holes:
        base.poly = _largest(base.poly.difference(unary_union(base_holes))).simplify(0)

    # ---------------- tampa solta
    warnings = []
    if p.lid == LID_LIFT:
        lc = max(0.0, p.lid_clearance)
        gw, gd = d.Wi - 2 * lc, d.Di - 2 * lc
        r = p.finger_hole / 2 if p.finger_hole > 0 else 0
        hole_top = [(W / 2, D / 2, r)] if r else []
        panels.append(Panel("Tampa", "tampa_solta", rect(0, 0, W, D), hole_top,
                            (0, 0, H), X, Y, Z, (0, 0, 2.2)))
        hole_g = [(gw / 2, gd / 2, r)] if r else []
        panels.append(Panel("Guia da tampa (colar embaixo)", "guia", rect(0, 0, gw, gd), hole_g,
                            (t + lc, t + lc, H - t), X, Y, Z, (0, 0, 1.9)))
        if p.cols > 1 or p.rows > 1:
            warnings.append("Com tampa solta, as divisórias ficam uma espessura abaixo da borda (espaço da guia).")
    if p.finger * 2 > min(d.Hi, d.Wi, d.Di):
        warnings.append("Dentes largos para esta caixa: algumas arestas ficam com só um dente.")
    return BoxResult(p, d, panels, warnings)


# ---------------------------------------------------------------------------------------------- saída
def flat_layout(result: BoxResult, gap: float = 6.0, max_width: Optional[float] = None) -> list[tuple]:
    """Peças planificadas lado a lado: [(painel, polígono de corte já posicionado, círculos posicionados)]."""
    k = result.params.kerf
    items = []
    for pn in result.panels:
        g = pn.cut_poly(k)
        x0, y0, _, _ = g.bounds
        items.append((pn, g, x0, y0))
    widest = max(g.bounds[2] - g.bounds[0] for _, g, _, _ in items)
    limit = max(max_width or 0.0, widest, math.sqrt(sum(g.area for _, g, _, _ in items)) * 1.6)
    order = sorted(range(len(items)), key=lambda i: -(items[i][1].bounds[3] - items[i][1].bounds[1]))
    out = [None] * len(items)
    x = y = row_h = 0.0
    from shapely import affinity
    for i in order:
        pn, g, x0, y0 = items[i]
        w, h = g.bounds[2] - g.bounds[0], g.bounds[3] - g.bounds[1]
        if x > 0 and x + w > limit:
            x, y, row_h = 0.0, y + row_h + gap, 0.0
        dx, dy = x - x0, y - y0
        circles = [(u + dx, v + dy, r) for u, v, r in pn.cut_circles(k)]
        out[i] = (pn, affinity.translate(g, dx, dy), circles)
        x += w + gap
        row_h = max(row_h, h)
    return out


def write_dxf(result: BoxResult, path: str) -> str:
    """DXF R2000 em mm: contornos como LWPOLYLINE fechada, furos redondos como CIRCLE."""
    import ezdxf
    doc = ezdxf.new("R2000", setup=False)
    doc.header["$INSUNITS"] = 4
    doc.header["$MEASUREMENT"] = 1
    doc.layers.add(CUT_LAYER, color=CUT_ACI)
    if result.params.engrave_names:
        doc.layers.add(TEXT_LAYER, color=TEXT_ACI)
    msp = doc.modelspace()
    attrs = {"layer": CUT_LAYER, "color": CUT_ACI}
    for pn, g, circles in flat_layout(result):
        for ring in [g.exterior] + list(g.interiors):
            pts = [(round(x, 4), round(y, 4)) for x, y in list(ring.coords)[:-1]]
            msp.add_lwpolyline(pts, close=True, dxfattribs=attrs)
        for cx, cy, r in circles:
            msp.add_circle((cx, cy), r, dxfattribs=attrs)
        if result.params.engrave_names:
            pt = g.representative_point()
            hgt = max(2.0, min(6.0, (g.bounds[3] - g.bounds[1]) / 6))
            txt = msp.add_text(pn.label, height=hgt,
                               dxfattribs={"layer": TEXT_LAYER, "color": TEXT_ACI})
            txt.set_placement((pt.x, pt.y), align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    tmp = path + ".tmp"
    doc.saveas(tmp)
    os.replace(tmp, path)
    return path


def default_name(p: BoxParams) -> str:
    d = resolve_dims(p)
    return f"caixa_{d.W:g}x{d.D:g}x{d.total_h:g}_{p.thickness:g}mm".replace(".", ",")


def summary(result: BoxResult) -> dict:
    d = result.dims
    return {
        "externa": (d.W, d.D, d.total_h),
        "interna": (d.Wi, d.Di, d.Hi),
        "volume_l": d.Wi * d.Di * d.Hi / 1e6,
        "pecas": result.count(),
        "area_cm2": result.area / 100.0,
        "maior": max(((pn.poly.bounds[2] - pn.poly.bounds[0], pn.poly.bounds[3] - pn.poly.bounds[1])
                      for pn in result.panels), key=lambda s: s[0] * s[1]),
    }
