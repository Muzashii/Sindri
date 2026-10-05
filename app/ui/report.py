"""Relatório do encaixe (PDF, A4 deitado).

1. Resumo + checklist: uma linha por placa (☐ cortada) e uma por solicitação (☐ separada/entregue).
2. Uma página por placa: cada peça pintada com a cor da sua solicitação e com o nº escrito em cima
   (numa solicitação só: o nº da peça), legenda ao lado e ☐ "placa cortada".
3. Lista de peças por solicitação, com ☐ para ir marcando.

As caixinhas são desenhadas (servem impressas) e, se a biblioteca ``pypdf`` estiver instalada,
também viram caixas clicáveis no PDF — dá para marcar na tela e salvar.
"""
from __future__ import annotations

import datetime as _dt
import os
from dataclasses import dataclass, field

from PySide6.QtCore import QMarginsF, QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetricsF, QPageLayout, QPageSize, QPainter,
                           QPdfWriter, QPen)
from shapely import affinity
from shapely.ops import polylabel

from ..core.models import NestParams, NestResult, Part, Placement
from .render import part_graphics

from .owners import OWNER_COLORS, part_number
RES = 150                      # dpi do PDF
MARGIN_MM = 12.0


@dataclass
class Owner:
    key: str                   # nº da solicitação ("" = sem solicitação)
    color: QColor
    title: str                 # "8759"
    who: str = ""              # "Aluno · RM 123"
    extra: str = ""            # projeto / professor
    parts: list[str] = field(default_factory=list)


def owners_for(parts: dict[str, Part], placements: list[Placement], requests: list[dict] | None) -> dict[str, Owner]:
    """Uma 'dona' por solicitação (tag das peças). Sem tag: a solicitação única (ou o arquivo)."""
    reqs = {str(r.get("code", "")): r for r in (requests or [])}
    keys = []
    for pl in sorted(placements, key=lambda p: (p.sheet_index,)):
        k = parts[pl.part_id].tag
        if k not in keys:
            keys.append(k)
    keys.sort(key=lambda k: (k == "", k))
    from .owners import owner_colors
    cols = owner_colors(parts.values())          # mesmas cores da tela
    out = {}
    for i, k in enumerate(keys):
        r = reqs.get(k) or (next(iter(reqs.values())) if (not k and len(reqs) == 1) else {})
        title = k or (str(r.get("code", "")) if r else "")
        who = " · ".join(x for x in (r.get("nome", ""), f"RM {r['rm']}" if r.get("rm") else "") if x)
        extra = " · ".join(x for x in (r.get("projeto", ""), f"Prof. {r['professor']}" if r.get("professor") else "")
                           if x)
        out[k] = Owner(k, QColor(cols.get(k, QColor(OWNER_COLORS[0]))), title or "Peças", who, extra)
    for p in parts.values():
        if p.tag in out:
            out[p.tag].parts.append(p.id)
    return out


def placed_outline(part: Part, pl: Placement):
    g = part.outer
    if pl.mirrored:
        g = affinity.scale(g, -1, 1, origin=(0, 0))
    g = affinity.rotate(g, pl.rotation, origin=(0, 0))
    return affinity.translate(g, pl.x, pl.y)


def _label_point(poly):
    try:
        return polylabel(poly, tolerance=max(0.5, (poly.bounds[2] - poly.bounds[0]) / 50))
    except Exception:
        return poly.representative_point()


def render_sheet(painter: QPainter, target: QRectF, parts: dict[str, Part], placements: list[Placement],
                 params: NestParams, sheet: int, owners: dict[str, Owner] | None = None,
                 label_mode: str = "tag"):
    """Desenha a placa. Peças pintadas com a cor da solicitação e o nº escrito em cima."""
    w, h = params.sheet_width, params.sheet_height
    placements = [pl for pl in placements if pl.sheet_index == sheet]
    s = min(target.width() / w, target.height() / h)
    ox = target.left() + (target.width() - w * s) / 2
    oy = target.top() + (target.height() + h * s) / 2
    pls = [pl for pl in placements if pl.sheet_index == sheet]
    painter.save()
    painter.translate(ox, oy)
    painter.scale(s, -s)
    painter.fillRect(QRectF(0, 0, w, h), QColor("#f4f5f7"))
    painter.setPen(QPen(QColor("#555"), 0))
    painter.drawRect(QRectF(0, 0, w, h))
    for pl in pls:
        part = parts[pl.part_id]
        g = part_graphics(part, pl.mirrored)
        own = owners.get(part.tag) if owners else None
        painter.save()
        painter.translate(pl.x, pl.y)
        painter.rotate(pl.rotation)
        fill = QColor(own.color if own else g.color)
        fill.setAlpha(95 if own else 50)
        painter.fillPath(g.fill, QBrush(fill))
        for col, path in g.lines:
            painter.setPen(QPen(own.color.darker(140) if own else col, 0))
            painter.drawPath(path)
        painter.restore()
    painter.restore()
    # textos (em coordenadas da página, sem espelhar)
    for pl in pls:
        part = parts[pl.part_id]
        poly = placed_outline(part, pl)
        pt = _label_point(poly)
        minx, miny, maxx, maxy = poly.bounds
        txt = part.tag if (label_mode == "tag" and part.tag) else part_number(part)
        box_w, box_h = (maxx - minx) * s, (maxy - miny) * s
        size = min(box_h * 0.42, box_w / max(1.0, 0.62 * len(txt)), 34.0)
        if size < 5:
            continue
        f = QFont("Arial")
        f.setBold(True)
        f.setPixelSize(int(size))
        painter.setFont(f)
        fm = QFontMetricsF(f)
        X, Y = ox + pt.x * s, oy - pt.y * s
        r = QRectF(X - fm.horizontalAdvance(txt) / 2 - 3, Y - fm.height() / 2, fm.horizontalAdvance(txt) + 6,
                   fm.height())
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 215))
        painter.drawRoundedRect(r, 3, 3)
        own = owners.get(part.tag) if owners else None
        painter.setPen(own.color.darker(160) if own else QColor("#222"))
        painter.drawText(r, Qt.AlignCenter, txt)
    painter.setBrush(Qt.NoBrush)


def sheet_stats(parts: dict[str, Part], placements: list[Placement], params: NestParams, sheet: int,
                index=None):
    from ..core.sheets import SheetIndex
    idx = index or SheetIndex(parts, placements)
    return idx.count(sheet), idx.net_area(sheet) / (params.sheet_width * params.sheet_height)


def sheet_titles(parts, placements, index=None) -> list[tuple[int, int, str, str]]:
    """[(índice da placa, nº geral 1..N, título, material)] na ordem do arquivo 'todas as placas'."""
    from ..core.sheets import SheetIndex
    idx = index or SheetIndex(parts, placements)
    out = []
    total = len(idx.number)
    for mat, sis in idx.groups:
        for k, si in enumerate(sis):
            n = idx.number[si]
            t = f"Placa {n} — {mat} · {k + 1} de {len(sis)}" if mat else f"Placa {n} de {total}"
            out.append((si, n, t, mat))
    return out


# ---------------------------------------------------------------------------------------------------
class _Doc:
    """Ajuda a escrever o PDF: fontes, quebra de página e caixinhas de marcar."""

    def __init__(self, path: str, title: str):
        self.writer = QPdfWriter(path)
        self.writer.setPageSize(QPageSize(QPageSize.A4))
        self.writer.setPageOrientation(QPageLayout.Landscape)
        self.writer.setPageMargins(QMarginsF(MARGIN_MM, MARGIN_MM, MARGIN_MM, MARGIN_MM), QPageLayout.Millimeter)
        self.writer.setResolution(RES)
        self.writer.setTitle(title)
        self.p = QPainter(self.writer)
        if not self.p.isActive():
            raise OSError(f"não foi possível gravar {os.path.basename(path)} — se ele estiver aberto "
                          "(navegador/leitor de PDF), feche e exporte de novo")
        self.p.setRenderHint(QPainter.Antialiasing)
        self.W, self.H = float(self.writer.width()), float(self.writer.height())
        self.page = 0
        self.y = 0.0
        self.boxes: list[tuple[int, QRectF, str]] = []

    def font(self, pt: float, bold: bool = False) -> QFont:
        f = QFont("Arial")
        f.setPointSizeF(pt)
        f.setBold(bold)
        self.p.setFont(f)
        return f

    def new_page(self):
        self.writer.newPage()
        self.page += 1
        self.y = 0.0

    def need(self, h: float, on_break=None):
        if self.y + h > self.H:
            self.new_page()
            if on_break:
                on_break()

    def checkbox(self, x: float, y: float, size: float, name: str):
        r = QRectF(x, y, size, size)
        self.p.save()
        self.p.setPen(QPen(QColor("#333"), 2))
        self.p.setBrush(QColor("white"))
        self.p.drawRoundedRect(r, 3, 3)
        self.p.restore()
        self.boxes.append((self.page, r, name))

    def text(self, x: float, y: float, w: float, h: float, txt: str, color: str = "#222",
             align=Qt.AlignLeft | Qt.AlignVCenter):
        self.p.setPen(QColor(color))
        self.p.drawText(QRectF(x, y, w, h), align, txt)

    def swatch(self, x: float, y: float, size: float, color: QColor):
        self.p.save()
        self.p.setPen(Qt.NoPen)
        self.p.setBrush(color)
        self.p.drawRoundedRect(QRectF(x, y, size, size), 4, 4)
        self.p.restore()

    def end(self):
        self.p.end()


def _add_clickable_boxes(path: str, boxes: list[tuple[int, QRectF, str]], paint_w: float):
    """Transforma as caixinhas desenhadas em caixas de marcar do PDF (se houver pypdf)."""
    try:
        from pypdf import PdfWriter
        from pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject, FloatObject, NameObject,
                                   NumberObject, TextStringObject)
    except Exception:
        return False
    w = PdfWriter(clone_from=path)
    fields = ArrayObject()
    for i, (pg, r, name) in enumerate(boxes):
        if pg >= len(w.pages):
            continue
        page = w.pages[pg]
        mb = page.mediabox
        pw, ph = float(mb.width), float(mb.height)
        m = MARGIN_MM / 25.4 * 72
        k = (pw - 2 * m) / paint_w                       # px -> pt
        x0, x1 = m + r.left() * k, m + r.right() * k
        y1, y0 = ph - (m + r.top() * k), ph - (m + r.bottom() * k)
        sz = x1 - x0

        def form(data: bytes, sz=sz):
            st = DecodedStreamObject()
            st.set_data(data)
            st.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Form"),
                       NameObject("/BBox"): ArrayObject([FloatObject(0), FloatObject(0), FloatObject(sz),
                                                         FloatObject(sz)])})
            return w._add_object(st)

        lw = max(1.2, sz * 0.14)
        on = form((f"q 0.05 0.55 0.25 RG {lw:.2f} w 1 J 1 j {sz * .2:.2f} {sz * .52:.2f} m "
                   f"{sz * .42:.2f} {sz * .26:.2f} l {sz * .82:.2f} {sz * .8:.2f} l S Q").encode())
        off = form(b"")
        annot = DictionaryObject({
            NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Widget"),
            NameObject("/FT"): NameObject("/Btn"), NameObject("/T"): TextStringObject(f"c{i}_{name}"[:120]),
            NameObject("/Rect"): ArrayObject([FloatObject(x0), FloatObject(y0), FloatObject(x1), FloatObject(y1)]),
            NameObject("/V"): NameObject("/Off"), NameObject("/AS"): NameObject("/Off"),
            NameObject("/F"): NumberObject(4),
            NameObject("/AP"): DictionaryObject({NameObject("/N"): DictionaryObject(
                {NameObject("/Yes"): on, NameObject("/Off"): off})}),
            NameObject("/MK"): DictionaryObject(),
        })
        ref = w._add_object(annot)
        annot[NameObject("/P")] = page.indirect_reference
        if "/Annots" not in page:
            page[NameObject("/Annots")] = ArrayObject()
        page["/Annots"].append(ref)
        fields.append(ref)
    w._root_object[NameObject("/AcroForm")] = DictionaryObject({NameObject("/Fields"): fields})
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        w.write(fh)
    from ..core.fileutil import replace_file
    replace_file(tmp, path)
    return True


def export_pdf(path: str, title: str, parts: dict[str, Part], result: NestResult, params: NestParams,
               header: list[str] | None = None, requests: list[dict] | None = None) -> str:
    pls = result.placements
    owners = owners_for(parts, pls, requests)
    batch = any(k for k in owners)
    label_mode = "tag" if len([k for k in owners if k]) > 1 else "num"
    from ..core.sheets import SheetIndex
    idx = SheetIndex(parts, pls)
    titles = sheet_titles(parts, pls, idx)
    by_tag: dict[str, list] = {}
    for pl in pls:
        by_tag.setdefault(parts[pl.part_id].tag, []).append(pl)
    d = _Doc(path, title)
    p = d.p
    row = 46.0                                  # altura de linha das tabelas (px a 150 dpi ≈ 7,8 mm)
    cb = 26.0

    # ------------------------------------------------------------ página 1: resumo + checklist
    d.font(18, True)
    d.text(0, 0, d.W, 50, f"Relatório de encaixe — {title}")
    d.font(10)
    info = [*(header or []),
            f"{_dt.datetime.now():%d/%m/%Y %H:%M}  ·  placa {params.sheet_width:g} × {params.sheet_height:g} mm  ·  "
            f"margem {params.margin:g} mm  ·  espaço entre peças {params.spacing:g} mm",
            f"{len(titles)} placa(s)  ·  {len(pls)} peça(s)  ·  aproveitamento médio {100 * result.utilization:.1f}%"
            + (f"  ·  ATENÇÃO: {len(result.unplaced)} peça(s) sem lugar" if result.unplaced else "")]
    d.y = 58
    for ln in info:
        d.text(0, d.y, d.W, 30, ln, "#444")
        d.y += 30

    def table_head(label: str, cols: list[tuple[str, float]]):
        d.need(row * 2)
        d.y += 18
        d.font(12, True)
        d.text(0, d.y, d.W, 34, label)
        d.y += 38
        d.font(9, True)
        p.fillRect(QRectF(0, d.y, d.W, 32), QColor("#eceff3"))
        x = 0.0
        for name, wdt in cols:
            d.text(x + 8, d.y, wdt - 8, 32, name, "#555")
            x += wdt
        d.y += 32

    # checklist das placas
    cols = [("Cortada", 110), ("Placa", 330), ("Material", 220), ("Peças", 110), ("Aproveit.", 130),
            ("Solicitações nesta placa", d.W - 900)]
    table_head("Checklist de corte — marque cada placa ao terminar", cols)
    for si, n, stitle, mat in titles:
        d.need(row, lambda: table_head("Checklist de corte (continuação)", cols))
        cnt, util = sheet_stats(parts, pls, params, si, idx)
        d.font(10)
        d.checkbox(40, d.y + (row - cb) / 2, cb, f"placa{n}")
        d.font(10, True)
        d.text(118, d.y, 322, row, f"Placa {n}")
        d.font(10)
        d.text(448, d.y, 212, row, mat or "—")
        d.text(668, d.y, 102, row, str(cnt))
        d.text(778, d.y, 122, row, f"{100 * util:.0f}%")
        x = 908.0
        keys = idx.tags_of_sheet.get(si, [])
        d.font(9, True)
        for k in sorted(keys):
            o = owners.get(k)
            if not o or not k:
                continue
            fm = QFontMetricsF(p.font())
            tw = fm.horizontalAdvance(o.title) + 16
            if x + tw > d.W:
                break
            p.save()
            p.setPen(Qt.NoPen)
            p.setBrush(o.color)
            p.drawRoundedRect(QRectF(x, d.y + 10, tw, row - 20), 8, 8)
            p.restore()
            d.text(x, d.y + 10, tw, row - 20, o.title, "white", Qt.AlignCenter)
            x += tw + 6
        p.setPen(QPen(QColor("#e3e6ea"), 1))
        p.drawLine(QPointF(0, d.y + row), QPointF(d.W, d.y + row))
        d.y += row

    # solicitações
    if batch or requests:
        cols2 = [("Entregue", 110), ("", 50), ("Solicitação", 200), ("Aluno", 560), ("Peças", 110),
                 ("Placas", d.W - 1030)]
        table_head("Solicitações — separe as peças pela cor e marque ao entregar", cols2)
        for k, o in owners.items():
            d.need(row, lambda: table_head("Solicitações (continuação)", cols2))
            mine = by_tag.get(k, [])
            n_pcs = len(mine)
            where = sorted({idx.number[pl.sheet_index] for pl in mine})
            d.checkbox(40, d.y + (row - cb) / 2, cb, f"entregue{o.title}")
            d.swatch(122, d.y + 10, row - 20, o.color)
            d.font(10, True)
            d.text(168, d.y, 192, row, o.title)
            d.font(10)
            d.text(368, d.y, 552, row, o.who or "—")
            d.text(928, d.y, 102, row, str(n_pcs))
            d.text(1038, d.y, d.W - 1040, row, ", ".join(str(n) for n in where))
            p.setPen(QPen(QColor("#e3e6ea"), 1))
            p.drawLine(QPointF(0, d.y + row), QPointF(d.W, d.y + row))
            d.y += row

    # ------------------------------------------------------------ uma página por placa
    legend_w = 420.0
    for si, n, stitle, mat in titles:
        d.new_page()
        cnt, util = sheet_stats(parts, pls, params, si, idx)
        d.font(16, True)
        d.text(0, 0, d.W - 260, 48, f"{stitle}")
        d.font(10)
        d.text(0, 46, d.W - 260, 30, f"{cnt} peças · aproveitamento {100 * util:.1f}%", "#555")
        d.checkbox(d.W - 250, 10, 34, f"cortada_placa{n}")
        d.font(12, True)
        d.text(d.W - 206, 4, 206, 48, "Placa cortada")
        area = QRectF(0, 90, d.W - legend_w - 20, d.H - 90)
        render_sheet(p, area, parts, pls, params, si, owners, label_mode)
        # legenda
        lx = d.W - legend_w
        ly = 96.0
        d.font(11, True)
        d.text(lx, ly, legend_w, 34, "Nesta placa")
        ly += 40
        counts: dict[str, dict[str, int]] = {}
        for pl in idx.by_sheet.get(si, ()):
            pt = parts[pl.part_id]
            counts.setdefault(pt.tag, {})
            counts[pt.tag][pt.id] = counts[pt.tag].get(pt.id, 0) + 1
        for k in sorted(counts, key=lambda k: (k == "", k)):
            o = owners[k]
            if ly > d.H - 60:
                break
            d.swatch(lx, ly + 4, 30, o.color)
            d.font(11, True)
            d.text(lx + 40, ly, legend_w - 40, 38, f"{o.title}  ·  {sum(counts[k].values())} peça(s)")
            ly += 36
            if o.who:
                d.font(9)
                d.text(lx + 40, ly, legend_w - 40, 30, o.who, "#444")
                ly += 30
            if label_mode == "num":
                d.font(9)
                for pid, c in sorted(counts[k].items()):
                    if ly > d.H - 40:
                        break
                    pt = parts[pid]
                    w_, h_ = pt.size
                    d.text(lx + 40, ly, legend_w - 40, 28, f"{part_number(pt)} → {c}× ({w_:.0f}×{h_:.0f} mm)", "#444")
                    ly += 28
            ly += 14

    # ------------------------------------------------------------ lista de peças por solicitação
    def parts_head(cont: bool = False):
        d.font(16, True)
        d.text(0, 0, d.W, 48, "Lista de peças" + (" (continuação)" if cont else "") +
               " — marque ao conferir cada peça")
        d.y = 56
        d.font(9, True)
        p.fillRect(QRectF(0, d.y, d.W, 32), QColor("#eceff3"))
        x = 0.0
        for name, wdt in pcols:
            d.text(x + 8, d.y, wdt - 8, 32, name, "#555")
            x += wdt
        d.y += 32

    pcols = [("Ok", 90), ("Nº", 90), ("Peça", 620), ("Tamanho (mm)", 260), ("Qtd.", 110), ("Material", 220),
             ("Placas", d.W - 1390)]
    d.new_page()
    parts_head()
    for k, o in owners.items():
        d.need(row * 2, lambda: parts_head(True))
        p.fillRect(QRectF(0, d.y + 6, d.W, row - 6), QColor(o.color.red(), o.color.green(), o.color.blue(), 40))
        d.swatch(10, d.y + 14, row - 22, o.color)
        d.font(11, True)
        d.text(56, d.y + 6, d.W - 60, row - 6, f"{o.title}   {o.who}" + (f"   ·   {o.extra}" if o.extra else ""))
        d.y += row
        placed: dict[str, list[int]] = {}
        for pl in sorted(by_tag.get(k, []), key=lambda q: idx.number[q.sheet_index]):
            placed.setdefault(pl.part_id, []).append(idx.number[pl.sheet_index])
        for pid in sorted(placed, key=lambda x: int("".join(c for c in x if c.isdigit()) or 0)):
            d.need(row, lambda: parts_head(True))
            pt = parts[pid]
            w_, h_ = pt.size
            ns = placed[pid]
            d.checkbox(32, d.y + (row - cb) / 2, cb, f"peca{pid}")
            d.font(10, True)
            d.text(98, d.y, 82, row, part_number(pt))
            d.font(10)
            name = pt.name.split(" · ", 1)[1] if (pt.tag and " · " in pt.name) else pt.name
            d.text(188, d.y, 612, row, name)
            d.text(808, d.y, 252, row, f"{w_:.1f} × {h_:.1f}")
            d.font(10, True)
            d.text(1068, d.y, 102, row, f"{len(ns)}")
            d.font(10)
            d.text(1178, d.y, 212, row, pt.material or "—")
            d.text(1398, d.y, d.W - 1400, row, ", ".join(f"{n}" + (f" ({ns.count(n)}×)" if ns.count(n) > 1 else "")
                                                        for n in sorted(set(ns))))
            p.setPen(QPen(QColor("#e3e6ea"), 1))
            p.drawLine(QPointF(0, d.y + row), QPointF(d.W, d.y + row))
            d.y += row
        d.y += 10
    if result.unplaced:
        d.need(row * 2, lambda: parts_head(True))
        d.font(11, True)
        d.text(0, d.y + 10, d.W, row, f"Sem lugar (não estão em nenhuma placa): {len(result.unplaced)} peça(s)", "#b91c1c")
    paint_w = d.W
    d.end()
    try:                                   # caixinhas clicáveis são um extra: nunca derrubam a exportação
        _add_clickable_boxes(path, d.boxes, paint_w)
    except Exception:
        pass
    return path

