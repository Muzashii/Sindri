"""Gravação de foto em linhas: a imagem vira linhas horizontais com potências diferentes (sem Qt).

Cada linha da imagem (a cada ``line_mm``) é lida da esquerda para a direita; trechos com o mesmo
tom viram UM segmento de reta. Os tons são agrupados em N níveis de potência, e cada nível vai para
uma cor própria do RDWorks (uma camada com a sua potência). O claro absoluto não é gravado.
A difusão de erro (Floyd–Steinberg entre níveis) dá a impressão de mais tons do que N.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

# cores do RDWorks que existem exatamente em ACI (o RDWorks separa camadas pela cor):
# preto, azul, vermelho, verde, amarelo — do nível mais escuro (mais potência) ao mais claro
LEVEL_ACI = [7, 5, 1, 3, 2]
LEVEL_NAMES = ["preto", "azul", "vermelho", "verde", "amarelo"]
MAX_LEVELS = len(LEVEL_ACI)


@dataclass
class PhotoParams:
    width_mm: float = 150.0
    line_mm: float = 0.25           # distância entre linhas (e resolução ao longo da linha)
    levels: int = 5                 # níveis de potência (1..5)
    power_min: float = 20.0         # potência do nível mais claro
    power_max: float = 70.0         # potência do nível mais escuro
    speed: float = 150.0            # mm/s (todas as camadas)
    brightness: float = 0.0         # -1..1
    contrast: float = 0.0           # -1..1
    gamma: float = 1.0
    invert: bool = False
    dither: bool = True
    white_cut: float = 0.06         # abaixo disto (escuridão) não grava
    frame_mm: float = 0.0           # moldura escura em volta (0 = sem)
    min_seg_mm: float = 0.0         # trechos menores são descartados (0 = mantém todos)
    x_mm: float = 10.0              # posição na placa (canto inferior esquerdo)
    y_mm: float = 10.0
    mode: str = "imagem"            # "imagem": BMP em tons de cinza (o RDWorks varia a potência sozinho)
                                    # "linhas": DXF com uma camada por nível de potência

    def level_powers(self) -> list[float]:
        """Potência de cada nível (0 = mais escuro)."""
        n = max(1, min(MAX_LEVELS, int(self.levels)))
        if n == 1:
            return [float(self.power_max)]
        return [round(self.power_max - (self.power_max - self.power_min) * k / (n - 1), 1) for k in range(n)]


@dataclass
class PhotoResult:
    levels: np.ndarray               # (linhas, colunas) int8: -1 = não grava, 0 = mais escuro
    width_mm: float
    height_mm: float
    line_mm: float
    segments: list = field(default_factory=list)   # [(nível, y, x0, x1)] em mm, origem no canto inf. esq.
    dark: Optional[np.ndarray] = None              # escuridão 0..1 já ajustada (modo imagem)

    @property
    def count(self) -> int:
        return len(self.segments)

    def length_mm(self) -> float:
        return float(sum(s[3] - s[2] for s in self.segments))


def adjust(gray: np.ndarray, p: PhotoParams) -> np.ndarray:
    """gray 0..1 (1 = branco) -> escuridão 0..1 (1 = queima máxima), com brilho/contraste/gama."""
    g = np.clip(gray.astype(np.float32), 0, 1)
    if p.invert:
        g = 1 - g
    g = g + float(p.brightness) * 0.5
    c = float(p.contrast)
    k = (1 + c) / (1 - c) if c < 1 else 50.0
    g = (g - 0.5) * k + 0.5
    g = np.clip(g, 0, 1)
    if p.gamma and abs(p.gamma - 1) > 1e-6:
        g = g ** (1.0 / float(p.gamma))
    return 1 - g


def resample(img: np.ndarray, rows: int, cols: int) -> np.ndarray:
    """Redimensiona por média de área (reduzir) ou interpolação (ampliar), sem bibliotecas extras."""
    h, w = img.shape

    def axis(src_n, dst_n):
        edges = np.linspace(0, src_n, dst_n + 1)
        return edges

    if rows <= h and cols <= w:
        # média por blocos usando soma acumulada (rápido e sem serrilhado)
        cs = np.cumsum(np.cumsum(np.pad(img, ((1, 0), (1, 0))), 0), 1)
        ye = np.clip(np.round(axis(h, rows)).astype(int), 0, h)
        xe = np.clip(np.round(axis(w, cols)).astype(int), 0, w)
        ye[1:] = np.maximum(ye[1:], ye[:-1] + 1)
        xe[1:] = np.maximum(xe[1:], xe[:-1] + 1)
        ye, xe = np.minimum(ye, h), np.minimum(xe, w)
        y0, y1 = ye[:-1][:, None], ye[1:][:, None]
        x0, x1 = xe[:-1][None, :], xe[1:][None, :]
        s = cs[y1, x1] - cs[y0, x1] - cs[y1, x0] + cs[y0, x0]
        area = np.maximum((y1 - y0) * (x1 - x0), 1)
        return s / area
    ys = (np.arange(rows) + 0.5) * h / rows - 0.5
    xs = (np.arange(cols) + 0.5) * w / cols - 0.5
    tmp = np.array([np.interp(xs, np.arange(w), row) for row in img])
    return np.array([np.interp(ys, np.arange(h), tmp[:, j]) for j in range(cols)]).T


def quantize(dark: np.ndarray, p: PhotoParams) -> np.ndarray:
    """Escuridão 0..1 -> nível (0 = mais escuro ... n-1) ou -1 (não grava)."""
    n = max(1, min(MAX_LEVELS, int(p.levels)))
    # valores possíveis: 0 (não grava) e n patamares de escuridão entre white_cut e 1
    lo = float(p.white_cut)
    steps = np.array([0.0] + [lo + (1 - lo) * (k + 1) / n for k in range(n)], dtype=np.float32)
    d = np.clip(dark.astype(np.float32), 0, 1).copy()
    rows, cols = d.shape
    out = np.zeros((rows, cols), dtype=np.int8)
    if p.dither:
        unit = (1 - lo) / n
        st = steps.tolist()
        grid = d.tolist()
        res = out.tolist()

        def nearest(v):
            k = int(round((v - lo) / unit))
            k = 1 if k < 1 else (n if k > n else k)
            return 0 if abs(v) < abs(v - st[k]) else k

        for y in range(rows):
            row = grid[y]
            nxt = grid[y + 1] if y + 1 < rows else None
            if y % 2 == 0:
                xs, step = range(cols), 1
            else:
                xs, step = range(cols - 1, -1, -1), -1
            r_out = res[y]
            for x in xs:
                v = row[x]
                k = nearest(v)
                r_out[x] = k
                err = v - st[k]
                if not err:
                    continue
                x1 = x + step
                inside = 0 <= x1 < cols
                if inside:
                    row[x1] += err * 0.4375
                if nxt is not None:
                    xb = x - step
                    if 0 <= xb < cols:
                        nxt[xb] += err * 0.1875
                    nxt[x] += err * 0.3125
                    if inside:
                        nxt[x1] += err * 0.0625
        out = np.array(res, dtype=np.int8)
    else:
        out = np.argmin(np.abs(d[..., None] - steps[None, None, :]), axis=2).astype(np.int8)
    # patamar k (1..n, do claro ao escuro) -> nível n-k (0 = mais escuro); 0 -> -1
    return np.where(out == 0, -1, n - out).astype(np.int8)


def dither_binary(dark: np.ndarray) -> np.ndarray:
    """Difusão de erro (Floyd–Steinberg, em serpentina) para 1 bit: 0 = queima, -1 = não queima."""
    d = np.clip(dark.astype(np.float64), 0, 1)
    rows, cols = d.shape
    out = np.full((rows, cols), -1, dtype=np.int8)
    carry = np.zeros(cols + 2)                       # erro que desce para a linha seguinte
    for y in range(rows):
        row = (d[y] + carry[1:-1]).tolist()
        nxt = [0.0] * (cols + 2)
        res = out[y]
        burn = []
        if y % 2 == 0:
            xs, step = range(cols), 1
        else:
            xs, step = range(cols - 1, -1, -1), -1
        err_right = 0.0
        for x in xs:
            v = row[x] + err_right
            if v >= 0.5:
                burn.append(x)
                e = v - 1.0
            else:
                e = v
            err_right = e * 0.4375
            nxt[x + 1 - step] += e * 0.1875
            nxt[x + 1] += e * 0.3125
            nxt[x + 1 + step] += e * 0.0625
        if burn:
            res[burn] = 0
        carry = np.asarray(nxt)
    return out


def segments(levels: np.ndarray, line_mm: float, min_seg_mm: float = 0.0) -> list:
    """Trechos contínuos do mesmo nível em cada linha -> [(nível, y, x0, x1)] (mm, y para cima)."""
    rows, cols = levels.shape
    out = []
    min_px = max(1, int(math.ceil(min_seg_mm / line_mm - 1e-9))) if min_seg_mm > 0 else 1
    for r in range(rows):
        row = levels[r]
        y = (rows - 1 - r + 0.5) * line_mm
        change = np.flatnonzero(np.diff(row)) + 1
        starts = np.concatenate(([0], change))
        ends = np.concatenate((change, [cols]))
        runs = [(int(row[s]), s, e) for s, e in zip(starts, ends) if row[s] >= 0 and e - s >= min_px]
        if r % 2:
            runs.reverse()                              # serpentina: menos deslocamento vazio
        for lv, s, e in runs:
            out.append((lv, y, s * line_mm, e * line_mm))
    return out


def trace(gray: np.ndarray, p: PhotoParams, max_cols: int = 2400) -> PhotoResult:
    """Imagem (0..1, 1 = branco) -> níveis e segmentos no tamanho pedido."""
    h, w = gray.shape
    width = float(p.width_mm)
    height = width * h / w
    line = max(0.05, float(p.line_mm))
    rows = max(1, int(round(height / line)))
    cols = max(1, min(max_cols, int(round(width / line))))
    px = width / cols
    dark = adjust(resample(gray.astype(np.float32), rows, cols), p)
    if p.frame_mm > 0:
        fr = max(1, int(round(p.frame_mm / line)))
        fc = max(1, int(round(p.frame_mm / px)))
        dark[:fr, :] = dark[-fr:, :] = 1.0
        dark[:, :fc] = dark[:, -fc:] = 1.0
    dark = np.where(dark < float(p.white_cut), 0.0, dark).astype(np.float32)
    if p.mode == "imagem":
        if p.dither:
            # pontilhado feito aqui: a imagem sai só com preto e branco e os tons vêm da densidade de
            # pontos — funciona com qualquer configuração do RDWorks (que costuma tratar o bitmap como
            # 1 bit, deixando a foto com 2 tons) e é o que dá melhor resultado em MDF/madeira
            lv = dither_binary(dark)
            dark = (lv == 0).astype(np.float32)
        else:
            lv = np.where(dark > 0, 0, -1).astype(np.int8)
        return PhotoResult(lv, width, rows * line, line, [], dark)
    lv = quantize(dark, p)
    segs = segments(lv, line, p.min_seg_mm)
    if abs(px - line) > 1e-9:                           # colunas com passo diferente das linhas
        segs = [(k, y, x0 / line * px, x1 / line * px) for k, y, x0, x1 in segs]
    return PhotoResult(lv, width, rows * line, line, segs, dark)


def write_bmp(result: PhotoResult, path: str) -> str:
    """BMP 8 bits em tons de cinza, com a resolução gravada (o RDWorks importa já no tamanho certo).
    Branco = não grava; quanto mais escuro, mais potência (entre a mínima e a máxima da camada)."""
    import os
    import struct
    dark = result.dark if result.dark is not None else np.where(result.levels >= 0, 1.0, 0.0)
    img = (255 - np.clip(dark, 0, 1) * 255).round().astype(np.uint8)
    h, w = img.shape
    row = (w + 3) & ~3
    data = np.full((h, row), 255, dtype=np.uint8)
    data[:, :w] = img[::-1]                                  # BMP guarda de baixo para cima
    ppm = int(round(w / result.width_mm * 1000.0))           # pixels por metro
    palette = b"".join(struct.pack("<BBBB", i, i, i, 0) for i in range(256))
    off = 14 + 40 + len(palette)
    size = off + data.size
    head = struct.pack("<2sIHHI", b"BM", size, 0, 0, off)
    info = struct.pack("<IiiHHIIiiII", 40, w, h, 1, 8, 0, data.size, ppm, ppm, 256, 0)
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(head + info + palette + data.tobytes())
    os.replace(tmp, path)
    return path


def write_dxf(result: PhotoResult, p: PhotoParams, path: str, outline: Optional[tuple] = None) -> str:
    """DXF com uma camada/cor por nível. ``outline``: (largura, altura) da placa para desenhar o contorno.

    Uma foto vira centenas de milhares de linhas: o cabeçalho e as camadas saem do ezdxf e as
    linhas são escritas direto no texto (o ezdxf levaria ~30 s para criar cada entidade)."""
    import io
    import ezdxf
    from .dxf_export import PLATE_LAYER
    doc = ezdxf.new("R12", setup=False)
    n = max(1, min(MAX_LEVELS, int(p.levels)))
    for k in range(n):
        doc.layers.add(f"FOTO_{k + 1}", color=LEVEL_ACI[k])
    msp = doc.modelspace()
    ox, oy = float(p.x_mm), float(p.y_mm)
    if outline:
        w, h = outline
        doc.layers.add(PLATE_LAYER, color=8)
        for a, b in (((0, 0), (w, 0)), ((w, 0), (w, h)), ((w, h), (0, h)), ((0, h), (0, 0))):
            msp.add_line(a, b, dxfattribs={"layer": PLATE_LAYER, "color": 8})
    xs = [ox + s[2] for s in result.segments] + [ox + s[3] for s in result.segments]
    ys = [oy + s[1] for s in result.segments]
    if xs:
        doc.header["$EXTMIN"] = (min(xs + [0.0]), min(ys + [0.0]), 0)
        doc.header["$EXTMAX"] = (max(xs + [outline[0] if outline else 0.0]),
                                 max(ys + [outline[1] if outline else 0.0]), 0)
    buf = io.StringIO()
    doc.write(buf)
    text = buf.getvalue()
    lines = []
    for k, y, x0, x1 in result.segments:
        yy = oy + y
        lines.append(f"  0\nLINE\n  8\nFOTO_{k + 1}\n 62\n{LEVEL_ACI[k]}\n 10\n{ox + x0:.3f}\n 20\n{yy:.3f}\n"
                     f" 30\n0.0\n 11\n{ox + x1:.3f}\n 21\n{yy:.3f}\n 31\n0.0\n")
    marker = "ENTITIES\n"
    i = text.index(marker) + len(marker)
    j = text.index("  0\nENDSEC", i)
    text = text[:j] + "".join(lines) + text[j:]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="cp1252", newline="\r\n") as fh:
        fh.write(text)
    import os
    os.replace(tmp, path)
    return path


def estimate_minutes(result: PhotoResult, p: PhotoParams) -> float:
    """Tempo aproximado: comprimento gravado / velocidade, mais o vaivém de cada linha."""
    if not result.segments or p.speed <= 0:
        return 0.0
    rows = result.levels.shape[0]
    travel = rows * result.width_mm * 0.35                # deslocamentos rápidos (aprox.)
    return (result.length_mm() / p.speed + travel / max(p.speed * 2, 1)) / 60.0
