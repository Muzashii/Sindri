import math

import pytest

from app.core.dxf_import import DXFImportError, read_dxf
from app.core.part_builder import import_files, remove_duplicates
from app.core.models import Prim
from tests.conftest import fx


def by_name(parts):
    return sorted((round(p.size[0]), round(p.size[1]), p.quantity) for p in parts)


def test_exemplo_lab_detecta_todas_as_pecas():
    rep = import_files([fx("exemplo_lab.dxf")])
    q = sorted(p.quantity for p in rep.parts)
    assert q == [2, 4, 6, 8, 15]
    lid = max(rep.parts, key=lambda p: p.area)
    # tampa: 3 rasgos + furo redondo; o texto e a linha de gravação pertencem à tampa
    kinds = [pr.kind for pr in lid.prims]
    assert "TEXT" in kinds
    assert kinds.count("CIRCLE") == 1
    assert len(lid.holes) >= 3
    side = [p for p in rep.parts if round(p.size[0]) == 110][0]
    assert side.quantity == 4 and len(side.holes) == 2
    assert not any(p.is_open for p in rep.parts)


def test_arcos_soltos_e_polilinha_com_bulge_sao_a_mesma_peca():
    rep = import_files([fx("exemplo_lab.dxf")])
    arcs = [p for p in rep.parts if p.quantity == 15][0]
    assert abs(arcs.area - math.pi * (80 ** 2 - 60 ** 2) * (140 / 360)) < 5


def test_polegadas_convertidas_para_mm():
    rep = import_files([fx("polegadas.dxf")])
    w, h = rep.parts[0].size
    assert abs(w - 101.6) < 1e-6 and abs(h - 50.8) < 1e-6
    assert any("polegadas" in v for v in rep.unit_notes.values())


def test_sem_unidade_assume_mm_e_avisa():
    rep = import_files([fx("sem_unidade.dxf")])
    assert any("assumindo milímetros" in w for w in rep.warnings)
    assert abs(rep.parts[0].size[0] - 30) < 1e-6


def test_blocos_aninhados_rotacao_e_escala():
    rep = import_files([fx("blocos.dxf")])
    sizes = by_name(rep.parts)
    # 4 cópias 40×20 (uma girada 30°, uma 90° num bloco aninhado) e uma escalada 2×
    assert (40, 20, 4) in sizes
    assert (80, 40, 1) in sizes
    for p in rep.parts:
        assert len(p.holes) == 1
        # cor BYBLOCK/camada 0 resolvida pelo INSERT (camada CORTE, vermelho)
        assert all(pr.layer == "CORTE" for pr in p.prims)


def test_duplicadas_removidas():
    rep = import_files([fx("duplicadas.dxf")])
    assert len(rep.parts) == 1
    assert len(rep.parts[0].prims) == 4
    assert any("duplicada" in w for w in rep.warnings)


def test_linhas_soltas_com_folga_sao_fechadas():
    rep = import_files([fx("linhas_soltas.dxf")])
    rect = [p for p in rep.parts if round(p.size[0]) == 100][0]
    assert not rect.is_open
    assert abs(rect.area - 5000) < 5


def test_contorno_aberto_e_texto_solto():
    rep = import_files([fx("contorno_aberto.dxf")])
    opened = [p for p in rep.parts if p.is_open]
    assert len(opened) == 1 and opened[0].warnings
    loose_text = [p for p in rep.parts if any(pr.kind == "TEXT" for pr in p.prims)]
    assert len(loose_text) == 1 and "solto" in loose_text[0].warnings[0]
    assert any(bad for _, bad in rep.preview)


def test_spline_e_elipse():
    rep = import_files([fx("spline_elipse.dxf")])
    assert len(rep.parts) == 3
    kinds = {pr.kind for p in rep.parts for pr in p.prims}
    assert {"SPLINE", "ELLIPSE"} <= kinds
    full = [p for p in rep.parts if any(pr.kind == "ELLIPSE" for pr in p.prims) and len(p.prims) == 1][0]
    assert abs(full.area - math.pi * 40 * 20) < 10


def test_arquivo_corrompido_nao_derruba():
    rep = import_files([fx("corrompido.dxf"), fx("simples.dxf")])
    assert any("corrompido.dxf" in w for w in rep.warnings)
    assert len(rep.parts) == 3


def test_arquivo_inexistente():
    with pytest.raises(DXFImportError):
        read_dxf(fx("nao_existe.dxf"))
    rep = import_files([fx("nao_existe.dxf")])
    assert rep.parts == [] and rep.warnings


def test_varios_arquivos_com_pecas_repetidas_entre_si():
    import_files([fx("simples.dxf"), fx("simples.dxf").replace("simples", "simples")])
    rep2 = import_files([fx("furos.dxf"), fx("exemplo_lab.dxf")])
    lid = max(rep2.parts, key=lambda p: p.area)
    assert lid.quantity == 3  # 1 de furos.dxf + 2 de exemplo_lab.dxf


def test_remove_duplicates_textos_iguais():
    t = Prim("TEXT", {"text": "A", "p": [0, 0], "h": 5, "bbox": [[0, 0], [1, 0], [1, 1], [0, 1]]})
    out, n = remove_duplicates([t, Prim("TEXT", dict(t.data))])
    assert n == 1 and len(out) == 1


def test_entidades_variadas():
    rep = import_files([fx("variados.dxf")])
    kinds = sorted(tuple(sorted(pr.kind for pr in p.prims)) for p in rep.parts)
    assert ("MTEXT", "POLY") in kinds          # POLYLINE 2D com bulge + MTEXT dentro
    assert ("ARC", "LINE") in kinds            # arco com extrusão -Z normalizado e fechado
    assert ("POLY", "TEXT") in kinds           # bloco com atributo
    assert any(p.quantity == 6 for p in rep.parts)  # MINSERT 2×3
    assert any(pr.rgb == (0, 128, 255) for p in rep.parts for pr in p.prims)
    assert not any(p.is_open for p in rep.parts)
    assert any("POINT" in w for w in rep.warnings)


def test_unidade_errada_no_cabecalho_e_sugerida(tmp_path):
    import ezdxf
    from app.core.dxf_import import read_dxf
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 6          # declara metros, mas o desenho está em mm
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (141, 0), (141, 12.5), (0, 12.5)], close=True)
    msp.add_lwpolyline([(0, 50), (24, 50), (24, 116), (0, 116)], close=True)
    p = str(tmp_path / "metros.dxf")
    doc.saveas(p)
    rf = read_dxf(p)
    assert rf.suggested_units == 4
    rep = import_files([p])
    assert rep.extra["suspicious_units"][0][2] == 4
    rep2 = import_files([p], units_override=4)
    assert sorted(round(x.size[0]) for x in rep2.parts) == [24, 141]
    assert rep2.extra["suspicious_units"] == []
    # desenho normal em mm não gera sugestão
    assert read_dxf(fx("simples.dxf")).suggested_units is None


def test_ignorar_textos_e_camadas():
    base = import_files([fx("furos.dxf")])
    assert any(pr.kind == "TEXT" for p in base.parts for pr in p.prims)
    assert set(base.extra["layers"]) >= {"CORTE", "GRAVACAO"}
    sem_txt = import_files([fx("furos.dxf")], ignore_text=True)
    assert not any(pr.kind == "TEXT" for p in sem_txt.parts for pr in p.prims)
    assert any("texto(s) ignorado(s)" in w for w in sem_txt.warnings)
    sem_grav = import_files([fx("furos.dxf")], excluded_layers={"GRAVACAO"})
    assert all(pr.layer != "GRAVACAO" for p in sem_grav.parts for pr in p.prims)
    # sem a linha de gravação, o furo redondo passa a ser aproveitável
    assert len(sem_grav.parts[0].holes) == 4
