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
LID_CHEST = "bau"          # tampa baú: caixa rasa por cima, presa atrás por dois pinos (abre para trás)
LID_SLIDE = "deslizante"   # tampa que corre por um rasgo nas laterais (a frente é mais baixa)
LID_DOORS = "portas"       # porta dupla: duas abas presas por pinos nas laterais (abrem para os lados)
LID_TYPES = (LID_OPEN, LID_CLOSED, LID_LIFT, LID_CHEST, LID_SLIDE, LID_DOORS)
LID_NAMES = {LID_OPEN: "Aberta", LID_CLOSED: "Fechada", LID_LIFT: "Tampa solta",
             LID_CHEST: "Baú", LID_SLIDE: "Deslizante", LID_DOORS: "Porta dupla"}
LID_HELP = {
    LID_OPEN: "Só base e paredes, sem tampa.",
    LID_CLOSED: "Tampa com dentes como as outras faces (fica fechada/colada).",
    LID_LIFT: "Placa de cima com uma guia colada por baixo que encaixa na boca da caixa e furo para o dedo.",
    LID_CHEST: "Tampa rasa que abre para trás, presa por dois pinos (parafuso M3, palito ou filamento).",
    LID_SLIDE: "Tampa que desliza por um rasgo nas laterais; a frente é mais baixa para ela passar.",
    LID_DOORS: "Duas abas que abrem para os lados, cada uma presa por dois pinos.",
}
HINGED = (LID_CHEST, LID_DOORS)

JOINT_FINGER = "dentes"    # encaixe de dentes (finger joint)
JOINT_FLAT = "lisa"        # aresta lisa, para colar
JOINT_NAMES = {JOINT_FINGER: "Dentes", JOINT_FLAT: "Lisa (colar)"}

CUT_LAYER, CUT_ACI = "CORTE", 7        # preto: a primeira camada do RDWorks
TEXT_LAYER, TEXT_ACI = "GRAVACAO", 5   # azul: nome das peças (opcional)

FACE_NAMES = {"base": "Base", "tampa": "Tampa", "frente": "Frente", "fundo": "Fundo (traseira)",
              "esquerda": "Lateral esquerda", "direita": "Lateral direita"}
PRIORITY = {"base": 5, "tampa": 4, "frente": 3, "fundo": 3, "esquerda": 2, "direita": 2,
            # tampas que são caixas rasas (baú, portas): mesma regra, entre as peças da própria tampa
            "bau_topo": 5, "bau_frente": 3, "bau_lado": 2, "porta_topo": 5, "porta_aba": 3}


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
    lid_clearance: float = 0.5       # folga: guia da tampa solta, abas do baú/portas, rasgo da deslizante
    finger_hole: float = 22.0        # diâmetro do furo para o dedo (tampa solta/deslizante; 0 = sem furo)
    lid_height: float = 20.0         # altura das abas da tampa baú / porta dupla
    pin: float = 3.2                 # diâmetro do furo dos pinos (baú / porta dupla)
    joint: str = JOINT_FINGER
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
    total_h: float    # altura externa total (com a tampa solta / baú / portas)
    total_w: float = 0.0   # largura e profundidade totais (a tampa baú/portas passa um pouco do corpo)
    total_d: float = 0.0
    front_h: float = 0.0   # altura da frente (só a deslizante tem frente mais baixa)


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
    # só para a vista 3D: como a peça se mexe ao abrir a tampa —
    # ("gira", pivô (x, y, z), eixo, ângulo em graus) ou ("move", (dx, dy, dz))
    motion: Optional[tuple] = None

    @property
    def label(self) -> str:
        """Texto gravado: peças iguais levam o mesmo (senão o encaixe não as agrupa)."""
        if self.kind in ("frente", "fundo"):
            return "Frente/Fundo"
        if self.kind in ("esquerda", "direita"):
            return "Lateral"
        if self.kind == "divisoria":
            return self.name.rstrip("0123456789")
        if self.kind == "bau_lado":
            return "Tampa: lateral"
        if self.kind == "porta_aba":
            return "Porta: aba"
        if self.kind == "porta_topo":
            return "Porta"
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
def slide_lip(t: float) -> float:
    """Altura da faixa das laterais acima do rasgo da tampa deslizante."""
    return max(5.0, 2 * t)


def chest_front_gap(D: float, vp: float, c: float) -> float:
    """Folga na frente da tampa baú. A aba da frente gira em volta de um pino lá atrás: um ponto da
    aba na altura do pino descreve um arco que cruza a face da frente (distância L = D + vp do pino)
    a vp²/… acima — para passar por cima da caixa é preciso folga ≥ vp² / (2·L)."""
    return max(c, vp * vp / (2 * (D + vp)) * 1.2 + 0.2)


def resolve_dims(p: BoxParams) -> Dims:
    """Medidas externas = corpo (largura × profundidade) e altura total; internas = espaço útil."""
    t, c = float(p.thickness), max(0.0, float(p.lid_clearance))
    on_top = t if p.lid in (LID_LIFT, LID_CHEST, LID_DOORS) else 0.0   # tampa apoiada em cima do corpo
    # o que fica entre o espaço útil e o alto do corpo
    above = {LID_OPEN: 0.0, LID_CLOSED: t, LID_LIFT: t, LID_CHEST: 0.0, LID_DOORS: 0.0,
             LID_SLIDE: t + c + slide_lip(t)}.get(p.lid, 0.0)
    if p.inner:
        Wi, Di, Hi = float(p.width), float(p.depth), float(p.height)
        W, D = Wi + 2 * t, Di + 2 * t
        H = Hi + t + above
    else:
        W, D = float(p.width), float(p.depth)
        H = float(p.height) - on_top
        Wi, Di = W - 2 * t, D - 2 * t
        Hi = H - t - above
    total_w, total_d = W, D
    vp = (float(p.lid_height) - t) / 2                 # pino das dobradiças: fica 2·vp para fora do corpo
    if p.lid == LID_CHEST:
        total_w, total_d = W + 2 * (t + c), D + 2 * vp + t + chest_front_gap(D, vp, c)
    elif p.lid == LID_DOORS:
        total_w, total_d = W + 4 * vp, D + 2 * (t + c)
    front_h = H - t - c - slide_lip(t) if p.lid == LID_SLIDE else H
    return Dims(W, D, H, Wi, Di, Hi, H + on_top, total_w, total_d, front_h)


def validate(p: BoxParams) -> list[str]:
    """Erros que impedem gerar a caixa (mensagens para o operador)."""
    err = []
    t = p.thickness
    if p.lid not in LID_TYPES:
        err.append("Tipo de tampa desconhecido.")
    if p.joint not in JOINT_NAMES:
        err.append("Tipo de junta desconhecido.")
    if t <= 0:
        err.append("A espessura do material precisa ser maior que zero.")
        return err
    if p.kerf < 0 or p.kerf > t / 2:
        err.append("O kerf precisa ficar entre 0 e metade da espessura.")
    if p.joint == JOINT_FINGER and p.finger < t:
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
        if p.lid in (LID_LIFT, LID_SLIDE) and p.finger_hole > 0 and p.finger_hole > min(d.Wi, d.Di) - 4 * t:
            err.append("O furo para o dedo não cabe na tampa: diminua o diâmetro.")
        if p.lid in HINGED:
            vp = (p.lid_height - t) / 2                 # centro do pino abaixo do topo
            c_ = p.lid_clearance
            if p.pin <= 0:
                err.append("Informe o diâmetro do pino da dobradiça.")
            elif vp - p.pin / 2 < t + 1:
                err.append(f"Abas da tampa baixas demais para o pino: use pelo menos "
                           f"{_ceil(t + 2 * (t + 1) + p.pin)} mm de altura de aba.")
            elif p.lid_height > d.H - t or d.H - 2 * vp - 1.5 < 3 * t:
                err.append("As abas da tampa estão altas demais para esta caixa: diminua a altura das abas.")
            elif c_ < 0.2:
                err.append("Use pelo menos 0,2 mm de folga entre a tampa e a caixa.")
            elif p.lid == LID_DOORS and d.W / 2 < 2 * p.lid_height:
                err.append("Caixa estreita demais para porta dupla com abas desta altura.")
    if p.quantity < 1:
        err.append("A quantidade precisa ser pelo menos 1.")
    return err


# ---------------------------------------------------------------------------------------------- geração
def _ceil(v: float) -> str:
    return f"{math.ceil(v * 2) / 2:g}".replace(".", ",")


def _largest(g) -> Polygon:
    if g.geom_type == "Polygon":
        return g
    return max(getattr(g, "geoms", []), key=lambda x: x.area)


def finger_segments(length: float, t: float, finger: float) -> list[tuple[float, float]]:
    """Trechos [(a, b)] do meio da aresta (entre os cantos), em número ímpar.

    ``finger`` infinito = junta lisa: um trecho só, todo da placa de maior prioridade."""
    mid = length - 2 * t
    if mid <= 1e-9:
        return []
    if math.isinf(finger):
        return [(t, length - t)]
    n = int(mid // finger)
    if n % 2 == 0:
        n -= 1
    n = max(1, n)
    step = mid / n
    return [(t + k * step, t + (k + 1) * step) for k in range(n)]


def _wins(me: str, other: str) -> bool:
    """A placa ``me`` fica com o material disputado com ``other``?"""
    pa, pb = PRIORITY[me], PRIORITY[other]
    return pa > pb or (pa == pb and me < other)


def _panel_outline(w: float, h: float, t: float, finger: float, me: str, edges: dict) -> Polygon:
    """Contorno de uma face da caixa.

    ``edges``: {"b"|"r"|"t"|"l": vizinho} com b = v=0, t = v=h, l = u=0, r = u=w. O vizinho é None
    (aresta livre), o nome da placa (encaixe na aresta toda) ou (nome, a, b): encaixe só no trecho
    [a, b] da aresta — o resto da faixa fica com esta placa (ex.: lateral da tampa deslizante, cuja
    frente é mais baixa)."""
    poly = rect(0, 0, w, h)
    cut = []

    def strip(edge, a, b):
        if edge == "b":
            return rect(a, 0, b, t)
        if edge == "t":
            return rect(a, h - t, b, h)
        if edge == "l":
            return rect(0, a, t, b)
        return rect(w - t, a, w, b)

    def spec(edge):
        sp = edges.get(edge)
        length = w if edge in ("b", "t") else h
        if sp is None:
            return None
        if isinstance(sp, str):
            return sp, 0.0, length, length
        return sp[0], float(sp[1]), float(sp[2]), length

    for edge in edges:
        sp = spec(edge)
        if sp is None:
            continue
        other, a, b, length = sp
        mine_first = _wins(me, other)
        for k, (sa, sb) in enumerate(finger_segments(b - a, t, finger)):
            if (k % 2 == 0) != mine_first:
                cut.append(strip(edge, a + sa, a + sb))
        # ponta do trecho que não é canto da placa: só as duas placas disputam o cubinho
        ends = ([a] if a > 1e-9 else []) + ([b - t] if b < length - 1e-9 else [])
        for e0 in ends:
            if not _wins(me, other):
                cut.append(strip(edge, e0, e0 + t))

    def covers(edge, at_start: bool):
        sp = spec(edge)
        if sp is None:
            return None
        other, a, b, length = sp
        ok = a <= 1e-9 if at_start else b >= length - 1e-9
        return other if ok else None

    corners = {("b", "l"): ((0, 0), True, True), ("b", "r"): ((w - t, 0), False, True),
               ("t", "l"): ((0, h - t), True, False), ("t", "r"): ((w - t, h - t), False, False)}
    for (e1, e2), ((x, y), s1, s2) in corners.items():
        # s1: o canto fica no começo da aresta e1 (u=0)? s2: no começo da aresta e2 (v=0)?
        group = [me] + [n for n in (covers(e1, s1), covers(e2, s2)) if n]
        owner = max(group, key=lambda n: (PRIORITY[n], n == me))
        if owner != me:
            cut.append(rect(x, y, x + t, y + t))
    if cut:
        poly = poly.difference(unary_union(cut))
    return _largest(poly).simplify(0)


def generate(p: BoxParams) -> BoxResult:
    errs = validate(p)
    if errs:
        raise ValueError(errs[0])
    t = float(p.thickness)
    f = float(p.finger) if p.joint == JOINT_FINGER else math.inf
    d = resolve_dims(p)
    W, D, H = d.W, d.D, d.H
    c = max(0.0, float(p.lid_clearance))
    has_top = p.lid == LID_CLOSED
    top = "tampa" if has_top else None
    panels: list[Panel] = []

    def face(name, w, h, edges, origin, U, V, N, ex, kind=None, title=None):
        pn = Panel(title or FACE_NAMES[name], kind or name, _panel_outline(w, h, t, f, name, edges), [],
                   origin, U, V, N, ex)
        panels.append(pn)
        return pn

    X, Y, Z = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)
    base = face("base", W, D, {"b": "frente", "t": "fundo", "l": "esquerda", "r": "direita"},
                (0, 0, 0), X, Y, Z, (0, 0, -1))
    if has_top:
        face("tampa", W, D, {"b": "frente", "t": "fundo", "l": "esquerda", "r": "direita"},
             (0, 0, H - t), X, Y, Z, (0, 0, 1.3))
    Hf = d.front_h                                     # deslizante: a frente fica abaixo do rasgo
    front_spec = "frente" if Hf >= H - 1e-9 else ("frente", 0.0, Hf)
    front = face("frente", W, Hf, {"b": "base", "t": top, "l": "esquerda", "r": "direita"},
                 (0, 0, 0), X, Z, Y, (0, -1, 0))
    back = face("fundo", W, H, {"b": "base", "t": top, "l": "esquerda", "r": "direita"},
                (0, D - t, 0), X, Z, Y, (0, 1, 0))
    left = face("esquerda", D, H, {"b": "base", "t": top, "l": front_spec, "r": "fundo"},
                (0, 0, 0), Y, Z, X, (-1, 0, 0))
    right = face("direita", D, H, {"b": "base", "t": top, "l": front_spec, "r": "fundo"},
                 (W - t, 0, 0), Y, Z, X, (1, 0, 0))
    warnings = []

    # ---------------- tampa deslizante: rasgo nas laterais, aberto na frente, até o fundo
    if p.lid == LID_SLIDE:
        # o rasgo termina uma espessura antes do fundo: a faixa de cima continua presa à lateral
        # por material inteiro (se fosse até o fundo, ela ficaria pendurada só nos dentes)
        slot_end = D - 2 * t
        slot = rect(-1, Hf, slot_end, Hf + t + c)
        for side in (left, right):
            side.poly = _largest(side.poly.difference(slot)).simplify(0)
        lw, ld = W - 1.0, slot_end - c / 2
        r = p.finger_hole / 2 if p.finger_hole > 0 else 0
        holes = [(lw / 2, min(ld / 2, r + 3 * t), r)] if r else []
        slide = Panel("Tampa deslizante", "deslizante", rect(0, 0, lw, ld), holes,
                      (0.5, 0, Hf + c / 2), X, Y, Z, (0, -1.2, 0.4))
        slide.motion = ("move", (0.0, -(D - t) * 0.8, 0.0))
        panels.append(slide)

    # ---------------- tampas com dobradiça de pino (baú e porta dupla)
    # O pino fica FORA do corpo, numa orelha da parede (atrás do fundo no baú, ao lado das laterais
    # nas portas): assim tudo o que está sobre a caixa fica à frente do pino e sobe ao abrir. Com o
    # pino dentro da aba, a parte da tampa sobre a parede desceria e bateria nela.
    # A aresta parede/orelha deixa de ter dentes perto do topo (a orelha precisa de material inteiro).
    if p.lid in HINGED:
        from shapely.geometry import Point
        hl = float(p.lid_height)
        vp = (hl - t) / 2                               # pino: no meio da aba, abaixo da placa de cima
        R = vp - 0.5                                    # raio da orelha (fica 0,5 mm abaixo da tampa)
        e = vp                                          # distância do pino até a face de fora do corpo
        pr = float(p.pin) / 2
        zp = H - vp                                     # altura do pino
        z0 = H + t - hl                                 # base das abas
        Hj = zp - R - 1                                 # acima disto a aresta é lisa (orelha)

        def ear(cu):                                    # orelha em volta do pino, colada na parede
            a, b = (cu - e, cu) if cu > 0 else (cu, cu + e)
            a, b = min(a, b), max(a, b)
            return unary_union([rect(a, zp - R, b, zp + R), Point(cu, zp).buffer(R, quad_segs=24)])

        def regen(pn, w, h, name, edges):
            pn.poly = _panel_outline(w, h, t, f, name, edges)

        if p.lid == LID_CHEST:
            # laterais do corpo: aresta com o fundo só até Hj; orelha atrás do fundo
            for side, nm in ((left, "esquerda"), (right, "direita")):
                regen(side, D, H, nm, {"b": "base", "l": front_spec, "r": ("fundo", 0.0, Hj)})
                side.poly = _largest(unary_union([side.poly, ear(D + e)])).simplify(0)
                side.circles.append((D + e, zp, pr))
            regen(back, W, H, "fundo", {"b": "base", "l": ("esquerda", 0.0, Hj), "r": ("direita", 0.0, Hj)})
            back.poly = _largest(back.poly.difference(
                unary_union([rect(-1, Hj, t, H + 1), rect(W - t, Hj, W + 1, H + 1)]))).simplify(0)
            cf = chest_front_gap(D, vp, c)              # folga da aba da frente (ver chest_front_gap)
            Wl, Dl = W + 2 * (t + c), D + t + cf        # placa de cima: até a face de trás do corpo
            Ls = Dl + e + vp                            # abas laterais: vão até o pino e arredondam
            x0, y0 = -(t + c), -(t + cf)
            mot = ("gira", (0.0, D + e, zp), X, -110.0)
            parts = [
                face("bau_topo", Wl, Dl, {"b": "bau_frente", "l": "bau_lado", "r": "bau_lado"},
                     (x0, y0, H), X, Y, Z, (0, 0, 1.8), "bau_topo", "Tampa baú"),
                face("bau_frente", Wl, hl, {"t": "bau_topo", "l": "bau_lado", "r": "bau_lado"},
                     (x0, y0, z0), X, Z, Y, (0, -0.6, 1.8), "bau_frente", "Tampa baú: frente"),
            ]
            for side_x, nm in ((x0, "esquerda"), (W + c, "direita")):
                sk = face("bau_lado", Ls, hl, {"t": ("bau_topo", 0.0, Dl), "l": "bau_frente"},
                          (side_x, y0, z0), Y, Z, X, (-0.6 if side_x < 0 else 0.6, 0, 1.8), "bau_lado",
                          f"Tampa baú: lateral {nm}")
                pu = Ls - vp
                keep = unary_union([rect(-1, -1, pu, hl + 1), Point(pu, vp).buffer(vp, quad_segs=24)])
                sk.poly = _largest(sk.poly.intersection(keep)).simplify(0.01)
                sk.circles.append((pu, vp, pr))
                parts.append(sk)
            for pn in parts:
                pn.motion = mot
        else:
            # frente e fundo do corpo: aresta com as laterais só até Hj; orelhas dos dois lados
            for wall, nm in ((front, "frente"), (back, "fundo")):
                regen(wall, W, H, nm, {"b": "base", "l": ("esquerda", 0.0, Hj), "r": ("direita", 0.0, Hj)})
                wall.poly = _largest(unary_union([wall.poly, ear(-e), ear(W + e)])).simplify(0)
                wall.circles += [(-e, zp, pr), (W + e, zp, pr)]
            for side, nm in ((left, "esquerda"), (right, "direita")):
                regen(side, D, H, nm, {"b": "base", "l": ("frente", 0.0, Hj), "r": ("fundo", 0.0, Hj)})
                side.poly = _largest(side.poly.difference(
                    unary_union([rect(-1, Hj, t, H + 1), rect(D - t, Hj, D + 1, H + 1)]))).simplify(0)
            Lw, Dl = W / 2 - c / 2, D + 2 * (t + c)
            Ls = Lw + e + vp
            for k, nm in enumerate(("esquerda", "direita")):
                is_left = k == 0
                xt = 0.0 if is_left else W / 2 + c / 2               # placa de cima da porta
                xs = -(e + vp) if is_left else xt                    # aba: passa da caixa até o pino
                top_rng = (Ls - Lw, Ls) if is_left else (0.0, Lw)
                pu = vp if is_left else Ls - vp                      # pino, na coordenada da aba
                mot = ("gira", ((-e if is_left else W + e), 0.0, zp), Y, -110.0 if is_left else 110.0)
                sgn = -1 if is_left else 1
                top_p = face("porta_topo", Lw, Dl, {"b": "porta_aba", "t": "porta_aba"},
                             (xt, -(t + c), H), X, Y, Z, (sgn * 0.7, 0, 1.8), "porta_topo", f"Porta {nm}")
                top_p.motion = mot
                for y_sk, lado in ((-(t + c), "frente"), (D + c, "fundo")):
                    sk = face("porta_aba", Ls, hl, {"t": ("porta_topo",) + top_rng}, (xs, y_sk, z0), X, Z, Y,
                              (sgn * 0.7, -0.5 if lado == "frente" else 0.5, 1.8), "porta_aba",
                              f"Porta {nm}: aba {lado}")
                    if is_left:
                        keep = unary_union([rect(pu, -1, Ls + 1, hl + 1), Point(pu, vp).buffer(vp, quad_segs=24)])
                    else:
                        keep = unary_union([rect(-1, -1, pu, hl + 1), Point(pu, vp).buffer(vp, quad_segs=24)])
                    sk.poly = _largest(sk.poly.intersection(keep)).simplify(0.01)
                    sk.circles.append((pu, vp, pr))
                    sk.motion = mot
        pin_txt = f"{p.pin:g}".replace(".", ",")
        warnings.append(f"Dobradiça: use pinos de {pin_txt} mm (parafuso M3 com porca, palito ou filamento).")

    # ---------------- divisórias
    hd = d.Hi                                         # altura das divisórias = altura útil
    cw = (d.Wi - (p.cols - 1) * t) / p.cols
    cd = (d.Di - (p.rows - 1) * t) / p.rows
    xs = [t + i * cw + (i - 1) * t for i in range(1, p.cols)]   # x inicial de cada divisória "X"
    ys = [t + j * cd + (j - 1) * t for j in range(1, p.rows)]
    cl = max(0.0, p.divider_clearance) + 2 * max(0.0, p.kerf)
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

    Ly = d.Di - cl                                    # comprimento das divisórias "X" (ao longo de y)
    oy = t + cl / 2
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
    Lx = d.Wi - cl
    ox = t + cl / 2
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
    if p.lid == LID_LIFT:
        lc = max(0.0, p.lid_clearance)
        gw, gd = d.Wi - 2 * lc, d.Di - 2 * lc
        r = p.finger_hole / 2 if p.finger_hole > 0 else 0
        hole_top = [(W / 2, D / 2, r)] if r else []
        lift = ("move", (0.0, 0.0, max(20.0, H * 0.6)))          # vista 3D: a tampa sobe ao abrir
        panels.append(Panel("Tampa", "tampa_solta", rect(0, 0, W, D), hole_top,
                            (0, 0, H), X, Y, Z, (0, 0, 2.2), motion=lift))
        hole_g = [(gw / 2, gd / 2, r)] if r else []
        panels.append(Panel("Guia da tampa (colar embaixo)", "guia", rect(0, 0, gw, gd), hole_g,
                            (t + lc, t + lc, H - t), X, Y, Z, (0, 0, 1.9), motion=lift))
        if p.cols > 1 or p.rows > 1:
            warnings.append("Com tampa solta, as divisórias ficam uma espessura abaixo da borda (espaço da guia).")
    if p.lid == LID_SLIDE and (p.cols > 1 or p.rows > 1):
        warnings.append("Com tampa deslizante, as divisórias ficam abaixo do rasgo da tampa.")
    if p.joint == JOINT_FINGER and p.finger * 2 > min(d.Hi, d.Wi, d.Di):
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
    return (f"caixa_{p.lid}_{d.total_w or d.W:g}x{d.total_d or d.D:g}x{d.total_h:g}_{p.thickness:g}mm"
            .replace(".", ","))


def summary(result: BoxResult) -> dict:
    d = result.dims
    return {
        "externa": (d.total_w or d.W, d.total_d or d.D, d.total_h),
        "interna": (d.Wi, d.Di, d.Hi),
        "volume_l": d.Wi * d.Di * d.Hi / 1e6,
        "pecas": result.count(),
        "area_cm2": result.area / 100.0,
        "maior": max(((pn.poly.bounds[2] - pn.poly.bounds[0], pn.poly.bounds[3] - pn.poly.bounds[1])
                      for pn in result.panels), key=lambda s: s[0] * s[1]),
    }
