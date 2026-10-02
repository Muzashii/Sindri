"""Relatório do encaixe: PDF (resumo + uma página por placa) e PNG por placa."""
from __future__ import annotations

import datetime as _dt
import os

from PySide6.QtCore import QMarginsF, QPointF, QRectF, Qt, QSizeF
from PySide6.QtGui import (QBrush, QColor, QFont, QImage, QPageLayout, QPageSize, QPainter,
                           QPdfWriter, QPen)

from ..core.models import NestParams, NestResult, Part, Placement
from .render import part_graphics


def render_sheet(painter: QPainter, target: QRectF, parts: dict[str, Part],
                 placements: list[Placement], params: NestParams, sheet: int):
    w, h = params.sheet_width, params.sheet_height
    s = min(target.width() / w, target.height() / h)
    painter.save()
    painter.translate(target.left() + (target.width() - w * s) / 2,
                      target.top() + (target.height() + h * s) / 2)
    painter.scale(s, -s)
    painter.fillRect(QRectF(0, 0, w, h), QColor("#eef0f3"))
    pen = QPen(QColor("#555"), 0)
    painter.setPen(pen)
    painter.drawRect(QRectF(0, 0, w, h))
    for pl in placements:
        if pl.sheet_index != sheet:
            continue
        g = part_graphics(parts[pl.part_id], pl.mirrored)
        painter.save()
        painter.translate(pl.x, pl.y)
        painter.rotate(pl.rotation)
        fill = QColor(g.color)
        fill.setAlpha(50)
        painter.fillPath(g.fill, QBrush(fill))
        for col, path in g.lines:
            p = QPen(col, 0)
            painter.setPen(p)
            painter.drawPath(path)
        painter.restore()
    painter.restore()


def sheet_stats(parts: dict[str, Part], placements: list[Placement], params: NestParams, sheet: int):
    pls = [p for p in placements if p.sheet_index == sheet]
    area = sum(parts[p.part_id].outer.area - sum(h.area for h in parts[p.part_id].holes) for p in pls)
    return len(pls), area / (params.sheet_width * params.sheet_height)


def _sheet_titles(parts, placements) -> list[tuple[int, str, str]]:
    """[(índice da placa, título, tag p/ arquivo)] agrupado por material."""
    from ..core.dxf_export import material_tag, sheet_groups
    out = []
    for mat, sis in sheet_groups(parts, placements):
        for k, si in enumerate(sis):
            pre = f"{mat} — " if mat else ""
            out.append((si, f"{pre}Placa {k + 1} de {len(sis)}", f"_{material_tag(mat)}" if mat else ""))
    return out


def export_pdf(path: str, title: str, parts: dict[str, Part], result: NestResult, params: NestParams,
               header: list[str] | None = None):
    writer = QPdfWriter(path)
    writer.setPageSize(QPageSize(QPageSize.A4))
    writer.setPageOrientation(QPageLayout.Landscape)
    writer.setPageMargins(QMarginsF(12, 12, 12, 12), QPageLayout.Millimeter)
    writer.setResolution(150)
    writer.setTitle(title)
    painter = QPainter(writer)
    painter.setRenderHint(QPainter.Antialiasing)
    page = QRectF(0, 0, writer.width(), writer.height())
    big = QFont("Arial", 18, QFont.Bold)
    norm = QFont("Arial", 11)
    sheets = sorted({p.sheet_index for p in result.placements})

    # página de resumo
    painter.setFont(big)
    painter.drawText(QRectF(0, 0, page.width(), 60), Qt.AlignLeft, f"Relatório de encaixe — {title}")
    painter.setFont(norm)
    lines = [
        *(header or []),
        f"Data: {_dt.datetime.now():%d/%m/%Y %H:%M}",
        f"Placa: {params.sheet_width:g} × {params.sheet_height:g} mm · margem {params.margin:g} mm · "
        f"espaço entre peças {params.spacing:g} mm",
        f"Placas usadas: {len(sheets)} · aproveitamento médio: {100 * result.utilization:.1f}%",
        f"Peças encaixadas: {len(result.placements)}" +
        (f" · sem lugar: {len(result.unplaced)}" if result.unplaced else ""),
        "",
        "Peças:",
    ]
    counts: dict[str, int] = {}
    for pl in result.placements:
        counts[pl.part_id] = counts.get(pl.part_id, 0) + 1
    for pid, n in sorted(counts.items()):
        p = parts[pid]
        w, h = p.size
        mat = f" · {p.material}" if p.material else ""
        lines.append(f"   {p.name}: {n} un. ({w:.1f} × {h:.1f} mm){mat}")
    lh = painter.fontMetrics().height() * 1.3
    y = 70
    for ln in lines:
        painter.drawText(QPointF(0, y + lh), ln)
        y += lh
        if y > page.height() - lh:
            break
    for si, stitle, _tag in _sheet_titles(parts, result.placements):
        writer.newPage()
        cnt, util = sheet_stats(parts, result.placements, params, si)
        painter.setFont(big)
        painter.drawText(QRectF(0, 0, page.width(), 50), Qt.AlignLeft,
                         f"{stitle} — {cnt} peças — aproveitamento {100 * util:.1f}%")
        render_sheet(painter, QRectF(0, 60, page.width(), page.height() - 60), parts,
                     result.placements, params, si)
    painter.end()


def export_pngs(out_dir: str, base: str, parts: dict[str, Part], result: NestResult,
                params: NestParams, width_px: int = 1800) -> list[str]:
    files = []
    for si, stitle, tag in _sheet_titles(parts, result.placements):
        n = int(stitle.split("Placa ")[1].split(" ")[0]) - 1
        hpx = int(width_px * params.sheet_height / params.sheet_width) + 70
        img = QImage(width_px, hpx, QImage.Format_ARGB32)
        img.fill(QColor("white"))
        painter = QPainter(img)
        painter.setRenderHint(QPainter.Antialiasing)
        cnt, util = sheet_stats(parts, result.placements, params, si)
        f = QFont("Arial")
        f.setPixelSize(26)
        painter.setFont(f)
        painter.setPen(QColor("#222"))
        painter.drawText(QPointF(20, 40), f"{stitle} · {cnt} peças · aproveitamento {100 * util:.1f}%")
        render_sheet(painter, QRectF(10, 60, width_px - 20, hpx - 70), parts, result.placements,
                     params, si)
        painter.end()
        path = os.path.join(out_dir, f"{base}{tag}_placa{n + 1:02d}.png")
        img.save(path)
        files.append(path)
    return files
