"""Leitura (somente leitura) de arquivos de máquina do RDWorks / controladora Ruida (.rd).

O .rd é o trabalho já pronto para a máquina: só tem movimentos e cortes em linha reta, separados por
camada, com velocidade, potência e cor de cada camada. Não é o projeto do RDWorks (.rld), que o Sindri
não lê.

Formato (documentação pública feita por engenharia reversa — https://edutechwiki.unige.ch/en/Ruida):

* cada byte é embaralhado com uma chave ("mágica") que depende do modelo: 0x88 na maioria, 0x11 nos 634XG;
* depois de desembaralhar, byte >= 0x80 começa um comando e os bytes < 0x80 seguintes são os dados dele;
* coordenadas absolutas: 5 bytes de 7 bits, em micrômetros; relativas: 2 bytes (14 bits com sinal);
* 0x88/0x89/0x8A/0x8B = mover (absoluto, relativo, só X, só Y); 0xA8/0xA9/0xAA/0xAB = cortar;
* 0xCA 0x02 n = camada atual; 0xCA 0x06 n cor = cor da camada; 0xC9 0x04 n v = velocidade;
  0xC6 0x31/0x32 n p = potência mínima/máxima do tubo 1; 0xD7 = fim.

Curvas chegam como muitos segmentos retos: se existir o DXF ou o .ai de origem, ele é melhor.
A VALIDAR NO LABORATÓRIO com um .rd salvo pelo RDWorks da máquina.
"""
from __future__ import annotations

import os
from typing import Optional

from .dxf_import import DXFImportError, RawFile, filter_prims, rgb_to_color
from .import_issues import CHECK, FIXED, ImportIssue
from .models import Prim

MAGICS = (0x88, 0x11)
_MOVES = {0x88, 0x89, 0x8A, 0x8B}
_CUTS = {0xA8, 0xA9, 0xAA, 0xAB}
_KNOWN = _MOVES | _CUTS | {0xC6, 0xC9, 0xCA, 0xD7, 0xD8, 0xE5, 0xE6, 0xE7, 0xE8, 0xF0, 0xF1, 0xF2, 0xDA}
_CLOSE_TOL = 0.02          # mm: fim encostado no começo = contorno fechado
_MAX_MM = 20000.0          # coordenada maior que 20 m: arquivo não é .rd válido


def unscramble(data: bytes, magic: int) -> bytes:
    """Desfaz o embaralhamento dos bytes do arquivo."""
    out = bytearray(len(data))
    for i, b in enumerate(data):
        b = (b - 1) & 0xFF
        b ^= magic
        b ^= (b >> 7) & 0xFF
        b ^= (b << 7) & 0xFF
        b ^= (b >> 7) & 0xFF
        out[i] = b
    return bytes(out)


def _tokens(data: bytes):
    """(comando, dados) — comando é o byte >= 0x80; dados são os bytes < 0x80 até o próximo comando."""
    cmd, payload = None, bytearray()
    for b in data:
        if b & 0x80:
            if cmd is not None:
                yield cmd, bytes(payload)
            cmd, payload = b, bytearray()
        elif cmd is not None:
            payload.append(b)
    if cmd is not None:
        yield cmd, bytes(payload)


def _abs(b: bytes) -> float:
    """5 bytes de 7 bits (35 bits, com sinal) em micrômetros → mm."""
    v = 0
    for x in b[:5]:
        v = (v << 7) | x
    if v >= 1 << 34:
        v -= 1 << 35
    return v / 1000.0


def _rel(b: bytes) -> float:
    """2 bytes de 7 bits (14 bits, com sinal) em micrômetros → mm."""
    v = (b[0] << 7) | b[1]
    if v > 8191:
        v -= 16384
    return v / 1000.0


def _score(data: bytes) -> float:
    """Quanto o fluxo desembaralhado parece um trabalho Ruida (0 a 1)."""
    total = known = draws = 0
    end = False
    for cmd, payload in _tokens(data):
        total += 1
        if cmd in _KNOWN:
            known += 1
        if cmd in (0x88, 0xA8) and len(payload) == 10 or cmd in (0x89, 0xA9) and len(payload) == 4 \
                or cmd in (0x8A, 0x8B, 0xAA, 0xAB) and len(payload) == 2:
            draws += 1
        if cmd == 0xD7:
            end = True
    if not total or not draws:
        return 0.0
    return known / total + (0.25 if end else 0.0)


def decode(data: bytes) -> dict:
    """Decodifica o conteúdo de um .rd. Devolve {"paths": [(camada, [(x, y), …])], "layers": {…},
    "magic": chave, "complete": chegou ao fim?} com Y já virado para cima (como aparece no RDWorks)."""
    best, best_score = None, 0.0
    for magic in MAGICS:
        plain = unscramble(data, magic)
        sc = _score(plain)
        if sc > best_score:
            best, best_score, best_magic = plain, sc, magic
    if best is None or best_score < 0.8:
        sample = data[:65536]                       # modelo desconhecido: procura a chave
        for magic in range(256):
            sc = _score(unscramble(sample, magic))
            if sc > best_score:
                best_score, best_magic = sc, magic
                best = None
        if best_score >= 0.8 and best is None:
            best = unscramble(data, best_magic)
    if best is None or best_score < 0.8:
        raise DXFImportError("não parece um arquivo de máquina do RDWorks (.rd) válido.")

    layers: dict[int, dict] = {}
    paths: list[tuple[int, list]] = []
    cur_layer = 0
    x = y = 0.0
    cur: Optional[list] = None
    complete = False
    bad = 0

    def lay(n):
        return layers.setdefault(int(n), {})

    def flush():
        nonlocal cur
        if cur and len(cur) >= 2:
            paths.append((cur_layer, cur))
        cur = None

    for cmd, p in _tokens(best):
        try:
            if cmd in _MOVES or cmd in _CUTS:
                if cmd in (0x88, 0xA8):
                    nx, ny = _abs(p[0:5]), _abs(p[5:10])
                    if len(p) < 10:
                        raise IndexError
                elif cmd in (0x89, 0xA9):
                    nx, ny = x + _rel(p[0:2]), y + _rel(p[2:4])
                elif cmd in (0x8A, 0xAA):
                    nx, ny = x + _rel(p[0:2]), y
                else:
                    nx, ny = x, y + _rel(p[0:2])
                if abs(nx) > _MAX_MM or abs(ny) > _MAX_MM:
                    raise ValueError
                if cmd in _CUTS:
                    if cur is None:
                        cur = [(x, -y)]
                    cur.append((nx, -ny))
                else:
                    flush()
                x, y = nx, ny
            elif cmd == 0xCA and p:
                if p[0] == 0x02 and len(p) >= 2:
                    flush()
                    cur_layer = p[1]
                    lay(cur_layer)
                elif p[0] == 0x06 and len(p) >= 7:
                    v = 0
                    for b in p[2:7]:
                        v = (v << 7) | b
                    lay(p[1])["color"] = (v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF)
            elif cmd == 0xC9 and p:
                if p[0] == 0x04 and len(p) >= 7:
                    lay(p[1])["speed"] = round(_abs(p[2:7]), 3)          # µm/s → mm/s
                elif p[0] == 0x02 and len(p) >= 6:
                    lay(cur_layer)["speed"] = round(_abs(p[1:6]), 3)
            elif cmd == 0xC6 and p:
                def pct(b):
                    return round(((b[0] << 7) | b[1]) / 16383.0 * 100.0, 1)
                if p[0] == 0x31 and len(p) >= 4:
                    lay(p[1])["power_min"] = pct(p[2:4])
                elif p[0] == 0x32 and len(p) >= 4:
                    lay(p[1])["power_max"] = pct(p[2:4])
                elif p[0] == 0x01 and len(p) >= 3:
                    lay(cur_layer)["power_min"] = pct(p[1:3])
                elif p[0] == 0x02 and len(p) >= 3:
                    lay(cur_layer)["power_max"] = pct(p[1:3])
            elif cmd == 0xD7:
                complete = True
                break
        except (IndexError, ValueError):
            bad += 1
            if bad > 20:
                break                               # fluxo inconsistente: fica com o que já leu
    flush()
    return {"paths": paths, "layers": layers, "magic": best_magic, "complete": complete and bad == 0}


def read_rd(path: str, ignore_text: bool = False, excluded_layers: Optional[set] = None) -> RawFile:
    """Lê um .rd e devolve o mesmo ``RawFile`` do DXF (polilinhas em mm, cor = cor da camada)."""
    name = os.path.basename(path)
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        raise DXFImportError(f"Não foi possível abrir \"{name}\": {exc}") from exc
    try:
        job = decode(data)
    except DXFImportError as exc:
        raise DXFImportError(f"\"{name}\": {exc}") from exc

    prims: list[Prim] = []
    for layer, pts in job["paths"]:
        info = job["layers"].get(layer, {})
        aci, rgb = rgb_to_color(info.get("color", (0, 0, 0)))
        clean = [pts[0]]
        for q in pts[1:]:
            if abs(q[0] - clean[-1][0]) > 1e-9 or abs(q[1] - clean[-1][1]) > 1e-9:
                clean.append(q)
        if len(clean) < 2:
            continue
        closed = len(clean) > 3 and abs(clean[0][0] - clean[-1][0]) <= _CLOSE_TOL \
            and abs(clean[0][1] - clean[-1][1]) <= _CLOSE_TOL
        if closed:
            clean = clean[:-1]
        lname = f"Camada {layer + 1}"
        if len(clean) == 2:
            prims.append(Prim("LINE", {"s": list(clean[0]), "e": list(clean[1])}, lname, aci, rgb))
        else:
            prims.append(Prim("POLY", {"pts": [[q[0], q[1], 0.0] for q in clean], "closed": closed},
                              lname, aci, rgb))

    warnings: list[str] = []
    issues: list = []

    def issue(kind, severity, message, count=1):
        issues.append(ImportIssue(os.path.abspath(path), kind, severity, message, count))

    warnings.append(f"{name}: arquivo de máquina (.rd) — as curvas vêm como segmentos retos. Se tiver o DXF "
                    "ou o .ai de origem, prefira ele.")
    issue("rd_segments", FIXED, "Arquivo de máquina: curvas vêm como segmentos retos.")
    desc = []
    for n in sorted(job["layers"]):
        info = job["layers"][n]
        if "speed" in info or "power_max" in info:
            bits = []
            if "speed" in info:
                bits.append(f"{info['speed']:g} mm/s")
            if "power_max" in info:
                lo = info.get("power_min")
                bits.append(f"{lo:g}–{info['power_max']:g}%" if lo is not None else f"{info['power_max']:g}%")
            desc.append(f"camada {n + 1}: " + ", ".join(bits))
    if desc:
        warnings.append(f"{name}: o arquivo trazia " + "; ".join(desc) + ". O Sindri não aplica esses valores "
                        "sozinho: confira no painel Laser.")
    if not job["complete"]:
        warnings.append(f"{name}: o arquivo terminou antes do esperado; confira se o desenho está inteiro.")
        issue("rd_incomplete", CHECK, "O arquivo .rd terminou antes do esperado; confira o desenho.")

    prims, layers, size = filter_prims(path, prims, ignore_text, excluded_layers, warnings, issues)
    meta = {"format": "rd", "magic": job["magic"],
            "laser": {str(n): dict(v) for n, v in sorted(job["layers"].items())}}
    return RawFile(path, prims, 1.0, "Arquivo de máquina do RDWorks: micrômetros convertidos para mm",
                   warnings, 4, None, size, layers, issues, meta)
