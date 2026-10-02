import json
import os
import time

import pytest

from app.core.intranet import (RequestFile, file_multipliers, parse_detail, request_folder,
                               safe_name, target_path)
from app.core.models import NestParams
from app.core.part_builder import import_files
from app.core.project import load_project, save_project
from tests.conftest import fx

DETAIL = {"codigo": 8759, "info": {"Nome": "Aluno Exemplo", "Projeto": "Projeto Exemplo"},
          "arquivos": [
              {"nome": "Peça_Meio_Cortada_3mm_2uni_Parte3.dxf", "href": "https://x/updown/A.dxf",
               "material": "MDF 3mm", "quantidade": "2"},
              {"nome": "Base_Parte3_6mm.dxf", "href": "https://x/updown/B.dxf", "material": "MDF 6mm",
               "quantidade": "1"},
              {"nome": "foto.png", "href": "https://x/updown/C.png", "material": "", "quantidade": ""},
          ]}


def test_parse_e_agrupa_por_material(tmp_path):
    d = parse_detail(DETAIL)
    assert d.code == 8759 and d.student.startswith("Aluno")
    mats = d.materials()
    assert set(mats) == {"MDF 3mm", "MDF 6mm"}           # o .png fica de fora
    assert mats["MDF 3mm"][0].quantity == 2
    p = target_path(str(tmp_path), d, mats["MDF 3mm"][0])
    assert p.endswith(os.path.join("8759 - Aluno Exemplo", "MDF 3mm",
                                   "Peça_Meio_Cortada_3mm_2uni_Parte3.dxf"))
    assert safe_name('a/b:c*?') == "a_b_c__"
    assert request_folder(str(tmp_path), d).endswith("8759 - Aluno Exemplo")


def test_quantidade_da_tabela_multiplica_pecas(tmp_path):
    f = RequestFile("furos.dxf", "", "MDF", 3, local_path=fx("furos.dxf"))
    mult = file_multipliers([f])
    rep = import_files([fx("furos.dxf")], multipliers=mult)
    assert rep.parts[0].quantity == 3 and rep.parts[0].file_quantity == 3
    # projeto guarda e restaura as quantidades
    proj = str(tmp_path / "p.dxfnest")
    save_project(proj, [fx("furos.dxf")], NestParams(), rep.parts, None, multipliers=mult, label="8759_MDF")
    pr = load_project(proj)
    assert pr.parts[0].quantity == 3 and pr.label == "8759_MDF"


def test_dialogo_baixa_da_pagina_simulada(tmp_path):
    """Navegador embutido abre a página (simulada), lê a lista, abre a solicitação e baixa os DXF."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox")
    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
    try:
        import PySide6.QtWebEngineWidgets  # noqa: F401
    except Exception:
        pytest.skip("QtWebEngine indisponível")
    from PySide6.QtCore import QSettings, QUrl
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, str(tmp_path / "cfg"))
    from app.ui.intranet import IntranetDialog
    url = QUrl.fromLocalFile(fx("intranet_mock.html")).toString()
    d = IntranetDialog(None, start_url=url, base_folder=str(tmp_path / "solic"))
    d.show()

    def pump(cond, limit=30):
        t = time.time()
        while time.time() - t < limit:
            app.processEvents()
            if cond():
                return True
            time.sleep(0.02)
        return False

    if not pump(lambda: len(d._rows) >= 3, 40):
        pytest.skip("QtWebEngine não renderiza neste ambiente")
    # a 2ª página da aba Aguardando é lida sozinha (clicando na paginação)
    assert pump(lambda: len(d._rows) == 4 and not d._crawl.isActive(), 40)
    assert d.list.rowCount() == 2                      # padrão: Aguardando + Corte Laser (8759 e 8701)
    assert {d.list.item(i, 0).text() for i in range(2)} == {"8759", "8701"}
    assert [d.status_filter.itemData(i) for i in range(d.status_filter.count())] == \
        ["Aguardando", "Em execução", "Todos os status"]
    d.status_filter.setCurrentIndex(1)
    assert d.list.rowCount() == 1 and d.list.item(0, 0).text() == "8700"
    d.status_filter.setCurrentIndex(0)
    d.type_filter.setCurrentText("Todos os tipos")
    assert d.list.rowCount() == 3
    d.search.setText("outro")
    assert d.list.rowCount() == 1
    d.search.setText("")
    # clicar na linha só VISUALIZA: nada é baixado
    d.list.selectRow(0)
    assert pump(lambda: d.detail is not None and d.detail.code == 8759, 40)
    assert not os.path.exists(str(tmp_path / "solic"))
    mats = d.detail.materials()
    assert set(mats) == {"MDF 3mm", "MDF 6mm"}
    assert [f.quantity for f in mats["MDF 6mm"]] == [1, 3]
    # enviar para a placa baixa só o material escolhido
    d.send("MDF 6mm")
    assert pump(lambda: d.result() == 1, 40)
    assert d.chosen_material == "MDF 6mm"
    for f in mats["MDF 6mm"]:
        assert f.local_path and os.path.getsize(f.local_path) > 1000
    assert all(f.local_path is None for f in mats["MDF 3mm"])
    d.deleteLater()


def test_materiais_nunca_dividem_placa(tmp_path):
    from app.core.optimizer import nest
    from app.core.validate import validate_layout
    from app.core.dxf_export import export_sheets
    from app.core.collision import CollisionChecker
    files = [fx("furos.dxf"), fx("simples.dxf"), fx("blocos.dxf")]
    mats = {os.path.abspath(files[0]): "MDF 3mm", os.path.abspath(files[1]): "MDF 6mm",
            os.path.abspath(files[2]): "MDF 6mm"}
    rep = import_files(files, file_materials=mats)
    pmap = {p.id: p for p in rep.parts}
    assert {p.material for p in rep.parts} == {"MDF 3mm", "MDF 6mm"}
    params = NestParams(sheet_width=600, sheet_height=400)
    res = nest(rep.parts, params, time_limit=10, max_generations=2, workers=0, seed=3)
    assert res.sheet_materials == ["MDF 3mm", "MDF 6mm"]          # tudo caberia numa placa, mas não mistura
    for si, m in enumerate(res.sheet_materials):
        assert {pmap[pl.part_id].material for pl in res.placements if pl.sheet_index == si} == {m}
    assert validate_layout(pmap, res.placements, params) == []
    out = export_sheets(rep.parts, res.placements, params, str(tmp_path), "8759_RM500123", "R2000")
    names = sorted(os.path.basename(f) for f in out)
    assert names == ["8759_RM500123_MDF3mm_placa01.dxf", "8759_RM500123_MDF6mm_placa01.dxf"]
    # arrastar uma peça de 6mm para a placa de 3mm é marcado como erro
    cc = CollisionChecker(rep.parts, params)
    pls = [type(p)(**p.to_json()) for p in res.placements]
    i6 = next(i for i, p in enumerate(pls) if pmap[p.part_id].material == "MDF 6mm")
    pls[i6].sheet_index = 0
    pls[i6].x, pls[i6].y = 590, 390
    assert i6 in cc.colliding(pls)


def test_redireciona_da_home_para_solicitacoes():
    from app.core.intranet import should_go_to_requests, INTRANET_URL
    assert should_go_to_requests("https://intranet.fiap.com.br/net/Home", False)
    assert should_go_to_requests("https://intranet.fiap.com.br/", False)
    assert not should_go_to_requests(INTRANET_URL, False)
    assert not should_go_to_requests(INTRANET_URL + "#tab_1-1", False)
    assert not should_go_to_requests("https://intranet.fiap.com.br/login", False)
    assert not should_go_to_requests("https://intranet.fiap.com.br/net/Home", True)   # tela com senha
    assert not should_go_to_requests("https://login.microsoftonline.com/xyz", False)


def test_status_pelas_abas():
    from app.core.intranet import merge_rows, status_options
    rows = merge_rows([
        {"codigo": "1", "abaNome": "AGUARDANDO"}, {"codigo": "2", "abaNome": "EM EXECUÇÃO"},
        {"codigo": "3", "abaNome": "RESULTADO PESQUISA"}, {"codigo": "1", "abaNome": "RESULTADO PESQUISA"},
        {"codigo": "4", "abaNome": "AGUARDANDO RETIRADO"}, {"codigo": "5", "status": "Finalizado"}])
    assert [r["codigo"] for r in rows] == ["1", "2", "3", "4", "5"]
    assert rows[0]["situacao"] == "Aguardando" and rows[1]["situacao"] == "Em execução"
    assert status_options(rows) == ["Aguardando", "Em execução", "Aguardando retirado", "Finalizado",
                                    "Resultado pesquisa"]
