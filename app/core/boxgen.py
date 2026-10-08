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
from dataclasses import asdict, dataclass, field, fields, replace
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
    LID_CHEST: "Tampa que abre para trás numa dobradiça de MDF (nó redondo + disco), sem parafuso.",
    LID_SLIDE: "Tampa que desliza por um rasgo nas laterais; a frente é mais baixa para ela passar.",
    LID_DOORS: "Duas portas que abrem para os lados, cada uma numa dobradiça de MDF (nó + disco).",
}
HINGED = (LID_CHEST, LID_DOORS)

JOINT_FINGER = "dentes"    # encaixe de dentes (finger joint)
JOINT_FLAT = "lisa"        # aresta lisa, para colar
JOINT_NAMES = {JOINT_FINGER: "Dentes", JOINT_FLAT: "Lisa (colar)"}

# ---- modelos (passo 0 do painel): cada um reaproveita o mesmo motor de faces com dentes
MODEL_BOX = "caixa"          # caixa com 6 tipos de tampa
MODEL_DRAWER = "gaveta"      # móvel aberto na frente + gaveta que corre dentro (frente falsa e puxador)
MODEL_ELEC = "eletronica"    # caixa de eletrônica: tampa parafusada (T-slot M3), furos da placa, cabo, ventilação
MODEL_TRAY = "bandeja"       # bandeja organizadora baixa, com divisórias e rampa opcional
MODEL_KERF = "kerf"          # pente de teste para descobrir o kerf do laser
MODELS = (MODEL_BOX, MODEL_DRAWER, MODEL_ELEC, MODEL_TRAY, MODEL_KERF)
MODEL_NAMES = {MODEL_BOX: "Caixa", MODEL_DRAWER: "Gaveta", MODEL_ELEC: "Eletrônica", MODEL_TRAY: "Bandeja",
               MODEL_KERF: "Teste de kerf"}
MODEL_HELP = {
    MODEL_BOX: "Caixa com encaixe de dentes e seis tipos de tampa.",
    MODEL_DRAWER: "Móvel aberto na frente com uma gaveta que corre dentro. As medidas são as do móvel.",
    MODEL_ELEC: "Caixa para projeto de eletrônica: tampa presa com parafusos M3, furos para a placa, "
                "cabo e ventilação.",
    MODEL_TRAY: "Bandeja baixa para organizar peças, com divisórias e rampa para pegar parafusos.",
    MODEL_KERF: "Pente com rasgos de larguras diferentes para descobrir o kerf do laser.",
}

# placas para a caixa de eletrônica: (nome, largura, altura, furos (x, y) a partir do canto, Ø do furo)
BOARDS = {
    "": ("Nenhuma", 0.0, 0.0, [], 0.0),
    "uno": ("Arduino Uno", 68.6, 53.3, [(13.97, 2.54), (15.24, 50.8), (66.04, 7.62), (66.04, 35.56)], 3.2),
    "mega": ("Arduino Mega", 101.6, 53.3, [(13.97, 2.54), (15.24, 50.8), (66.04, 7.62), (66.04, 35.56),
                                           (90.17, 50.8), (96.52, 2.54)], 3.2),
    "rpi": ("Raspberry Pi", 85.0, 56.0, [(3.5, 3.5), (61.5, 3.5), (3.5, 52.5), (61.5, 52.5)], 2.75),
}
PULLS = {"vazado": "Vazado", "furo": "Furo p/ puxador", "nenhum": "Nenhum"}
KERF_STEPS = (0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30)
ELEC_LIP = 2.0               # a tampa parafusada passa 2 mm das paredes (o furo não encosta na borda)
STANDOFF = 5.0               # altura dos espaçadores da placa (só para a vista 3D)

CUT_LAYER, CUT_ACI = "CORTE", 7        # preto: a primeira camada do RDWorks
TEXT_LAYER, TEXT_ACI = "GRAVACAO", 5   # azul: nome das peças (opcional)

FACE_NAMES = {"base": "Base", "tampa": "Tampa", "frente": "Frente", "fundo": "Fundo (traseira)",
              "esquerda": "Lateral esquerda", "direita": "Lateral direita"}
PRIORITY = {"base": 5, "tampa": 4, "frente": 3, "fundo": 3, "esquerda": 2, "direita": 2,
            # tampas que são caixas rasas (baú, portas): mesma regra, entre as peças da própria tampa
            "bau_topo": 5, "bau_dobradica": 3, "bau_frente": 3, "bau_lado": 2,
            "porta_topo": 5, "porta_dobradica": 3, "porta_frente": 3, "porta_lado": 2}


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
    lid_height: float = 24.0         # altura da tampa baú / porta dupla (parte que abre)
    pin: float = 3.2                 # (sem uso: a dobradiça agora é de MDF) — mantido para projetos antigos
    joint: str = JOINT_FINGER
    engrave_names: bool = False      # escrever o nome em cada peça (camada de gravação)
    model: str = MODEL_BOX
    handles: bool = False            # alças vazadas nas laterais (caixa / bandeja)
    ramp: bool = False               # bandeja: rampa na frente de cada compartimento
    board: str = ""                  # eletrônica: placa ("uno", "mega", "rpi")
    cable_hole: float = 0.0          # eletrônica: Ø do furo do cabo no fundo (0 = sem)
    vents: bool = False              # eletrônica: rasgos de ventilação nas laterais
    screw_len: float = 16.0          # eletrônica: comprimento do parafuso M3 da tampa
    pull: str = "vazado"             # gaveta: puxador
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
    hinge_wall_h: float = 0.0   # baú/portas: altura da parede do corpo do lado da dobradiça


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
    ghost: bool = False                        # só aparece na vista 3D (ex.: a placa do Arduino); não é cortada
    texts: list = field(default_factory=list)  # textos gravados [(u, v, texto, altura)] (ex.: teste de kerf)
    engrave: str = ""                          # nome gravado (quando o padrão não serve)

    @property
    def label(self) -> str:
        """Texto gravado: peças iguais levam o mesmo (senão o encaixe não as agrupa)."""
        if self.engrave:
            return self.engrave
        if self.kind in ("frente", "fundo"):
            return "Frente/Fundo"
        if self.kind in ("esquerda", "direita"):
            return "Lateral"
        if self.kind == "divisoria":
            return self.name.rstrip("0123456789")
        if self.kind == "bau_lado":
            return "Tampa: lateral"
        if self.kind == "porta_lado":
            return "Porta: lateral"
        if self.kind == "porta_topo":
            return "Porta"
        if self.kind == "porta_dobradica":
            return "Porta: dobradiça"
        if self.kind == "disco":
            return "Disco"
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
    def cut_panels(self) -> list:
        """Peças que vão para o laser (sem as de mostruário, como a placa do Arduino)."""
        return [pn for pn in self.panels if not pn.ghost]

    @property
    def area(self) -> float:
        """Área de material (mm²), sem contar furos."""
        return sum(p.poly.area for p in self.cut_panels)

    def count(self) -> int:
        return len(self.cut_panels)


# ---------------------------------------------------------------------------------------------- medidas
def slide_lip(t: float) -> float:
    """Altura da faixa das laterais acima do rasgo da tampa deslizante."""
    return max(5.0, 2 * t)


def hinge_geom(t: float, kerf: float = 0.0) -> dict:
    """Medidas da dobradiça integrada de MDF (mesmo princípio do ChestHinge do boxes.py).

    Na parede do corpo perpendicular ao eixo há um NÓ redondo (raio R) com um furo (raio p); dentro do
    furo gira um DISCO solto com um furo retangular t × ph; a parede da tampa do lado da dobradiça tem,
    em cada ponta, uma LINGUETA t × ph que entra nesse retângulo — o disco gira junto com a tampa.
    g = folga entre tampa e corpo; gp = folga do disco no furo (cobre o kerf)."""
    pr = 2.0 * t
    s_ = 1.0 * t
    g = max(0.3, 0.1 * t)
    ph = math.sqrt((0.9 * pr) ** 2 - t ** 2)            # altura da lingueta (cabe folgada dentro do disco)
    return {"p": pr, "R": pr + s_, "g": g, "ph": ph, "gp": max(0.25, 0.1 * t, kerf + 0.15),
            "rho": math.hypot(t / 2, ph / 2)}


def hinge_heights(p: BoxParams, total: float) -> tuple:
    """(Hb, Hbw): altura das paredes do corpo (linha da tampa) e da parede do lado da dobradiça."""
    t = float(p.thickness)
    hg = hinge_geom(t, p.kerf)
    Hb = total - float(p.lid_height)
    return Hb, Hb - hg["rho"] - hg["g"]


def board_size(p: BoxParams) -> tuple:
    """(largura, altura, furos, Ø do furo) da placa escolhida."""
    name, bw, bh, holes, dia = BOARDS.get(p.board, BOARDS[""])
    return bw, bh, holes, dia


def kerf_comb_size(t: float) -> tuple:
    pitch = max(12.0, 3 * t)
    depth = max(12.0, 4 * t)
    return pitch, depth, 2 * 8.0 + pitch * len(KERF_STEPS), depth + 14.0


def resolve_dims(p: BoxParams) -> Dims:
    """Medidas resolvidas de cada modelo (externas = o que se vê por fora; internas = espaço útil)."""
    t, c = float(p.thickness), max(0.0, float(p.lid_clearance))
    if p.model == MODEL_KERF:
        _, _, cw, ch = kerf_comb_size(t)
        return Dims(cw, t, ch, 0.0, 0.0, 0.0, ch, cw, t, ch)
    if p.model == MODEL_DRAWER:
        # móvel W×D×H; gaveta (Wd × Dd × Hd) com folga c dos lados e em cima; frente falsa na frente do móvel
        if p.inner:
            Wi, Di, Hi = float(p.width), float(p.depth), float(p.height)
            W, D, H = Wi + 4 * t + 2 * c, Di + 3 * t + c, Hi + 3 * t + c
        else:
            W, D, H = float(p.width), float(p.depth), float(p.height)
            Wi, Di, Hi = W - 4 * t - 2 * c, D - 3 * t - c, H - 3 * t - c
        return Dims(W, D, H, Wi, Di, Hi, H, W, D + t, H)
    if p.model == MODEL_ELEC:
        # paredes de altura Hw; a tampa (placa lisa parafusada) fica por cima e passa ELEC_LIP das paredes
        if p.inner:
            Wi, Di, Hi = float(p.width), float(p.depth), float(p.height)
            W, D, Hw = Wi + 2 * t, Di + 2 * t, Hi + t
        else:
            W, D = float(p.width), float(p.depth)
            Hw = float(p.height) - t
            Wi, Di, Hi = W - 2 * t, D - 2 * t, Hw - t
        return Dims(W, D, Hw, Wi, Di, Hi, Hw + t, W + 2 * ELEC_LIP, D + 2 * ELEC_LIP, Hw)
    if p.model == MODEL_TRAY:
        p = replace(p, lid=LID_OPEN)
    return _box_dims(p)


def _box_dims(p: BoxParams) -> Dims:
    """Medidas externas = corpo (largura × profundidade) e altura total; internas = espaço útil."""
    t, c = float(p.thickness), max(0.0, float(p.lid_clearance))
    if p.lid in HINGED:
        return _hinged_dims(p)
    on_top = t if p.lid == LID_LIFT else 0.0               # tampa solta apoiada em cima do corpo
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
    front_h = H - t - c - slide_lip(t) if p.lid == LID_SLIDE else H
    return Dims(W, D, H, Wi, Di, Hi, H + on_top, W, D, front_h)


def _hinged_dims(p: BoxParams) -> Dims:
    """Baú / porta dupla: corpo até Hb, tampa (lid_height) por cima, mesma largura e profundidade.
    O espaço útil vai até a parede do lado da dobradiça (Hbw), que fica mais baixa."""
    t = float(p.thickness)
    hg = hinge_geom(t, p.kerf)
    hl = float(p.lid_height)
    if p.inner:
        Wi, Di, Hi = float(p.width), float(p.depth), float(p.height)
        W, D = Wi + 2 * t, Di + 2 * t
        Hbw = Hi + t
        Hb = Hbw + hg["rho"] + hg["g"]
    else:
        W, D = float(p.width), float(p.depth)
        Hb, Hbw = hinge_heights(p, float(p.height))
        Wi, Di, Hi = W - 2 * t, D - 2 * t, Hbw - t
    out = hg["R"] - t / 2                               # quanto o nó passa da face do corpo
    tw, td = (W, D + out) if p.lid == LID_CHEST else (W + 2 * out, D)
    return Dims(W, D, Hb, Wi, Di, Hi, Hb + hl, tw, td, Hb, Hbw)


def validate(p: BoxParams) -> list[str]:
    """Erros que impedem gerar a caixa (mensagens para o operador)."""
    err = []
    t = p.thickness
    if p.model not in MODELS:
        err.append("Modelo desconhecido.")
        return err
    if p.model == MODEL_KERF:
        if t <= 0 or t > 12:
            err.append("Use uma espessura entre 0,5 e 12 mm no teste de kerf.")
        if p.quantity < 1:
            err.append("A quantidade precisa ser pelo menos 1.")
        return err
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
    if p.model == MODEL_BOX and p.lid in HINGED:
        hg = hinge_geom(t, p.kerf)
        need = hg["R"] + hg["g"] + 2 * t + 0.5          # nó + folga + espaço para os dentes com o tampo
        if p.lid_height < need:
            err.append(f"Tampa baixa demais para a dobradiça: use pelo menos {_ceil(need)} mm de altura de tampa.")
            return err
        if d.H - hg["R"] - 1 < 3 * t + 2:
            err.append("Caixa baixa demais para a dobradiça: aumente a altura ou diminua a altura da tampa.")
            return err
        if p.lid == LID_DOORS and d.W / 2 - 0.5 < hg["R"] + 3 * t:
            err.append("Caixa estreita demais para porta dupla: aumente a largura.")
            return err
    if min(d.Wi, d.Di) < 3 * t or d.Hi < 2 * t:
        err.append("A caixa ficou pequena demais para esta espessura: aumente as medidas.")
    else:
        cw = (d.Wi - (p.cols - 1) * t) / p.cols
        cd = (d.Di - (p.rows - 1) * t) / p.rows
        if min(cw, cd) < 2 * t:
            err.append("Compartimentos estreitos demais: diminua o número de divisórias.")
        err += _validate_model(p, d)
        if p.model == MODEL_BOX and p.lid in (LID_LIFT, LID_SLIDE) and p.finger_hole > 0 \
                and p.finger_hole > min(d.Wi, d.Di) - 4 * t:
            err.append("O furo para o dedo não cabe na tampa: diminua o diâmetro.")
    if p.quantity < 1:
        err.append("A quantidade precisa ser pelo menos 1.")
    return err


def _validate_model(p: BoxParams, d: Dims) -> list[str]:
    """Conferências que só valem para um modelo (as medidas básicas já foram conferidas)."""
    t, err = p.thickness, []
    if p.handles and p.model in (MODEL_BOX, MODEL_TRAY) and handle_size(p, d)[1] < 8:
        err.append("Caixa baixa demais para as alças: aumente a altura ou desligue as alças.")
    if p.model == MODEL_ELEC:
        depth = p.screw_len - t + 1
        if p.screw_len < t + 6:
            err.append("Parafuso curto demais: use pelo menos " + _ceil(t + 6) + " mm.")
        elif d.H - depth < 2 * t + 3:
            err.append(f"Caixa baixa demais para parafuso de {p.screw_len:g} mm: use um parafuso menor "
                       "ou aumente a altura.")
        if p.board:
            bw, bh, _, _ = board_size(p)
            if not ((bw <= d.Wi - 2 and bh <= d.Di - 2) or (bh <= d.Wi - 2 and bw <= d.Di - 2)):
                dims = f"{bw:g} × {bh:g}".replace(".", ",")
                err.append(f"A placa ({BOARDS[p.board][0]}, {dims} mm) não cabe dentro da caixa.")
        if p.cable_hole > 0 and p.cable_hole / 2 + t + 3 + p.cable_hole / 2 > d.H - depth - 2:
            err.append("O furo do cabo não cabe abaixo dos parafusos da tampa: diminua o furo ou aumente a altura.")
        if p.cable_hole > d.Wi - 10:
            err.append("O furo do cabo é maior que o fundo da caixa.")
    if p.model == MODEL_DRAWER and p.pull not in PULLS:
        err.append("Tipo de puxador desconhecido.")
    return err


def handle_size(p: BoxParams, d: Dims) -> tuple:
    """(comprimento, altura) da alça vazada nas laterais."""
    hh = min(16.0, (d.H - 2 * p.thickness) * 0.3)
    return min(0.45 * d.D, 80.0), hh


def _stadium(cu: float, cv: float, length: float, height: float, vertical: bool = False) -> Polygon:
    """Rasgo com pontas redondas (alça, puxador, ventilação)."""
    from shapely.geometry import LineString
    r = height / 2
    half = max(0.0, length / 2 - r)
    line = LineString([(cu, cv - half), (cu, cv + half)] if vertical else [(cu - half, cv), (cu + half, cv)])
    return line.buffer(r, quad_segs=12) if half > 0 else line.centroid.buffer(r, quad_segs=12)


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
    if p.model == MODEL_DRAWER:
        return _gen_drawer(p)
    if p.model == MODEL_ELEC:
        return _gen_elec(p)
    if p.model == MODEL_KERF:
        return _gen_kerf(p)
    if p.model == MODEL_TRAY:
        res = _gen_box(replace(p, lid=LID_OPEN))
        res.params = p
        return res
    return _gen_box(p)


def _faces(panels: list, t: float, f: float):
    """Fábrica de faces com encaixe: face(nome, w, h, vizinhos, origem, U, V, N, explosão, tipo, título)."""
    def face(name, w, h, edges, origin, U, V, N, ex, kind=None, title=None, engrave=""):
        pn = Panel(title or FACE_NAMES[name], kind or name, _panel_outline(w, h, t, f, name, edges), [],
                   origin, U, V, N, ex, engrave=engrave)
        panels.append(pn)
        return pn
    return face


def _add_dividers(panels: list, base: Panel, p: BoxParams, t: float, f: float, Wi: float, Di: float,
                  hd: float, ox: float, oy: float, z: float, motion=None, prefix: str = "") -> tuple:
    """Divisórias em grade dentro do espaço Wi × Di que começa em (ox, oy), com o fundo em z.

    Meia-madeira nos cruzamentos e um dente por vão entrando em furos na ``base``.
    Devolve (largura, profundidade) de cada compartimento."""
    cw = (Wi - (p.cols - 1) * t) / p.cols
    cd = (Di - (p.rows - 1) * t) / p.rows
    xs = [ox + i * cw + (i - 1) * t for i in range(1, p.cols)]   # x inicial de cada divisória "A"
    ys = [oy + j * cd + (j - 1) * t for j in range(1, p.rows)]
    cl = max(0.0, p.divider_clearance) + 2 * max(0.0, p.kerf)
    bx, by = base.origin[0], base.origin[1]                      # furos na base: coordenadas da base
    base_holes = []
    X, Y, Z = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)

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

    Ly = Di - cl                                      # comprimento das divisórias "A" (ao longo de y)
    y0 = oy + cl / 2
    cross_y = [y - y0 for y in ys]                    # início de cada rasgo, em u
    for i, x in enumerate(xs):
        g = rect(0, 0, Ly, hd)
        slots = [rect(u, hd / 2, u + t, hd + 1) for u in cross_y]
        stops = [u + t / 2 for u in cross_y]
        tabs = [rect(a, -t, b, 0) for a, b in tabs_between(stops, Ly)]
        g = unary_union([g] + tabs).difference(unary_union(slots)) if slots else unary_union([g] + tabs)
        for a, b in tabs_between(stops, Ly):
            base_holes.append(rect(x - bx, y0 + a - by, x + t - bx, y0 + b - by))
        panels.append(Panel(f"{prefix}Divisória A{i + 1}", "divisoria", _largest(g).simplify(0), [],
                            (x, y0, z), Y, Z, X, (0, 0, 1.6), stops, motion))
    Lx = Wi - cl
    x0 = ox + cl / 2
    cross_x = [x - x0 for x in xs]
    for j, y in enumerate(ys):
        g = rect(0, 0, Lx, hd)
        slots = [rect(u, -t - 1, u + t, hd / 2) for u in cross_x]
        stops = [u + t / 2 for u in cross_x]
        tabs = [rect(a, -t, b, 0) for a, b in tabs_between(stops, Lx)]
        g = unary_union([g] + tabs)
        if slots:
            g = g.difference(unary_union(slots))
        for a, b in tabs_between(stops, Lx):
            base_holes.append(rect(x0 + a - bx, y - by, x0 + b - bx, y + t - by))
        panels.append(Panel(f"{prefix}Divisória B{j + 1}", "divisoria", _largest(g).simplify(0), [],
                            (x0, y, z), X, Z, Y, (0, 0, 1.6), stops, motion))
    if base_holes:
        base.poly = _largest(base.poly.difference(unary_union(base_holes))).simplify(0)
    return cw, cd


def _gen_box(p: BoxParams) -> BoxResult:
    t = float(p.thickness)
    f = float(p.finger) if p.joint == JOINT_FINGER else math.inf
    d = resolve_dims(p)
    W, D, H = d.W, d.D, d.H
    c = max(0.0, float(p.lid_clearance))
    has_top = p.lid == LID_CLOSED
    top = "tampa" if has_top else None
    panels: list[Panel] = []
    face = _faces(panels, t, f)
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

    # ---------------- tampas com dobradiça integrada de MDF (baú e porta dupla)
    if p.lid in HINGED:
        warnings += _hinged_lid(p, panels, d, t, f, front, back, left, right, front_spec)

    # ---------------- divisórias
    cw, cd = _add_dividers(panels, base, p, t, f, d.Wi, d.Di, d.Hi, t, t, t)

    # ---------------- bandeja: rampa encostada na frente de cada compartimento (45°, colar)
    if p.model == MODEL_TRAY and p.ramp:
        s2 = math.sqrt(0.5)
        rh = min(cd * 0.55, d.Hi * 0.8)
        for j in range(p.rows):
            yf = t + j * (cd + t)                       # face da frente do compartimento
            for i in range(p.cols):
                xi = t + i * (cw + t)
                panels.append(Panel("Rampa", "rampa", rect(0, 0, cw - 1.0, rh * math.sqrt(2)), [],
                                    (xi + 0.5, yf + rh, t), X, (0.0, -s2, s2), (0.0, s2, s2), (0, -0.4, 1.2)))
        warnings.append("Rampas: cole cada uma apoiada no fundo e na parede da frente do compartimento.")

    # ---------------- alças vazadas nas laterais
    if p.handles:
        if p.lid in (LID_OPEN, LID_CLOSED, LID_LIFT):
            L, hh = handle_size(p, d)
            top_off = t if p.lid in (LID_CLOSED, LID_LIFT) else 0.0
            vc = H - top_off - 5 - hh / 2
            for side in (left, right):
                side.poly = _largest(side.poly.difference(_stadium(D / 2, vc, L, hh))).simplify(0)
        else:
            warnings.append("Alças não combinam com esta tampa (a lateral tem dobradiça ou rasgo): ficaram de fora.")

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


def _hinged_lid(p: BoxParams, panels: list, d: Dims, t: float, f: float, front: Panel, back: Panel,
                left: Panel, right: Panel, front_spec) -> list:
    """Dobradiça integrada de MDF (como a do boxes.py, foto do usuário): tudo no plano das paredes.

    Corpo: a parede do lado da dobradiça fica mais baixa (Hbw); as duas paredes perpendiculares ao eixo
    ganham um NÓ redondo (raio R, furo raio p) no canto, centrado na linha da tampa (Hb), no meio da
    espessura da parede da dobradiça. Tampa: caixa rasa (tampo + paredes) da mesma largura do corpo,
    cujas paredes ficam no mesmo plano das do corpo; a parede do lado da dobradiça desce até o eixo e
    tem uma LINGUETA em cada ponta que entra no furo retangular de um DISCO solto, que gira no nó.
    Ao abrir, tudo o que está à frente do eixo sobe; o que está atrás fica dentro do raio do nó ou
    passa por trás da caixa — por isso não bate (um teste gira a tampa de 0 a 100°)."""
    from shapely.geometry import Point
    W, D, Hb = d.W, d.D, d.H
    Htop, Hbw = d.total_h, d.hinge_wall_h
    hg = hinge_geom(t, p.kerf)
    pr, R, g, ph, gp = hg["p"], hg["R"], hg["g"], hg["ph"], hg["gp"]
    zc = Hb                                              # eixo na linha da tampa
    Hj = Hb - R - 1.0                                    # acima disto a aresta parede/nó fica lisa (o nó
                                                         # e o disco ocupam o canto; dentes ali se sobreporiam)
    zU = Hb + R + g                                      # acima disto a parede da dobradiça da tampa é larga
    Hl = Htop - (Hb + g)                                 # altura das paredes da tampa
    X, Y, Z = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)

    def outline(w, h, name, edges):
        return _panel_outline(w, h, t, f, name, edges)

    def knuckled(poly, centers):
        """Parede do corpo com o nó (raio R) e o furo (raio p) em cada centro."""
        nodes = [Point(cu, zc).buffer(R, quad_segs=32) for cu in centers]
        holes = [Point(cu, zc).buffer(pr, quad_segs=32) for cu in centers]
        return _largest(unary_union([poly] + nodes).difference(unary_union(holes))).simplify(0.005)

    if p.lid == LID_CHEST:
        # corpo: fundo mais baixo; laterais com nó no canto de trás
        back.poly = _largest(outline(W, Hbw, "fundo", {"b": "base", "l": ("esquerda", 0.0, Hj),
                                                       "r": ("direita", 0.0, Hj)}).difference(
            unary_union([rect(-1, Hj, t, Hbw + 1), rect(W - t, Hj, W + 1, Hbw + 1)]))).simplify(0)
        for side, nm in ((left, "esquerda"), (right, "direita")):
            side.poly = knuckled(outline(D, Hb, nm, {"b": "base", "l": front_spec, "r": ("fundo", 0.0, Hj)}),
                                 [D - t / 2])
        # tampa: eixo X na parede de trás; a = de trás para a frente, b = ao longo de x
        flaps = [("bau", (0.0, D, 0.0), (0.0, -1.0, 0.0), X, D, W, True, "Tampa", (0.0, D - t / 2, zc), X, -100.0)]
    else:
        # corpo: laterais mais baixas; frente e fundo com nó nos dois cantos de cima
        for side, nm in ((left, "esquerda"), (right, "direita")):
            side.poly = _largest(outline(D, Hbw, nm, {"b": "base", "l": ("frente", 0.0, Hj),
                                                      "r": ("fundo", 0.0, Hj)}).difference(
                unary_union([rect(-1, Hj, t, Hbw + 1), rect(D - t, Hj, D + 1, Hbw + 1)]))).simplify(0)
        for wall, nm in ((front, "frente"), (back, "fundo")):
            wall.poly = knuckled(outline(W, Hb, nm, {"b": "base", "l": ("esquerda", 0.0, Hj),
                                                     "r": ("direita", 0.0, Hj)}), [t / 2, W - t / 2])
        La = W / 2 - g / 2
        flaps = [("porta", (0.0, 0.0, 0.0), X, Y, La, D, False, "Porta esquerda", (t / 2, 0.0, zc), Y, -100.0),
                 ("porta", (W, 0.0, 0.0), (-1.0, 0.0, 0.0), Y, La, D, False, "Porta direita",
                  (W - t / 2, 0.0, zc), Y, 100.0)]

    def at(O, A, B, a, b, z=0.0):
        return tuple(O[i] + a * A[i] + b * B[i] + z * Z[i] for i in range(3))

    for pre, O, A, B, La, Lb, free_wall, title, pivot, axis, ang in flaps:
        topo, dob, lado, livre = f"{pre}_topo", f"{pre}_dobradica", f"{pre}_lado", f"{pre}_frente"
        mot = ("gira", pivot, axis, ang)
        sgn = 1.0 if ang > 0 else -1.0
        ex_up = (0.0, 0.0, 1.8)
        parts = []
        # tampo
        parts.append(Panel(title, topo, outline(La, Lb, topo, {"l": dob, "r": livre if free_wall else None,
                                                               "b": lado, "t": lado}),
                           [], at(O, A, B, 0, 0, Htop - t), A, B, Z, ex_up))
        # parede do lado da dobradiça: parte larga em cima, parte estreita até o eixo, lingueta nas pontas
        up = outline(Lb, Htop - zU, dob, {"t": topo, "l": lado, "r": lado})
        from shapely import affinity
        up = affinity.translate(up, 0, zU)
        low = rect(t, zc - ph / 2, Lb - t, zU + 0.01)
        tabs = [rect(0, zc - ph / 2, t, zc + ph / 2), rect(Lb - t, zc - ph / 2, Lb, zc + ph / 2)]
        hw = _largest(unary_union([up, low] + tabs)).simplify(0.005)
        parts.append(Panel(f"{title}: parede da dobradiça", dob, hw, [], at(O, A, B, 0, 0), B, Z, A, ex_up))
        # paredes nas pontas (no plano das paredes do corpo), recortadas em volta do nó
        for b0, nm in ((0.0, "1"), (Lb - t, "2")):
            ew = outline(La, Hl, lado, {"t": topo, "l": (dob, zU - (Hb + g), Hl), "r": livre if free_wall else None})
            cut = unary_union([Point(t / 2, zc - (Hb + g)).buffer(R + g, quad_segs=32),
                               rect(-1, -1, t, zU - (Hb + g))])
            ew = _largest(ew.difference(cut)).simplify(0.005)
            parts.append(Panel(f"{title}: lateral {nm}", lado, ew, [], at(O, A, B, 0, b0, Hb + g), A, Z, B, ex_up))
            # disco solto: gira no nó do corpo; a lingueta entra no furo retangular
            disc = Point(t / 2, zc).buffer(pr - gp, quad_segs=32).difference(
                rect(0, zc - ph / 2, t, zc + ph / 2))
            parts.append(Panel(f"{title}: disco da dobradiça {nm}", "disco", _largest(disc).simplify(0.005), [],
                               at(O, A, B, 0, b0), A, Z, B, (0.0, 0.0, 0.9)))
        if free_wall:
            parts.append(Panel(f"{title}: frente", livre, outline(Lb, Hl, livre, {"t": topo, "l": lado, "r": lado}),
                               [], at(O, A, B, La - t, 0, Hb + g), B, Z, A, ex_up))
        for pn in parts:
            pn.motion = mot
            if pn.kind != "disco":
                pn.explode = (sgn * 0.6 if pre == "porta" else 0.0, 0.0, 1.8)
        panels += parts
    return ["Dobradiça de MDF: encaixe os discos nas linguetas da tampa e depois coloque as laterais do corpo "
            "por fora, com os discos dentro dos nós. Não precisa de parafuso."]


def _gen_drawer(p: BoxParams) -> BoxResult:
    """Móvel aberto na frente + gaveta (caixa aberta em cima) + frente falsa com puxador."""
    t = float(p.thickness)
    f = float(p.finger) if p.joint == JOINT_FINGER else math.inf
    d = resolve_dims(p)
    W, D, H = d.W, d.D, d.H
    c = max(0.0, float(p.lid_clearance))
    panels: list[Panel] = []
    face = _faces(panels, t, f)
    X, Y, Z = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)
    # ---- móvel: base, tampa, fundo e laterais (frente aberta)
    shell_edges = {"t": "fundo", "l": "esquerda", "r": "direita"}
    face("base", W, D, shell_edges, (0, 0, 0), X, Y, Z, (0, 0, -1), title="Móvel: base", engrave="Móvel: base/tampa")
    face("tampa", W, D, shell_edges, (0, 0, H - t), X, Y, Z, (0, 0, 1.3), title="Móvel: tampa",
         engrave="Móvel: base/tampa")
    face("fundo", W, H, {"b": "base", "t": "tampa", "l": "esquerda", "r": "direita"}, (0, D - t, 0), X, Z, Y,
         (0, 1, 0), title="Móvel: fundo")
    for x, nm, ex in ((0.0, "esquerda", -1), (W - t, "direita", 1)):
        face(nm, D, H, {"b": "base", "t": "tampa", "r": "fundo"}, (x, 0, 0), Y, Z, X, (ex, 0, 0),
             title=f"Móvel: lateral {nm}", engrave="Móvel: lateral")
    # ---- gaveta: caixa aberta em cima, encostada na frente do móvel
    gx = t + c
    Wd, Dd, Hd = d.Wi + 2 * t, d.Di + 2 * t, d.Hi + t
    mot = ("move", (0.0, -0.7 * Dd, 0.0))
    drawer = []
    g_face = _faces(drawer, t, f)
    gbase = g_face("base", Wd, Dd, {"b": "frente", "t": "fundo", "l": "esquerda", "r": "direita"},
                   (gx, 0, t), X, Y, Z, (0, -1.6, 0.4), title="Gaveta: fundo (piso)", engrave="Gaveta: piso")
    gfront = g_face("frente", Wd, Hd, {"b": "base", "l": "esquerda", "r": "direita"}, (gx, 0, t), X, Z, Y,
                    (0, -1.9, 0.4), title="Gaveta: frente", engrave="Gaveta: frente")
    g_face("fundo", Wd, Hd, {"b": "base", "l": "esquerda", "r": "direita"}, (gx, Dd - t, t), X, Z, Y,
           (0, -1.2, 0.4), title="Gaveta: traseira", engrave="Gaveta: traseira")
    for x, nm in ((gx, "esquerda"), (gx + Wd - t, "direita")):
        g_face(nm, Dd, Hd, {"b": "base", "l": "frente", "r": "fundo"}, (x, 0, t), Y, Z, X, (0, -1.6, 0.4),
               title=f"Gaveta: lateral {nm}", engrave="Gaveta: lateral")
    # ---- frente falsa (cobre a boca do móvel; cole na frente da gaveta)
    ff = Panel("Gaveta: frente de acabamento (colar)", "frente_falsa", rect(0, 0, W - 1.0, H - 1.0), [],
               (0.5, -t, 0.5), X, Z, Y, (0, -2.4, 0.4), engrave="Gaveta: acabamento")
    drawer.append(ff)
    # ---- puxador: vazado (atravessa a frente falsa e a da gaveta) ou furo para um puxador com parafuso
    zc = t + Hd - 6 - min(16.0, Hd * 0.35) / 2
    if p.pull == "vazado":
        L, hh = min(60.0, Wd * 0.4), min(16.0, Hd * 0.35)
        ff.poly = _largest(ff.poly.difference(_stadium(W / 2 - 0.5, zc - 0.5, L, hh))).simplify(0)
        gfront.poly = _largest(gfront.poly.difference(_stadium(W / 2 - gx, zc - t, L, hh))).simplify(0)
    elif p.pull == "furo":
        ff.circles.append((W / 2 - 0.5, zc - 0.5, 2.0))
        gfront.circles.append((W / 2 - gx, zc - t, 2.0))
    for pn in drawer:
        pn.motion = mot
    panels += drawer
    _add_dividers(panels, gbase, p, t, f, d.Wi, d.Di, d.Hi, gx + t, t, 2 * t, motion=mot, prefix="Gaveta: ")
    warnings = ["Cole a frente de acabamento na frente da gaveta, centralizada na boca do móvel."]
    return BoxResult(p, d, panels, warnings)


def _gen_elec(p: BoxParams) -> BoxResult:
    """Caixa de eletrônica: paredes com rasgo em T (parafuso + porca M3) e tampa lisa parafusada por cima."""
    t = float(p.thickness)
    f = float(p.finger) if p.joint == JOINT_FINGER else math.inf
    d = resolve_dims(p)
    W, D, Hw = d.W, d.D, d.H
    panels: list[Panel] = []
    face = _faces(panels, t, f)
    X, Y, Z = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)
    base = face("base", W, D, {"b": "frente", "t": "fundo", "l": "esquerda", "r": "direita"},
                (0, 0, 0), X, Y, Z, (0, 0, -1))
    walls = {
        "frente": face("frente", W, Hw, {"b": "base", "l": "esquerda", "r": "direita"}, (0, 0, 0), X, Z, Y, (0, -1, 0)),
        "fundo": face("fundo", W, Hw, {"b": "base", "l": "esquerda", "r": "direita"}, (0, D - t, 0), X, Z, Y,
                      (0, 1, 0)),
        "esquerda": face("esquerda", D, Hw, {"b": "base", "l": "frente", "r": "fundo"}, (0, 0, 0), Y, Z, X,
                         (-1, 0, 0)),
        "direita": face("direita", D, Hw, {"b": "base", "l": "frente", "r": "fundo"}, (W - t, 0, 0), Y, Z, X,
                        (1, 0, 0)),
    }
    # ---- tampa: placa lisa ELEC_LIP maior que as paredes, um parafuso no meio de cada parede (2 se longa)
    o = ELEC_LIP
    sr = 1.55                                          # rasgo de 3,1 mm para o parafuso M3
    nw, nh = 5.7, 2.6                                  # rasgo da porca M3 (5,5 × 2,4 mm)
    depth = p.screw_len - t + 1                        # o parafuso atravessa a tampa e entra na parede
    nut_v = Hw - depth + 2                             # porca a 2 mm da ponta do parafuso
    top_holes = []

    def screws(length):
        n = 1 if length < 140 else 2
        return [length * (k + 1) / (n + 1) for k in range(n)]

    for nm, wall in walls.items():
        along_x = nm in ("frente", "fundo")
        for us in screws(W if along_x else D):
            tslot = unary_union([rect(us - sr, Hw - depth, us + sr, Hw + 1),
                                 rect(us - nw / 2, nut_v, us + nw / 2, nut_v + nh)])
            wall.poly = _largest(wall.poly.difference(tslot)).simplify(0)
            if along_x:
                y = t / 2 if nm == "frente" else D - t / 2
                top_holes.append((us + o, y + o, 1.65))
            else:
                x = t / 2 if nm == "esquerda" else W - t / 2
                top_holes.append((x + o, us + o, 1.65))
    lid = Panel("Tampa (parafusada)", "tampa_parafusada", rect(0, 0, W + 2 * o, D + 2 * o), top_holes,
                (-o, -o, Hw), X, Y, Z, (0, 0, 2.0), motion=("move", (0.0, 0.0, max(20.0, Hw * 0.6))))
    panels.append(lid)
    warnings = [f"Tampa: {len(top_holes)} parafusos M3 × {p.screw_len:g} mm com porca (a porca entra no rasgo "
                "da parede)."]
    # ---- placa: furos na base (centralizada; gira 90° se só couber assim) e a placa fantasma na vista 3D
    if p.board:
        name = BOARDS[p.board][0]
        bw, bh, holes, dia = board_size(p)
        if not (bw <= d.Wi - 2 and bh <= d.Di - 2):
            holes = [(hy, bw - hx) for hx, hy in holes]
            bw, bh = bh, bw
        bx, by = W / 2 - bw / 2, D / 2 - bh / 2
        base.circles += [(bx + hx, by + hy, dia / 2) for hx, hy in holes]
        panels.append(Panel(f"Placa: {name}", "placa", rect(0, 0, bw, bh), [(hx, hy, dia / 2) for hx, hy in holes],
                            (bx, by, t + STANDOFF), X, Y, Z, (0, 0, 0.8), ghost=True))
        screw = "M2,5" if dia < 3 else "M3"
        warnings.append(f"{name}: fixe com espaçadores de {STANDOFF:g} mm e parafusos {screw}.")
    # ---- furo do cabo no fundo, perto de baixo (abaixo dos parafusos da tampa)
    if p.cable_hole > 0:
        rc = p.cable_hole / 2
        walls["fundo"].circles.append((W / 2, t + 3 + rc, rc))
    # ---- ventilação: rasgos verticais nas laterais, abaixo das porcas
    if p.vents:
        v0, v1 = t + 4, nut_v - 4
        if v1 - v0 >= 8:
            for nm in ("esquerda", "direita"):
                wall = walls[nm]
                slots = [_stadium(u, (v0 + v1) / 2, v1 - v0, 3.0, vertical=True)
                         for u in [t + 8 + 7 * k for k in range(int((D - 2 * t - 16) // 7) + 1)]]
                wall.poly = _largest(wall.poly.difference(unary_union(slots))).simplify(0)
        else:
            warnings.append("Caixa baixa demais para os rasgos de ventilação: ficaram de fora.")
    return BoxResult(p, d, panels, warnings)


def _gen_kerf(p: BoxParams) -> BoxResult:
    """Pente com rasgos de largura (espessura − k) para k em KERF_STEPS, e uma tira para testar."""
    t = float(p.thickness)
    d = resolve_dims(p)
    pitch, depth, cw, ch = kerf_comb_size(t)
    X, Z, Y = (1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, 1.0, 0.0)
    comb = rect(0, 0, cw, ch)
    texts = []
    for i, k in enumerate(KERF_STEPS):
        u = 8.0 + pitch * i + pitch / 2
        w = t - k
        comb = comb.difference(rect(u - w / 2, ch - depth, u + w / 2, ch + 1))
        texts.append((u, 5.0, f"{k:.2f}".replace(".", ","), 3.2))
    panels = [Panel("Pente do teste de kerf", "pente", _largest(comb).simplify(0), [], (0, 0, 0), X, Z, Y,
                    (0, 0, 0), texts=texts),
              Panel("Tira de teste", "tira", rect(0, 0, depth + 6, max(14.0, pitch)), [],
                    (cw + 10, 0, 0), X, Z, Y, (0.5, 0, 0))]
    warnings = ["Corte as duas peças sem compensação. Encaixe a tira em cada rasgo: o número embaixo do "
                "rasgo em que ela entrar justa (sem folga e sem forçar) é o seu kerf."]
    return BoxResult(replace(p, kerf=0.0), d, panels, warnings)


# ---------------------------------------------------------------------------------------------- saída
def flat_layout(result: BoxResult, gap: float = 6.0, max_width: Optional[float] = None) -> list[tuple]:
    """Peças planificadas lado a lado: [(painel, polígono de corte posicionado, círculos posicionados,
    deslocamento (dx, dy) aplicado — para posicionar os textos)]."""
    k = result.params.kerf
    items = []
    for pn in result.cut_panels:
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
        out[i] = (pn, affinity.translate(g, dx, dy), circles, (dx, dy))
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
    if result.params.engrave_names or any(pn.texts for pn in result.cut_panels):
        doc.layers.add(TEXT_LAYER, color=TEXT_ACI)
    msp = doc.modelspace()
    attrs = {"layer": CUT_LAYER, "color": CUT_ACI}
    for pn, g, circles, (dx, dy) in flat_layout(result):
        for u, v, text, hgt in pn.texts:                # textos próprios da peça (sempre gravados)
            txt = msp.add_text(text, height=hgt, dxfattribs={"layer": TEXT_LAYER, "color": TEXT_ACI})
            txt.set_placement((u + dx, v + dy), align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)
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
    if p.model == MODEL_KERF:
        return f"teste_kerf_{p.thickness:g}mm".replace(".", ",")
    kind = p.lid if p.model == MODEL_BOX else p.model
    return (f"caixa_{kind}_{d.total_w or d.W:g}x{d.total_d or d.D:g}x{d.total_h:g}_{p.thickness:g}mm"
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
                      for pn in result.cut_panels), key=lambda s: s[0] * s[1]),
    }
