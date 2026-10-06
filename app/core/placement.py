"""Decodificador de posicionamento: ordem + rotações -> posições (estilo SVGnest).

Para cada peça na ordem:
    região válida = IFP(placa) − ∪ NFP(peças já colocadas)
                  ∪ (IFP(furos) − ∪ NFP(outras peças))        [part-in-part]
e escolhe o vértice da região que minimiza o critério:
    * "bbox": menor área da caixa delimitadora do conjunto (desempate: x, y)
    * "left": mais à esquerda (menor largura ocupada), depois mais abaixo
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pyclipper

from .geometry import CLIPPER_SCALE
from .models import NestParams, Placement
from .nfp import NFPCache, PartShape, Variant, round_rot

S = CLIPPER_SCALE


@dataclass
class _Placed:
    pid: str
    instance: int
    variant: Variant
    x: int
    y: int
    rot: float
    mirror: bool
    locked: bool = False
    host: Optional[int] = None   # índice da peça em cujo furo esta foi colocada


@dataclass
class _Sheet:
    placed: list[_Placed] = field(default_factory=list)
    bbox: Optional[list[int]] = None   # [minx, miny, maxx, maxy] das variantes (com folga)
    free_area: float = 0.0             # limite superior da área livre (unid²)
    material: Optional[str] = None     # placas nunca misturam materiais
    failed: set = field(default_factory=set)   # variantes que já não couberam (a placa só enche)

    def add(self, pl: _Placed):
        self.placed.append(pl)
        self.extend_bbox(pl.variant, pl.x, pl.y)
        self.free_area -= pl.variant.area_int
        self.free_area += sum(pl.variant.hole_areas)

    def extend_bbox(self, v: Variant, x: int, y: int):
        b = [v.bbox[0] + x, v.bbox[1] + y, v.bbox[2] + x, v.bbox[3] + y]
        if self.bbox is None:
            self.bbox = b
        else:
            self.bbox = [min(self.bbox[0], b[0]), min(self.bbox[1], b[1]),
                         max(self.bbox[2], b[2]), max(self.bbox[3], b[3])]


@dataclass
class DecodeResult:
    placements: list[Placement]
    sheets_used: int
    unplaced: list[tuple[str, int]]
    fitness: float
    utilization: float
    last_bbox: Optional[list[int]] = None
    sheet_materials: list = field(default_factory=list)


class Decoder:
    def __init__(self, shapes: dict[str, PartShape], params: NestParams,
                 cache: Optional[NFPCache] = None):
        self.shapes = shapes
        self.cancelled = lambda: False
        self.params = params
        self.cache = cache or NFPCache(shapes, params.spacing, params.curve_tolerance,
                                       params.part_in_part, getattr(params, "detail", 1))
        p = params
        half = p.spacing / 2.0
        self.rect = (int(round((p.margin - half) * S)), int(round((p.margin - half) * S)),
                     int(round((p.sheet_width - p.margin + half) * S)),
                     int(round((p.sheet_height - p.margin + half) * S)))
        self.sheet_area = p.sheet_width * p.sheet_height
        self.rect_area = float(self.rect[2] - self.rect[0]) * float(self.rect[3] - self.rect[1])

    def _new_sheet(self) -> _Sheet:
        return _Sheet(free_area=self.rect_area)

    # ------------------------------------------------------------------
    def fits_sheet(self, pid: str, rotations: list[float], mirror_opts=(False,)) -> bool:
        for r in rotations:
            for m in mirror_opts:
                if self.cache.ifp_rect(self.rect, self.cache.variant(pid, r, m)) is not None:
                    return True
        return False

    # ------------------------------------------------------------------
    def _try_place(self, sheet: _Sheet, v: Variant, criterion: str):
        if self.cancelled():
            return None
        if v.key in sheet.failed or v.area_int > sheet.free_area * 1.0001:
            return None
        pos = self._try_place_inner(sheet, v, criterion)
        if pos is None:
            sheet.failed.add(v.key)
        return pos

    def _try_place_inner(self, sheet: _Sheet, v: Variant, criterion: str):
        ifp = self.cache.ifp_rect(self.rect, v)
        candidates = []
        hosts = []   # para cada candidato: índice do anfitrião (furo) ou None
        if ifp is not None:
            pc = pyclipper.Pyclipper()
            pc.AddPath(ifp, pyclipper.PT_SUBJECT, True)
            clips = []
            ix0, iy0 = ifp[0]
            ix1, iy1 = ifp[2]
            for pl in sheet.placed:
                if self.cancelled():
                    return None
                paths, bb = self.cache.nfp_np(pl.variant, v)
                if (bb[2] + pl.x < ix0 or bb[0] + pl.x > ix1 or
                        bb[3] + pl.y < iy0 or bb[1] + pl.y > iy1):
                    continue
                off = np.array([pl.x, pl.y], dtype=np.int64)
                for path in paths:
                    clips.append((path + off).tolist())
            if clips:
                pc.AddPaths(clips, pyclipper.PT_CLIP, True)
            region = pc.Execute(pyclipper.CT_DIFFERENCE, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)
            for path in region:
                if len(path) >= 3 and abs(pyclipper.Area(path)) > 0:
                    candidates.extend(path)
                    hosts.extend([None] * len(path))
        # part-in-part: furos das peças já colocadas
        if self.params.part_in_part:
            for j, host in enumerate(sheet.placed):
                if self.cancelled():
                    return None
                hv = host.variant
                for hi, harea in enumerate(hv.hole_areas):
                    if harea <= v.area_int:
                        continue
                    regs = self.cache.hole_ifp(hv, hi, v)
                    if not regs:
                        continue
                    pc = pyclipper.Pyclipper()
                    pc.AddPaths([[(x + host.x, y + host.y) for x, y in r] for r in regs],
                                pyclipper.PT_SUBJECT, True)
                    hb = np.asarray(hv.holes[hi])
                    hx0, hy0 = hb[:, 0].min() + host.x, hb[:, 1].min() + host.y
                    hx1, hy1 = hb[:, 0].max() + host.x, hb[:, 1].max() + host.y
                    clips = []
                    # o anfitrião e os "ancestrais" dele (peças em cujo furo ele está) não bloqueiam
                    skip = {j}
                    a = host.host
                    while a is not None and a not in skip:
                        skip.add(a)
                        a = sheet.placed[a].host
                    for k, pl in enumerate(sheet.placed):
                        if k in skip:
                            continue
                        # apenas peças que podem estar dentro do furo
                        if (pl.variant.bbox[2] + pl.x < hx0 or pl.variant.bbox[0] + pl.x > hx1 or
                                pl.variant.bbox[3] + pl.y < hy0 or pl.variant.bbox[1] + pl.y > hy1):
                            continue
                        paths, _ = self.cache.nfp_np(pl.variant, v)
                        off = np.array([pl.x, pl.y], dtype=np.int64)
                        for path in paths:
                            clips.append((path + off).tolist())
                    if clips:
                        pc.AddPaths(clips, pyclipper.PT_CLIP, True)
                    region = pc.Execute(pyclipper.CT_DIFFERENCE, pyclipper.PFT_NONZERO,
                                        pyclipper.PFT_NONZERO)
                    for path in region:
                        if len(path) >= 3 and abs(pyclipper.Area(path)) > 0:
                            candidates.extend(path)
                            hosts.extend([j] * len(path))
        if not candidates:
            return None
        c = np.asarray(candidates, dtype=np.int64)
        bx0 = v.bbox[0] + c[:, 0]
        by0 = v.bbox[1] + c[:, 1]
        bx1 = v.bbox[2] + c[:, 0]
        by1 = v.bbox[3] + c[:, 1]
        if sheet.bbox is not None:
            sb = sheet.bbox
            bx0 = np.minimum(bx0, sb[0]); by0 = np.minimum(by0, sb[1])
            bx1 = np.maximum(bx1, sb[2]); by1 = np.maximum(by1, sb[3])
        w = (bx1 - bx0) / S
        h = (by1 - by0) / S
        xs = c[:, 0] / S
        ys = c[:, 1] / S
        if criterion == "left":
            order = np.lexsort((np.round(xs, 1), np.round(ys, 1), np.round(bx1 / S, 1)))
        else:
            area = np.round(w * h, 0)
            order = np.lexsort((np.round(ys, 1), np.round(xs, 1), area))
        best = c[order[0]]
        return int(best[0]), int(best[1]), hosts[int(order[0])]

    # ------------------------------------------------------------------
    def decode(self, order: list[tuple[str, int]], rots: list[float], mirrors: list[bool],
               criterion: str = "bbox", locked: Optional[list[Placement]] = None) -> DecodeResult:
        p = self.params
        sheets: list[_Sheet] = []
        placements: list[Placement] = []
        for lp in locked or []:
            while len(sheets) <= lp.sheet_index:
                sheets.append(self._new_sheet())
            v = self.cache.variant(lp.part_id, lp.rotation, lp.mirrored)
            xi, yi = int(round(lp.x * S)), int(round(lp.y * S))
            sh = sheets[lp.sheet_index]
            if sh.material is None:
                sh.material = self.shapes[lp.part_id].material
            sh.add(_Placed(lp.part_id, lp.instance, v, xi, yi, lp.rotation, lp.mirrored, True))
            placements.append(Placement(lp.part_id, lp.instance, lp.sheet_index, lp.x, lp.y,
                                        lp.rotation, lp.mirrored, True))
        unplaced: list[tuple[str, int]] = []
        mirror_opts = (False, True) if p.allow_mirror else (False,)

        for idx, (pid, inst) in enumerate(order):
            if self.cancelled():
                unplaced.extend(order[idx:])
                break
            shape = self.shapes[pid]
            allowed = shape.rotations
            first = rots[idx] if idx < len(rots) else allowed[0]
            if round_rot(first) not in [round_rot(r) for r in allowed]:
                first = allowed[0]
            rot_try = [first] + [r for r in allowed if round_rot(r) != round_rot(first)]
            m0 = mirrors[idx] if (idx < len(mirrors) and p.allow_mirror) else False
            m_try = [m0] + [m for m in mirror_opts if m != m0]
            done = False
            mat = shape.material
            closed = set(getattr(p, "closed_sheets", None) or [])
            for si in range(len(sheets) + 1):
                if si in closed and si < len(sheets):
                    continue
                if si == len(sheets):
                    if not p.multi_sheet and any(s.material == mat for s in sheets):
                        break
                    if len(sheets) >= p.max_sheets:
                        break
                    sheets.append(self._new_sheet())
                    sheets[-1].material = mat
                sheet = sheets[si]
                if sheet.material is not None and sheet.material != mat:
                    continue
                for m in m_try:
                    for r in rot_try:
                        if self.cancelled():
                            break
                        v = self.cache.variant(pid, r, m)
                        pos = self._try_place(sheet, v, criterion)
                        if pos is not None:
                            if sheet.material is None:      # placa vazia criada antes de uma travada
                                sheet.material = mat
                            sheet.add(_Placed(pid, inst, v, pos[0], pos[1], r, m, host=pos[2]))
                            placements.append(Placement(pid, inst, si, pos[0] / S, pos[1] / S,
                                                        round_rot(r), m))
                            done = True
                            break
                    if done:
                        break
                if done:
                    break
            if not done:
                unplaced.append((pid, inst))
                # remove folha vazia criada sem sucesso
                if sheets and not sheets[-1].placed:
                    sheets.pop()

        # remove placas vazias e agrupa as placas por material (3mm primeiro, depois 6mm…)
        mats = sorted({sh.material or "" for sh in sheets if sh.placed})
        order = sorted([i for i, sh in enumerate(sheets) if sh.placed],
                       key=lambda i: (mats.index(sheets[i].material or ""), i))
        remap = {old: new for new, old in enumerate(order)}
        sheets = [sheets[i] for i in order]
        for pl in placements:
            pl.sheet_index = remap.get(pl.sheet_index, pl.sheet_index)
        n = len(sheets)
        net = sum(self.shapes[pl.part_id].net_area for pl in placements)
        util = net / (n * self.sheet_area) if n else 0.0
        last_bbox = sheets[-1].bbox if sheets else None
        fitness = self.fitness(n, last_bbox, len(unplaced), sheets)
        return DecodeResult(placements, n, unplaced, fitness, util, last_bbox,
                            [sh.material or "" for sh in sheets])

    def fitness(self, n_sheets, last_bbox, n_unplaced, sheets) -> float:
        """Menor é melhor: peças sem lugar, placas extras e compactação da última placa de cada material."""
        groups: dict = {}
        for sh in sheets:
            groups.setdefault(sh.material or "", []).append(sh)
        f = 100.0 * n_unplaced + 2.0 * max(0, n_sheets - max(1, len(groups)))
        for shs in groups.values():
            for k, sh in enumerate(shs):
                if sh.bbox is None:
                    continue
                w = (sh.bbox[2] - sh.bbox[0]) / S
                h = (sh.bbox[3] - sh.bbox[1]) / S
                if k == len(shs) - 1:
                    f += (w * h) / self.sheet_area + 0.1 * w / self.params.sheet_width
                else:
                    f += 0.05 * (w * h) / self.sheet_area
        return f
