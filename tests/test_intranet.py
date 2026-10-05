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


def _mock_server():
    """Servidor local que imita a intranet: arquivos + paginação por POST (10 por página)."""
    import threading
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs
    folder = os.path.dirname(fx("intranet_mock.html"))
    pages = {2: [("8701", "15/09/2026 09:00:00")], 3: [("8702", "20/09/2026 18:30:00")]}

    class H(SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=folder, **k)

        def log_message(self, *a):
            pass

        def do_POST(self):
            q = parse_qs(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode())
            n = int(q.get("pagina", ["1"])[0])
            if n == 1:
                body = open(os.path.join(folder, "intranet_mock.html"), encoding="utf-8").read()
                body = body.split('id="tab_1-1">', 1)[1].split('<div class="tab-pane" id="tab_2-2">', 1)[0]
                body = body.rsplit("</div>", 1)[0]
            else:
                body = "<table><tbody>" + "".join(
                    f'<tr><td>{c}</td><td>561111</td><td>Aluno Pagina {n}</td><td>Corte Laser</td><td>{d}</td>'
                    f'<td>Aguardando</td><td></td><td><a data-codigo="{c}" class="js-visualisa-solicitacao" '
                    f'onclick="abreSolicitacao({c})">ver</a></td></tr>' for c, d in pages.get(n, [])) + \
                    f'</tbody></table><input type="hidden" id="paginaAtual" value="{n}"><ul class="pagination">' + \
                    "".join(f'<li><a data-qtdlinhas="21" data-codigostatus="1" data-pagina="{i}">{i}</a></li>'
                            for i in (1, 2, 3)) + "</ul>"
            data = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_dialogo_baixa_da_pagina_simulada(tmp_path):
    """Navegador embutido abre a página (simulada), lê a lista, abre a solicitação e baixa os DXF."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox")
    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
    try:
        import PySide6.QtWebEngineWidgets  # noqa: F401
    except Exception:
        pytest.skip("QtWebEngine indisponível")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, str(tmp_path / "cfg"))
    from app.ui.intranet import IntranetDialog
    srv = _mock_server()
    url = f"http://127.0.0.1:{srv.server_address[1]}/intranet_mock.html"
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
    # as páginas 2 e 3 da aba Aguardando são lidas sozinhas (clique + requisição repetida)
    assert pump(lambda: len(d._rows) == 5 and not d._crawl.isActive(), 40), d._rows
    # padrão: Aguardando + Corte Laser, mais recente primeiro
    assert [d.list.item(i, 0).text() for i in range(d.list.rowCount())] == ["8759", "8702", "8701"]
    d._header_clicked(3)
    assert [d.list.item(i, 0).text() for i in range(d.list.rowCount())] == ["8701", "8702", "8759"]
    d._header_clicked(3)
    assert pump(lambda: d.page.url().toString().endswith("intranet_mock.html"), 5)
    assert [d.status_filter.itemData(i) for i in range(d.status_filter.count())] == \
        ["Aguardando", "Em execução", "Todos os status"]
    d.status_filter.setCurrentIndex(1)
    assert d.list.rowCount() == 1 and d.list.item(0, 0).text() == "8700"
    d.status_filter.setCurrentIndex(0)
    d.type_filter.setCurrentText("Todos os tipos")
    assert d.list.rowCount() == 4
    d.search.setText("outro")
    assert d.list.rowCount() == 1
    d.search.setText("")
    # clicar na linha só VISUALIZA: nada é baixado
    d.list.cellClicked.emit(0, 1)
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
    srv.shutdown()


def test_lote_varias_solicitacoes(tmp_path):
    """Marcar várias solicitações, ver o lote e enviar todas juntas."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        import PySide6.QtWebEngineWidgets  # noqa: F401
    except Exception:
        pytest.skip("QtWebEngine indisponível")
    from PySide6.QtCore import QSettings, Qt
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, str(tmp_path / "cfg"))
    from app.ui.intranet import ALL_MATERIALS, IntranetDialog
    srv = _mock_server()
    d = IntranetDialog(None, start_url=f"http://127.0.0.1:{srv.server_address[1]}/intranet_mock.html",
                       base_folder=str(tmp_path / "solic"))
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
    assert pump(lambda: len(d._rows) == 5 and not d._crawl.isActive(), 40)
    assert not d.batch_bar.isVisibleTo(d)
    for i in range(d.list.rowCount()):
        if d.list.item(i, 0).text() in ("8759", "8702"):
            d.list.item(i, 0).setCheckState(Qt.Checked)
    assert d._checked == ["8759", "8702"] or d._checked == ["8702", "8759"]
    assert d.batch_bar.isVisibleTo(d) and "2" in d.btn_batch.text()
    d.view_batch()
    assert pump(lambda: len(d.batch) == 2, 40)
    assert not os.path.exists(str(tmp_path / "solic"))          # ver o lote não baixa nada
    d.send(ALL_MATERIALS)
    assert pump(lambda: d.result() == 1, 60)
    assert len(d.batch_result) == 2
    for det, fs in d.batch_result:
        assert len(fs) == 3 and all(os.path.getsize(f.local_path) > 1000 for f in fs)
    # pastas separadas por solicitação, mesmo com arquivos de mesmo nome
    paths = {f.local_path for _, fs in d.batch_result for f in fs}
    assert len(paths) == 6
    # peças de solicitações diferentes não se misturam e levam o nº no nome
    from app.core.intranet import batch_label, batch_summary, file_materials, file_multipliers, file_tags
    files = [f for _, fs in d.batch_result for f in fs]
    rep = import_files([f.local_path for f in files], multipliers=file_multipliers(files),
                       file_materials=file_materials(files), file_tags=file_tags(d.batch_result))
    tags = {p.tag for p in rep.parts}
    assert tags == {"8759", "8702"}
    assert all(p.name.startswith(p.tag + " · ") for p in rep.parts)
    info = batch_summary(d.batch_result)
    assert info["batch"] and len(info["requests"]) == 2 and set(info["materials"]) == {"MDF 3mm", "MDF 6mm"}
    assert batch_label([8759, 8702]) == "lote_8759-8702"
    assert batch_label([1, 2, 3, 4, 5]) == "lote_1_mais4"
    d.deleteLater()
    srv.shutdown()


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
    # arquivo único com todas as placas de todos os materiais (o que abre no RDWorks)
    out2 = export_sheets(rep.parts, res.placements, params, str(tmp_path / "c"), "x", "R2000", combined=True)
    import ezdxf
    allp = [f for f in out2 if os.path.basename(f) == "x_todas_placas.dxf"]
    assert len(allp) == 1
    xs = [e.dxf.center.x for e in ezdxf.readfile(allp[0]).modelspace().query("CIRCLE")]
    assert max(xs) > params.sheet_width        # a placa de 6mm vem ao lado da de 3mm
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


def test_ordem_por_data():
    from app.core.intranet import date_key
    assert date_key("04/10/2026 13:45:58") > date_key("04/10/2026 09:00:00") > date_key("30/09/2026")
    assert date_key("") == (0,)


def test_projeto_guarda_lote(tmp_path):
    a, b = fx("furos.dxf"), fx("simples.dxf")
    tags = {os.path.abspath(a): "8759", os.path.abspath(b): "8760"}
    rep = import_files([a, b], file_tags=tags)
    assert {p.tag for p in rep.parts} == {"8759", "8760"}
    proj = str(tmp_path / "lote.sindri")
    save_project(proj, [a, b], NestParams(), rep.parts, None, label="lote_8759-8760", tags=tags,
                 request={"batch": True, "requests": [{"code": 8759}, {"code": 8760}]},
                 checklist={"cut": [0, 2], "delivered": ["8759"]})
    pr = load_project(proj)
    assert pr.checklist == {}          # sem encaixe salvo, as marcações de corte não valem
    assert pr.tags == tags and {p.tag for p in pr.parts} == {"8759", "8760"}
    assert pr.request["batch"] and pr.label == "lote_8759-8760"


def test_projeto_lote_com_arquivos_de_mesmo_nome(tmp_path):
    import shutil
    a = tmp_path / "8759 - Ana" / "MDF 3mm" / "peca.dxf"
    b = tmp_path / "8760 - Bia" / "Acrilico" / "peca.dxf"
    for p, src in ((a, fx("furos.dxf")), (b, fx("simples.dxf"))):
        p.parent.mkdir(parents=True)
        shutil.copy(src, p)
    fs = [str(a), str(b)]
    mats = {str(a): "MDF 3mm", str(b): "Acrilico"}
    mult = {str(a): 2, str(b): 5}
    tags = {str(a): "8759", str(b): "8760"}
    rep = import_files(fs, multipliers=mult, file_materials=mats, file_tags=tags)
    proj = str(tmp_path / "lote.sindri")
    save_project(proj, fs, NestParams(), rep.parts, None, multipliers=mult, materials=mats, tags=tags)
    pr = load_project(proj)
    assert pr.materials == mats and pr.multipliers == mult and pr.tags == tags
