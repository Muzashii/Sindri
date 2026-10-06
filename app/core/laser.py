"""Velocidade e potência do laser por camada, aplicadas no RDWorks antes de abrir o arquivo (sem Qt).

O RDWorks separa as camadas pela COR e lembra, para cada uma das suas 256 cores, a última
velocidade/potência usada. Essas lembranças ficam no arquivo ``config`` da pasta do RDWorks: uma
tabela com um registro por cor (cor RGB logo antes de 5 números ``double``: velocidade, potência
mín./máx. do tubo 1 e mín./máx. do tubo 2). O Sindri só troca esses 5 números das cores usadas no
arquivo exportado — nada mais do ``config`` é alterado — e guarda uma cópia antes.

Num lote com materiais diferentes no mesmo arquivo, a mesma cor não pode ter duas potências: as
peças do 2º material em diante passam para outra cor livre do RDWorks (cada material vira uma camada).
"""
from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .models import Part, Placement, Prim

CONFIG_NAME = "config"
BACKUP_NAME = "config.antes_sindri"
FIELDS = 5                      # velocidade, pot. mín 1, pot. máx 1, pot. mín 2, pot. máx 2
# paleta padrão do RDWorks V8 (usada quando o config não pode ser lido)
DEFAULT_PALETTE = [(0, 0, 0), (0, 0, 255), (255, 0, 0), (0, 255, 0), (250, 128, 114), (255, 255, 0),
                   (30, 144, 255), (138, 54, 15), (128, 128, 0), (0, 128, 92), (255, 69, 0), (64, 0, 128),
                   (254, 210, 219), (176, 158, 250), (213, 251, 157), (250, 144, 87)]


# ---------------------------------------------------------------------------------------- cores
def export_aci(p: Prim) -> int:
    """Cor ACI com que a primitiva é gravada no DXF (o que o RDWorks enxerga)."""
    if p.rgb:
        from .dxf_export import nearest_aci
        return nearest_aci(p.rgb)
    c = int(p.color)
    return c if 1 <= c <= 255 else 7


def aci_rgb(aci: int) -> tuple[int, int, int]:
    """RGB que o RDWorks usa para uma cor ACI (7 = preto)."""
    if aci == 7 or not 1 <= aci <= 255:
        return (0, 0, 0)
    from ezdxf.colors import aci2rgb
    c = aci2rgb(aci)
    return (int(c[0]), int(c[1]), int(c[2]))


def _exact_aci(rgb) -> Optional[int]:
    """ACI que tem exatamente esse RGB (para mandar peças para uma camada escolhida do RDWorks)."""
    if tuple(rgb) == (0, 0, 0):
        return 7
    for i in range(1, 256):
        if i != 7 and aci_rgb(i) == tuple(rgb):
            return i
    return None


def nearest_layer(rgb, palette: list) -> int:
    r, g, b = rgb
    return min(range(len(palette)),
               key=lambda i: (palette[i][0] - r) ** 2 + (palette[i][1] - g) ** 2 + (palette[i][2] - b) ** 2)


# ------------------------------------------------------------------------------- plano de camadas
@dataclass
class LaserGroup:
    """Uma camada do arquivo exportado: um material + uma cor de origem."""
    material: str
    aci: int                          # cor original (como sairia no DXF)
    layers: list[str] = field(default_factory=list)
    count: int = 0                    # primitivas
    rd_index: int = 0                 # camada do RDWorks que vai receber
    target_aci: int = 0               # cor gravada no DXF (igual a aci, ou outra se precisou separar)

    @property
    def key(self) -> str:
        return f"{self.material}|{self.aci}"


def groups_from_parts(parts: Iterable[Part]) -> list[LaserGroup]:
    """Camadas (material × cor) presentes nas peças, na ordem em que aparecem."""
    out: dict[tuple, LaserGroup] = {}
    for part in parts:
        if part.quantity <= 0:
            continue
        for p in part.prims:
            if p.kind in ("TEXT", "MTEXT"):
                continue
            k = (part.material or "", export_aci(p))
            g = out.get(k)
            if g is None:
                g = out[k] = LaserGroup(k[0], k[1])
            g.count += 1
            if p.layer and p.layer not in g.layers:
                g.layers.append(p.layer)
    return list(out.values())


def plan_layers(groups: list[LaserGroup], palette: Optional[list] = None,
                material_order: Optional[list] = None) -> list[LaserGroup]:
    """Escolhe a camada do RDWorks de cada grupo. O 1º material fica com as cores naturais; um
    material seguinte que caia numa camada já usada vai para uma cor livre da paleta."""
    palette = palette or DEFAULT_PALETTE
    order = {m: i for i, m in enumerate(material_order or [])}
    groups = sorted(groups, key=lambda g: (order.get(g.material, len(order)), g.material))
    owner: dict[int, str] = {}
    exact = {i: _exact_aci(c) for i, c in enumerate(palette)}
    for g in groups:
        idx = nearest_layer(aci_rgb(g.aci), palette)
        g.rd_index, g.target_aci = idx, g.aci
        if owner.get(idx, g.material) != g.material:
            free = [i for i in range(len(palette)) if i not in owner and exact.get(i)]
            if free:
                g.rd_index, g.target_aci = free[0], exact[free[0]]
        owner.setdefault(g.rd_index, g.material)
    return groups


def color_map(groups: list[LaserGroup]) -> dict[tuple, int]:
    """(material, cor original) -> cor a gravar no DXF, só para os grupos que mudaram de cor."""
    return {(g.material, g.aci): g.target_aci for g in groups if g.target_aci != g.aci}


# --------------------------------------------------------------------------- arquivo do RDWorks
@dataclass
class LayerTable:
    offset: int                       # posição da velocidade da camada 0
    stride: int
    count: int


def _plausible(data, o) -> bool:
    if o < 8 or o + 8 * FIELDS > len(data):
        return False
    s, *pw = struct.unpack_from("<5d", data, o)
    return 0 < s <= 10000 and all(0 <= x <= 100 for x in pw)


def find_tables(data: bytes) -> list[LayerTable]:
    """Tabelas de camadas: registros de tamanho fixo que começam com preto, azul, vermelho, verde."""
    tables = []
    pos = 0
    while True:
        i = data.find(b"\x00\x00\x00", pos)
        if i < 0 or i + 8 >= len(data):
            break
        pos = i + 1
        o = i + 8
        if not _plausible(data, o):
            continue
        j = data.find(b"\x00\x00\xff", o, o + 6000)
        while j >= 0:
            stride = j + 8 - o
            if stride >= 8 * FIELDS and data[o + 2 * stride - 8:o + 2 * stride - 5] == b"\xff\x00\x00" \
                    and data[o + 3 * stride - 8:o + 3 * stride - 5] == b"\x00\xff\x00" \
                    and all(_plausible(data, o + k * stride) for k in range(4)):
                n = 0
                while n < 256 and _plausible(data, o + n * stride):
                    n += 1
                tables.append(LayerTable(o, stride, n))
                pos = o + (n - 1) * stride + 1
                break
            j = data.find(b"\x00\x00\xff", j + 1, o + 6000)
    return tables


def palette_of(data: bytes, table: LayerTable) -> list[tuple[int, int, int]]:
    return [tuple(data[table.offset + k * table.stride - 8:table.offset + k * table.stride - 5])
            for k in range(table.count)]


def read_layer(data: bytes, table: LayerTable, index: int) -> tuple[float, float, float]:
    """(velocidade, potência mín., potência máx.) do tubo 1."""
    s, pmin, pmax, _, _ = struct.unpack_from("<5d", data, table.offset + index * table.stride)
    return s, pmin, pmax


def patch(data: bytes, values: dict[int, tuple[float, float]]) -> bytes:
    """Troca velocidade e potência (mín = máx, nos dois tubos) das camadas pedidas, em todas as tabelas."""
    tables = find_tables(data)
    if not tables:
        raise ValueError("não reconheci a tabela de camadas do RDWorks (versão diferente?)")
    out = bytearray(data)
    for t in tables:
        for idx, (speed, power) in values.items():
            if not 0 <= idx < t.count:
                continue
            o = t.offset + idx * t.stride
            if not _plausible(out, o):
                raise ValueError(f"registro inesperado na camada {idx}")
            struct.pack_into("<5d", out, o, float(speed), float(power), float(power), float(power), float(power))
    return bytes(out)


def config_path(exe: str) -> str:
    return os.path.join(os.path.dirname(exe), CONFIG_NAME)


def read_palette(exe: Optional[str]) -> Optional[list]:
    try:
        with open(config_path(exe), "rb") as fh:
            data = fh.read()
        tables = find_tables(data)
        return palette_of(data, tables[0]) if tables else None
    except (OSError, TypeError):
        return None


def apply_to_config(path: str, values: dict[int, tuple[float, float]]) -> None:
    """Grava os valores no config do RDWorks (guarda uma cópia antes). Pode dar PermissionError."""
    with open(path, "rb") as fh:
        data = fh.read()
    new = patch(data, values)
    if new == data:
        return
    backup = os.path.join(os.path.dirname(path), BACKUP_NAME)
    if not os.path.exists(backup):
        shutil.copy2(path, backup)
    tmp = path + ".sindri"
    with open(tmp, "wb") as fh:
        fh.write(new)
    os.replace(tmp, path)


def rdworks_running() -> bool:
    if sys.platform != "win32":
        return False
    try:
        out = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True, timeout=10,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.lower()
    except (OSError, subprocess.SubprocessError):
        return False
    return any(n.lower() in out for n in ("rdworksv8.exe", "rdworks.exe", "laserworks.exe", "laserworksv8.exe"))


# ------------------------------------------------------- ajudante com permissão de administrador
def run_helper(job_path: str) -> int:
    """Executado já como administrador: grava o config e abre o RDWorks (um só pedido de permissão)."""
    import json
    with open(job_path, encoding="utf-8") as fh:
        job = json.load(fh)
    err = ""
    try:
        apply_to_config(job["config"], {int(k): tuple(v) for k, v in job["values"].items()})
    except Exception as e:                       # abre mesmo assim; o Sindri mostra o erro depois
        err = str(e)
    if err:
        with open(job_path + ".erro", "w", encoding="utf-8") as fh:
            fh.write(err)
    if job.get("exe") and job.get("file"):
        subprocess.Popen([job["exe"], job["file"]], cwd=os.path.dirname(job["exe"]) or None, close_fds=True)
    return 0


def launch_elevated_helper(job_path: str) -> None:
    """Roda o ajudante como administrador (o Windows mostra o pedido de permissão)."""
    import ctypes
    from ctypes import wintypes
    if getattr(sys, "frozen", False):
        exe, args = sys.executable, f'--rdworks-helper "{job_path}"'
    else:
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        exe = pyw if os.path.isfile(pyw) else sys.executable
        args = f'"{os.path.join(root, "sindri.py")}" --rdworks-helper "{job_path}"'
    fn = ctypes.windll.shell32.ShellExecuteW
    fn.restype = ctypes.c_void_p
    fn.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                   ctypes.c_int]
    r = fn(None, "runas", exe, args, None, 0) or 0
    if r <= 32:
        raise OSError("o pedido de permissão do Windows foi recusado" if r == 5
                      else f"o Windows não conseguiu abrir o ajudante (código {r})")
