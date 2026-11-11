"""Grade de teste de velocidade × potência para descobrir os parâmetros de um material.

Cada quadrado da grade é uma combinação (velocidade, potência). O RDWorks separa os parâmetros pela COR
da camada, e a paleta útil tem 16 camadas — uma fica para os números gravados, então cabem até 15
combinações por arquivo (14 com a paleta padrão). Grades maiores viram vários arquivos (linhas inteiras de velocidade em cada um).

* **corte**: contorno de cada quadrado; o técnico vê qual soltou limpo.
* **gravação**: o quadrado é preenchido por linhas paralelas (gravação vetorial) com o contorno.

Os números (velocidade à esquerda de cada linha, potência acima de cada coluna) são gravados com a fonte de
traços do Sindri, numa camada própria com valores de gravação leve. Sem Qt.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from .laser import COLOR_CHOICES, DEFAULT_PALETTE, aci_rgb, extra_layer_colors, nearest_layer, rd_triple

LABEL_ACI = 5                    # azul: a cor de gravação de costume
LABEL_HEIGHT = 2.5               # mm
CHAR_ADVANCE = 0.85              # avanço da fonte de traços, em alturas (0,55 + 0,30)
MODES = ("corte", "gravacao")


@dataclass
class Cell:
    row: int
    col: int
    speed: float                 # mm/s
    power_min: float             # %
    power_max: float             # %
    aci: int                     # cor no DXF
    rd_layer: int                # camada do RDWorks que essa cor alcança
    x: float = 0.0               # canto inferior esquerdo (mm)
    y: float = 0.0


@dataclass
class GridSheet:
    """Um arquivo da grade."""
    cells: list[Cell]
    speeds: list[float]
    powers: list[float]
    width: float
    height: float
    label_aci: int = LABEL_ACI
    label_layer: int = 1
    number: int = 1              # arquivo 1 de N
    total: int = 1


@dataclass
class GridPlan:
    material: str
    mode: str
    size: float
    gap: float
    interval: float
    sheets: list[GridSheet] = field(default_factory=list)

    @property
    def cells(self) -> list[Cell]:
        return [c for s in self.sheets for c in s.cells]


def steps(lo: float, hi: float, n: int, digits: int = 1) -> list[float]:
    """n valores igualmente espaçados de lo a hi (inclusive), sem repetição."""
    lo, hi, n = float(lo), float(hi), max(1, int(n))
    if n == 1 or abs(hi - lo) < 1e-9:
        return [round(lo, digits)]
    if hi < lo:
        lo, hi = hi, lo
    out: list[float] = []
    for i in range(n):
        v = round(lo + (hi - lo) * i / (n - 1), digits)
        if not out or v != out[-1]:
            out.append(v)
    return out


def cell_colors(palette: Optional[list] = None) -> list[tuple[int, int]]:
    """[(ACI, camada do RDWorks)] das cores que caem cada uma numa camada diferente, sem a cor dos números."""
    pal = palette or DEFAULT_PALETTE
    label_layer = nearest_layer(aci_rgb(LABEL_ACI), pal)
    out, used = [], {label_layer}
    for aci in [a for _, a in COLOR_CHOICES] + extra_layer_colors(pal):
        layer = nearest_layer(aci_rgb(aci), pal)
        if layer not in used:
            used.add(layer)
            out.append((aci, layer))
    return out


def _text_width(text: str, h: float = LABEL_HEIGHT) -> float:
    return len(text) * h * CHAR_ADVANCE


def speed_label(v: float) -> str:
    return f"V{v:g}"


def power_label(p: float) -> str:
    return f"P{p:g}"


def plan_grid(material: str, speeds: list[float], powers: list[float], mode: str = "corte",
              size: float = 10.0, gap: float = 3.0, interval: float = 0.2,
              palette: Optional[list] = None) -> GridPlan:
    """Distribui as combinações em arquivos de no máximo uma cor por combinação."""
    if mode not in MODES:
        raise ValueError(f"modo desconhecido: {mode}")
    speeds = [float(v) for v in speeds if float(v) > 0]
    powers = [float(p) for p in powers if 0 < float(p) <= 100]
    if not speeds or not powers:
        raise ValueError("informe ao menos uma velocidade e uma potência válidas")
    if size <= 0 or gap < 0:
        raise ValueError("tamanho do quadrado inválido")
    colors = cell_colors(palette)
    cap = len(colors)
    pal = palette or DEFAULT_PALETTE
    plan = GridPlan(material, mode, float(size), float(gap), float(interval))
    col_chunks = [powers[i:i + cap] for i in range(0, len(powers), cap)]
    for cols in col_chunks:
        per = max(1, cap // len(cols))
        for r0 in range(0, len(speeds), per):
            rows = speeds[r0:r0 + per]
            cells = []
            k = 0
            for i, v in enumerate(rows):
                for j, p in enumerate(cols):
                    aci, layer = colors[k]
                    k += 1
                    t = rd_triple(v, p, 0.0, "corte" if mode == "corte" else "scan")
                    cells.append(Cell(i, j, v, t[1], t[2], aci, layer))
            plan.sheets.append(_layout(cells, rows, cols, plan, pal))
    for n, s in enumerate(plan.sheets, 1):
        s.number, s.total = n, len(plan.sheets)
    return plan


def _layout(cells: list[Cell], rows: list[float], cols: list[float], plan: GridPlan, pal) -> GridSheet:
    step = plan.size + plan.gap
    label_w = max(_text_width(speed_label(v)) for v in rows) + 2.0
    nrows = len(rows)
    for c in cells:
        c.x = label_w + c.col * step
        c.y = (nrows - 1 - c.row) * step
    grid_w = label_w + len(cols) * step - plan.gap
    head = LABEL_HEIGHT + 2.0                      # potências
    title = LABEL_HEIGHT * 1.2 + 2.0                # material e modo
    width = max(grid_w, _text_width(_title(plan, 1, 1), LABEL_HEIGHT * 1.2))
    height = nrows * step - plan.gap + head + title
    return GridSheet(cells, list(rows), list(cols), round(width, 3), round(height, 3),
                     LABEL_ACI, nearest_layer(aci_rgb(LABEL_ACI), pal))


def _title(plan: GridPlan, n: int, total: int) -> str:
    mode = "CORTE" if plan.mode == "corte" else "GRAVACAO"
    tag = f" {n}/{total}" if total > 1 else ""
    return f"{plan.material} {mode} V MM/S P{tag}".upper()


def label_values(speed: float = 300.0, power: float = 12.0) -> tuple[float, float, float]:
    """(vel., pot. mín., pot. máx.) da camada dos números: gravação leve."""
    return rd_triple(speed, power, 0.0, "scan")


def sheet_values(sheet: GridSheet, labels: tuple[float, float] = (300.0, 12.0)) -> dict[int, tuple]:
    """{camada do RDWorks: (vel., pot. mín., pot. máx.)} para gravar no config ao abrir o arquivo."""
    out = {c.rd_layer: (c.speed, c.power_min, c.power_max) for c in sheet.cells}
    out[sheet.label_layer] = label_values(*labels)
    return out


def sheet_geometry(sheet: GridSheet, plan: GridPlan) -> dict:
    """Geometria pronta para desenhar ou gravar: {"squares": [(cell, [pts])], "hatch": [(cell, (p, q))],
    "labels": [[pts]…]} — tudo em mm, origem no canto inferior esquerdo."""
    from .dxf_export import stroke_lines
    squares, hatch, labels = [], [], []
    s = plan.size
    for c in sheet.cells:
        squares.append((c, [(c.x, c.y), (c.x + s, c.y), (c.x + s, c.y + s), (c.x, c.y + s)]))
        if plan.mode == "gravacao":
            n = max(1, int(s / max(plan.interval, 0.05)))
            for k in range(1, n):
                y = c.y + s * k / n
                hatch.append((c, ((c.x, y), (c.x + s, y))))
    step = plan.size + plan.gap
    nrows = len(sheet.speeds)
    for i, v in enumerate(sheet.speeds):
        y = (nrows - 1 - i) * step + (s - LABEL_HEIGHT) / 2
        labels += stroke_lines(speed_label(v), 0.0, y, LABEL_HEIGHT)
    top = nrows * step - plan.gap
    x0 = sheet.cells[0].x if sheet.cells else 0.0
    for j, p in enumerate(sheet.powers):
        text = power_label(p)
        x = x0 + j * step + (s - _text_width(text)) / 2
        labels += stroke_lines(text, max(0.0, x), top + 1.5, LABEL_HEIGHT)
    labels += stroke_lines(_title(plan, sheet.number, sheet.total), 0.0, top + LABEL_HEIGHT + 3.5,
                           LABEL_HEIGHT * 1.2)
    return {"squares": squares, "hatch": hatch, "labels": labels}


def write_grid_dxf(sheet: GridSheet, plan: GridPlan, path: str, version: str = "R2000") -> str:
    """Grava o arquivo da grade (mm). Números primeiro, depois a gravação e os cortes."""
    import ezdxf
    doc = ezdxf.new(version)
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    g = sheet_geometry(sheet, plan)
    for pts in g["labels"]:
        if len(pts) >= 2:
            msp.add_lwpolyline(pts, dxfattribs={"layer": "NUMEROS", "color": sheet.label_aci})
    for c, (p, q) in g["hatch"]:
        msp.add_line(p, q, dxfattribs={"layer": f"GRADE_{c.rd_layer}", "color": c.aci})
    for c, pts in g["squares"]:
        msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": f"GRADE_{c.rd_layer}", "color": c.aci})
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    tmp = path + ".tmp"
    doc.saveas(tmp)
    os.replace(tmp, path)
    return path


def write_plan(plan: GridPlan, folder: str, base: str = "") -> list[str]:
    """Um DXF por arquivo da grade. Devolve os caminhos."""
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in (base or plan.material or "grade"))
    out = []
    for s in plan.sheets:
        suffix = f"_{s.number}de{s.total}" if s.total > 1 else ""
        out.append(write_grid_dxf(s, plan, os.path.join(folder, f"grade_{safe}_{plan.mode}{suffix}.dxf")))
    return out


def record_result(db, material: str, cell: Cell, mode: str, who: str = "", today: Optional[str] = None):
    """Grava no banco de materiais a combinação escolhida (corte → camada "corte"; gravação →
    "gravacao_vetorial"), com a data do teste e quem validou. Devolve o material."""
    import datetime as _dt

    from .material_db import Material
    m = db.find(material) or db.add(Material(name=material))
    op = "corte" if mode == "corte" else "gravacao_vetorial"
    m.set_laser(op, {"speed": float(cell.speed), "power": float(cell.power_max),
                     "power_min": float(cell.power_min)})
    m.tested = today or _dt.date.today().isoformat()
    if who:
        m.validated_by = who
    return m
