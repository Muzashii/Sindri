"""Gera os DXFs de exemplo usados nos testes (rode: python tests/fixtures/make_fixtures.py)."""
from __future__ import annotations

import math
import os

import ezdxf
from ezdxf.math import Vec2

HERE = os.path.dirname(os.path.abspath(__file__))


def _new(units=4, version="R2000"):
    doc = ezdxf.new(version)
    doc.header["$INSUNITS"] = units
    doc.layers.add("CORTE", color=1)      # vermelho
    doc.layers.add("GRAVACAO", color=5)   # azul
    return doc


def rect(msp, x, y, w, h, layer="CORTE", **kw):
    return msp.add_lwpolyline([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], close=True,
                              dxfattribs={"layer": layer, **kw})


def simple():
    doc = _new()
    msp = doc.modelspace()
    rect(msp, 0, 0, 100, 50)
    rect(msp, 200, 0, 100, 50)
    rect(msp, 0, 100, 60, 60)
    msp.add_circle((300, 200), 30, dxfattribs={"layer": "CORTE"})
    doc.saveas(os.path.join(HERE, "simples.dxf"))


def lid(msp, x, y, w=160, h=100, text=True):
    """Tampa com rasgos de encaixe (furos), um furo redondo e gravação."""
    rect(msp, x, y, w, h)
    # rasgos (slots) com arcos nas pontas via bulge
    for i in range(3):
        sx = x + 25 + i * 50
        msp.add_lwpolyline([(sx, y + 15, 0), (sx + 20, y + 15, 1), (sx + 20, y + 21, 0), (sx, y + 21, 1)],
                           format="xyb", close=True, dxfattribs={"layer": "CORTE"})
    msp.add_circle((x + w / 2, y + h / 2 + 15), 20, dxfattribs={"layer": "CORTE"})
    if text:
        msp.add_text("LAB MAKER", height=6, dxfattribs={"layer": "GRAVACAO"}).set_placement(
            (x + 10, y + h - 15))
        msp.add_line((x + 10, y + h - 18), (x + 70, y + h - 18), dxfattribs={"layer": "GRAVACAO"})


def arc_piece(msp, cx, cy, r_in=60, r_out=80, a0=20, a1=160, loose=True):
    """Arco/curva: dois ARC concêntricos ligados por LINEs (entidades soltas)."""
    if loose:
        msp.add_arc((cx, cy), r_out, a0, a1, dxfattribs={"layer": "CORTE"})
        msp.add_arc((cx, cy), r_in, a0, a1, dxfattribs={"layer": "CORTE"})
        for a in (a0, a1):
            ra = math.radians(a)
            msp.add_line((cx + r_in * math.cos(ra), cy + r_in * math.sin(ra)),
                         (cx + r_out * math.cos(ra), cy + r_out * math.sin(ra)),
                         dxfattribs={"layer": "CORTE"})
    else:
        p1 = Vec2.from_deg_angle(a0, r_out) + (cx, cy)
        p2 = Vec2.from_deg_angle(a1, r_out) + (cx, cy)
        p3 = Vec2.from_deg_angle(a1, r_in) + (cx, cy)
        p4 = Vec2.from_deg_angle(a0, r_in) + (cx, cy)
        b_out = math.tan(math.radians(a1 - a0) / 4)
        msp.add_lwpolyline([(p1.x, p1.y, b_out), (p2.x, p2.y, 0), (p3.x, p3.y, -b_out), (p4.x, p4.y, 0)],
                           format="xyb", close=True, dxfattribs={"layer": "CORTE"})


def holes():
    doc = _new()
    msp = doc.modelspace()
    lid(msp, 0, 0)
    doc.saveas(os.path.join(HERE, "furos.dxf"))


def loose_lines():
    doc = _new()
    msp = doc.modelspace()
    # retângulo de LINEs com pequenas folgas (0.03 mm) e fora de ordem
    g = 0.03
    msp.add_line((0, 0), (100, 0))
    msp.add_line((100, 50), (0 + g, 50))
    msp.add_line((100 + g, 0), (100, 50 - g))
    msp.add_line((0, 50), (0, 0 + g))
    arc_piece(msp, 250, 0)
    doc.saveas(os.path.join(HERE, "linhas_soltas.dxf"))


def blocks():
    doc = _new()
    blk = doc.blocks.new("PECA")
    blk.add_lwpolyline([(0, 0), (40, 0), (40, 20), (0, 20)], close=True)  # camada 0 => herda
    blk.add_circle((10, 10), 4)
    inner = doc.blocks.new("DUPLA")
    inner.add_blockref("PECA", (0, 0))
    inner.add_blockref("PECA", (60, 0), dxfattribs={"rotation": 90})
    msp = doc.modelspace()
    msp.add_blockref("PECA", (0, 0), dxfattribs={"layer": "CORTE", "color": 0})
    msp.add_blockref("PECA", (100, 0), dxfattribs={"layer": "CORTE", "rotation": 30})
    msp.add_blockref("PECA", (200, 0), dxfattribs={"layer": "CORTE", "xscale": 2, "yscale": 2})
    msp.add_blockref("DUPLA", (0, 100), dxfattribs={"layer": "CORTE"})
    doc.saveas(os.path.join(HERE, "blocos.dxf"))


def inches():
    doc = _new(units=1)
    msp = doc.modelspace()
    rect(msp, 0, 0, 4, 2)  # 101.6 × 50.8 mm
    doc.saveas(os.path.join(HERE, "polegadas.dxf"))


def no_units():
    doc = _new(units=0)
    rect(doc.modelspace(), 0, 0, 30, 30)
    doc.saveas(os.path.join(HERE, "sem_unidade.dxf"))


def duplicates():
    doc = _new()
    msp = doc.modelspace()
    for _ in range(2):
        msp.add_line((0, 0), (50, 0))
        msp.add_line((50, 0), (50, 50))
        msp.add_line((50, 50), (0, 50))
        msp.add_line((0, 50), (0, 0))
    msp.add_line((50, 0), (0, 0))  # mesma linha invertida
    doc.saveas(os.path.join(HERE, "duplicadas.dxf"))


def open_contour():
    doc = _new()
    msp = doc.modelspace()
    msp.add_line((0, 0), (80, 0))
    msp.add_line((80, 0), (80, 40))
    msp.add_line((80, 40), (10, 40))   # falta fechar (folga de 10 mm)
    rect(msp, 200, 0, 50, 50)
    msp.add_text("SOLTO", height=8).set_placement((300, 0))
    doc.saveas(os.path.join(HERE, "contorno_aberto.dxf"))


def spline_ellipse():
    doc = _new()
    msp = doc.modelspace()
    msp.add_ellipse((0, 0), major_axis=(40, 0), ratio=0.5, dxfattribs={"layer": "CORTE"})
    # gota: spline aberta + linha fechando
    msp.add_spline([(100, 0), (120, 30), (140, 40), (160, 30), (180, 0)],
                        dxfattribs={"layer": "CORTE"})
    msp.add_line((180, 0), (100, 0), dxfattribs={"layer": "CORTE"})
    # meia elipse + linha
    msp.add_ellipse((300, 0), major_axis=(50, 0), ratio=0.6, start_param=0, end_param=math.pi,
                    dxfattribs={"layer": "CORTE"})
    msp.add_line((250, 0), (350, 0), dxfattribs={"layer": "CORTE"})
    doc.saveas(os.path.join(HERE, "spline_elipse.dxf"))


def example_lab():
    """Arquivo parecido com o caso real: peças soltas, repetidas e desorganizadas."""
    doc = _new()
    msp = doc.modelspace()
    import random
    rnd = random.Random(42)
    # 15 arcos (metade como entidades soltas, metade como polilinha), espalhados e girados
    for i in range(15):
        cx = (i % 5) * 230 + rnd.uniform(-15, 15)
        cy = (i // 5) * 200 + rnd.uniform(-10, 10)
        rot = rnd.choice([0, 30, 90, 200])
        arc_piece(msp, cx, cy, a0=20 + rot, a1=160 + rot, loose=(i % 2 == 0))
    # 2 tampas com rasgos
    lid(msp, 0, 700)
    lid(msp, 250, 750)
    # 4 laterais
    for i in range(4):
        x = 500 + i * 130
        rect(msp, x, 700, 110, 70)
        msp.add_circle((x + 15, 715), 3, dxfattribs={"layer": "CORTE"})
        msp.add_circle((x + 95, 715), 3, dxfattribs={"layer": "CORTE"})
    # 6 tiras finas
    for i in range(6):
        rect(msp, (i % 2) * 350, 900 + (i // 2) * 30, 300, 12)
    # 8 peças em L
    for i in range(8):
        x, y = 1150 + (i % 2) * 120, i * 90
        msp.add_lwpolyline([(x, y), (x + 100, y), (x + 100, y + 25), (x + 25, y + 25), (x + 25, y + 70), (x, y + 70)],
                           close=True, dxfattribs={"layer": "CORTE"})
    doc.saveas(os.path.join(HERE, "exemplo_lab.dxf"))


def variety():
    """Entidades menos comuns: POLYLINE 2D com bulge, MTEXT, MINSERT, ATTRIB, extrusão -Z, cor RGB."""
    doc = _new(version="R2010")
    msp = doc.modelspace()
    msp.add_polyline2d([(0, 0, 0), (50, 0, 0.5), (50, 30, 0), (0, 30, 0)], format="xyb", close=True,
                       dxfattribs={"layer": "CORTE"})
    msp.add_mtext("Gravação\\Plinha 2", dxfattribs={"insert": (5, 25), "char_height": 3, "layer": "GRAVACAO"})
    # círculo e arco com extrusão invertida (comum em arquivos do CorelDraw/Inkscape)
    # OCS com Z invertido: centro OCS (-300, 20) = WCS (300, 20); o arco vai de WCS (130,0) a (170,0)
    msp.add_circle((-300, 20), 10, dxfattribs={"extrusion": (0, 0, -1), "layer": "CORTE"})
    msp.add_arc((-150, 0), 20, 0, 180, dxfattribs={"extrusion": (0, 0, -1), "layer": "CORTE"})
    msp.add_line((130, 0), (170, 0), dxfattribs={"layer": "CORTE"})
    blk = doc.blocks.new("ETIQ")
    blk.add_lwpolyline([(0, 0), (20, 0), (20, 10), (0, 10)], close=True)
    blk.add_attdef("COD", (2, 2), dxfattribs={"height": 3})
    ins = msp.add_blockref("ETIQ", (100, 0), dxfattribs={"layer": "CORTE"})
    ins.add_auto_attribs({"COD": "A1"})
    m = msp.add_blockref("ETIQ", (100, 100), dxfattribs={"layer": "CORTE"})
    m.grid(size=(2, 3), spacing=(15, 30))
    p3 = msp.add_polyline3d([(200, 0, 0), (240, 0, 0), (240, 20, 0), (200, 20, 0)], close=True)
    p3.rgb = (0, 128, 255)
    msp.add_point((500, 500))
    doc.saveas(os.path.join(HERE, "variados.dxf"))


def corrupt():
    with open(os.path.join(HERE, "corrompido.dxf"), "w") as f:
        f.write("isto não é um dxf\n0\nSECTION\n2\nFOO\n")


def main():
    for fn in (simple, holes, loose_lines, blocks, inches, no_units, duplicates, open_contour,
               spline_ellipse, example_lab, variety, corrupt):
        fn()


if __name__ == "__main__":
    main()
