"""Regressões dos problemas reproduzidos na revisão técnica."""
import io
import json
import os
import zipfile
from pathlib import Path

import ezdxf
import pytest
from shapely.geometry import LineString, box

from app.core.geometry import flatten_prim
from app.core.models import NestParams, NestResult, Part, Placement, Prim
from tests.conftest import fx
from app.core.part_builder import build_parts_from_prims, group_identical, import_files, make_part
from app.core.project import ProjectError, load_project, save_project
from app.core.validate import fine_solid, validate_layout


def rect(x=0, y=0, w=20, h=10, rgb=None):
    return Prim("POLY", {"pts": [[x, y, 0], [x+w, y, 0], [x+w, y+h, 0], [x, y+h, 0]],
                         "closed": True}, rgb=rgb)


def dxf(path, w=20, h=10, units=4):
    doc = ezdxf.new("R2000")
    doc.units = units
    doc.modelspace().add_lwpolyline([(0, 0), (w, 0), (w, h), (0, h)], close=True)
    doc.saveas(path)
    return str(path)


def test_contorno_invalido_conserva_todos_os_trechos():
    prim = Prim("POLY", {"pts": [[0, 0, 0], [40, 40, 0], [0, 40, 0], [40, 0, 0]], "closed": True})
    raw, _, warnings = build_parts_from_prims([prim], "bow.dxf", .05, .1)
    part = make_part(raw[0], "A", "A", 1)
    assert fine_solid(part).buffer(1e-6).covers(LineString(flatten_prim(part.prims[0], .01)))
    assert warnings and part.warnings


@pytest.mark.parametrize("delta", [.002, .02, .2])
def test_dimensoes_diferentes_nao_sao_agrupadas(delta):
    raw, _, _ = build_parts_from_prims([rect(w=100, h=100), rect(x=200, w=100+delta, h=100)], "", .05, .1)
    assert len(group_identical(raw, .1)) == 2


def test_rgb_preservado_no_agrupamento_e_nos_furos():
    raw, _, _ = build_parts_from_prims([rect(rgb=(255, 0, 0)), rect(x=50, rgb=(0, 0, 255))], "", .05, .1)
    assert len(group_identical(raw, .1)) == 2
    raw, _, _ = build_parts_from_prims([rect(w=100, h=100, rgb=(255, 0, 0)),
                                      rect(30, 30, 20, 20, rgb=(0, 0, 255))], "", .05, .1)
    assert not raw[0].holes


@pytest.mark.parametrize("spacing", [0, .01, .02])
def test_sobreposicao_independe_da_folga(spacing):
    part = Part("A", "A", "", box(-5, -5, 5, 5), [], [], [], quantity=2)
    pls = [Placement("A", i, 0, 30, 30, 0) for i in range(2)]
    assert any("sobreposição" in e for e in validate_layout({"A": part}, pls, NestParams(spacing=spacing)))


def test_validacao_de_instancias_e_material():
    a = Part("A", "A", "", box(-5, -5, 5, 5), [], [], [], material="MDF")
    b = Part("B", "B", "", box(-5, -5, 5, 5), [], [], [], material="Acrílico")
    params = NestParams()
    assert validate_layout({"A": a}, [Placement("A", 2, 0, 30, 30, 0)], params)
    assert validate_layout({"A": a}, [Placement("X", 0, 0, 30, 30, 0)], params)
    assert validate_layout({"A": a, "B": b}, [Placement("A", 0, 0, 30, 30, 0),
                                               Placement("B", 0, 0, 60, 30, 0)], params)


def test_projeto_portatil_preserva_desenho_mesmo_se_fonte_muda(tmp_path):
    src = dxf(tmp_path / "a.dxf", 100, 50)
    parts = import_files([src]).parts
    project = str(tmp_path / "a.sindri")
    res = NestResult([Placement(parts[0].id, 0, 0, 70, 50, 0)], 1, .1, 0)
    save_project(project, [src], NestParams(), parts, res)
    dxf(Path(src), 125, 40)
    loaded = load_project(project)
    assert loaded.parts[0].size == pytest.approx((100, 50))
    assert loaded.warnings and loaded.result is not None
    Path(src).unlink()
    assert load_project(project).parts[0].identity == parts[0].identity


def test_salva_entre_discos(tmp_path, monkeypatch):
    from app.core import project
    monkeypatch.setattr(project.os.path, "relpath", lambda *a: (_ for _ in ()).throw(ValueError("different drives")))
    dest = str(tmp_path / "cross.sindri")
    save_project(dest, ["Z:/a.dxf"], NestParams(), [], None, multipliers={"Z:/a.dxf": 2},
                 materials={"Z:/a.dxf": "MDF"}, tags={"Z:/a.dxf": "42"})
    assert json.loads(Path(dest).read_text(encoding="utf-8"))["files_rel"][0] == os.path.abspath("Z:/a.dxf")


@pytest.mark.parametrize("data", [b"\xff\xfeinvalid", b"[]", b'{"format":"dxfnest","version":999}',
                                  b'{"format":"dxfnest","params":{"sheet_width":-1}}'])
def test_projeto_malformado_gera_erro_claro(tmp_path, data):
    dest = tmp_path / "bad.sindri"
    dest.write_bytes(data)
    with pytest.raises(ProjectError):
        load_project(str(dest))


def test_dependencia_de_corte_da_peca_no_furo(tmp_path):
    from app.core.dxf_export import export_sheets
    host = Part("H", "H", "", box(-40, -40, 40, 40), [box(-25, -25, 25, 25)],
                [rect(-40, -40, 80, 80, (255, 0, 0)), rect(-25, -25, 50, 50, (0, 255, 0))], [0])
    child = Part("C", "C", "", box(-5, -5, 5, 5), [], [rect(-5, -5, 10, 10, (0, 0, 255))], [0])
    files = export_sheets([host, child], [Placement("H", 0, 0, 50, 50, 0),
                                        Placement("C", 0, 0, 55, 55, 0)], NestParams(), str(tmp_path))
    colors = [e.dxf.color for e in ezdxf.readfile(files[0]).modelspace()]
    assert colors == [5, 3, 1]  # criança, furo, contorno externo da hospedeira


def test_atualizacao_reverte_falha_e_protege_pastas(tmp_path, monkeypatch):
    from app.core import updater
    (tmp_path / "app").mkdir()
    (tmp_path / "app/main.py").write_bytes(b"old main")
    (tmp_path / "sindri.py").write_bytes(b"old launcher")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv/test.txt").write_bytes(b"keep")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, value in {"sindri.py": b"new", "app/main.py": b"new", ".VENV/test.txt": b"bad",
                            "trabalho.sindri": b"bad"}.items():
            z.writestr("Sindri-test/" + name, value)
    replace = updater.os.replace
    def fail(src, dst):
        if Path(dst).name == "main.py":
            raise PermissionError("simulated")
        return replace(src, dst)
    monkeypatch.setattr(updater.os, "replace", fail)
    with pytest.raises(RuntimeError, match="restaurados"):
        updater.apply_zip(buf.getvalue(), str(tmp_path), "new")
    assert (tmp_path / "sindri.py").read_bytes() == b"old launcher"
    assert (tmp_path / "app/main.py").read_bytes() == b"old main"
    assert not (tmp_path / updater.VERSION_FILE).exists()
    monkeypatch.setattr(updater.os, "replace", replace)
    updater.apply_zip(buf.getvalue(), str(tmp_path), "new")
    assert (tmp_path / ".venv/test.txt").read_bytes() == b"keep"
    assert not (tmp_path / "trabalho.sindri").exists()
    assert len(list((tmp_path / updater.BACKUP_DIR).iterdir())) == 2


def test_cli_nao_exporta_layout_incompleto(tmp_path):
    from app.cli import main
    src = dxf(tmp_path / "big.dxf", 100, 50)
    dest = tmp_path / "out"
    assert main([src, "--placa", "10x10", "--margem", "0", "--tempo", "1", "--processos", "0",
                 "--saida", str(dest)]) == 4
    assert not list(dest.glob("*.dxf"))
    assert main([src, "--espaco", "nan"]) == 2


@pytest.fixture
def window(monkeypatch):
    from PySide6.QtWidgets import QApplication, QMessageBox
    from app.ui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    for name in ("question", "warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **kw: QMessageBox.Yes)
    w = MainWindow(workers=0)
    yield w
    w.dirty = False
    w.close()
    app.processEvents()


def test_unidades_por_arquivo_e_quantidades_estaveis(window, tmp_path):
    valid = dxf(tmp_path / "inch.dxf", 1, .5, 1)
    bad = dxf(tmp_path / "bad-units.dxf", 1000, 200, 1)
    assert window.load_files([valid, bad])
    inch = next(p for p in window.parts if p.source_file == valid)
    assert inch.size[0] == pytest.approx(25.4)
    inch.quantity = 7
    inch.rotation_locked = True
    pid = inch.id
    assert window.load_files([dxf(tmp_path / "big.dxf", 1500, 250)], add=True)
    inch = next(p for p in window.parts if p.source_file == valid)
    assert inch.quantity == 7 and inch.id == pid and inch.rotation_locked
    window.project_path = str(tmp_path / "units.sindri")
    window.save_project()
    assert load_project(window.project_path).units == window.file_units


def test_novo_dxf_limpa_destino_e_importacao_falha_preserva_estado(window, tmp_path):
    assert window.load_files([dxf(tmp_path / "a.dxf")])
    project = str(tmp_path / "old.sindri")
    window.project_path = project
    window.save_project()
    window.open_project(project)
    assert window.load_files([dxf(tmp_path / "b.dxf", 30, 15)])
    assert window.project_path is None
    parts = window.parts
    assert window.load_files([str(tmp_path / "missing.dxf")]) is False
    assert window.parts is parts


def test_reduzir_kits_checklist_e_desfazer(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    window.load_files([dxf(tmp_path / "a.dxf")])
    part = window.parts[0]
    part.quantity = 3
    window.placements = [Placement(part.id, i, 0, 30 + 30*i, 30, 0) for i in range(3)]
    window.n_sheets = 1
    monkeypatch.setattr(QInputDialog, "getInt", lambda *a, **kw: (1, True))
    window.multiply_kits()
    assert part.quantity == 1 and len(window.placements) == 1 and not window.unplaced
    window.undo()
    assert part.quantity == 3 and len(window.placements) == 3
    window.placements = window.placements[:1]
    window.on_sheet_cut(0, True)
    assert part.id not in window.done_parts
    from app.core.sheets import checklist_progress
    assert checklist_progress(window.sheet_index(), window.parts, {0}, set())[0][part.id] == (1, 3)
    window._autosave_timer.stop()
    window.on_rotation_lock_changed(part.id, True)
    assert window._autosave_timer.isActive()
    window.undo()
    assert not part.rotation_locked


def test_autosave_sem_encaixe(window, tmp_path):
    window.load_files([dxf(tmp_path / "a.dxf")])
    window.on_quantity_changed(window.parts[0].id, 7)
    window.autosave()
    recovered = load_project(window.autosave_path())
    assert recovered.parts[0].quantity == 7
    assert not recovered.result.placements


def test_exportacao_falha_no_pdf_nao_publica_dxf(window, tmp_path, monkeypatch):
    from app.ui.mainwindow import export as module
    from app.ui.dialogs import settings
    window.load_files([dxf(tmp_path / "source.dxf")])
    p = window.parts[0]
    window.placements = [Placement(p.id, 0, 0, 30, 30, 0)]
    window.n_sheets = 1
    settings().setValue("export/last_dir", str(tmp_path))
    settings().setValue("export/open_rdworks", "false")
    monkeypatch.setattr(module, "export_pdf", lambda *a, **kw: (_ for _ in ()).throw(OSError("PDF bloqueado")))
    window.export()
    assert not list(tmp_path.glob("*_todas_placas.dxf"))
    assert not list(tmp_path.glob(".sindri-export-*"))


def test_cancelamento_antes_da_primeira_solucao(tmp_path):
    import threading
    from app.core.optimizer import GeneticNester
    src = dxf(tmp_path / "a.dxf")
    parts = import_files([src]).parts
    parts[0].quantity = 1000
    nester = GeneticNester(parts, NestParams(), workers=0)
    stop = threading.Event()
    stop.set()
    result = nester.run(stop_event=stop)
    assert not result.placements and len(result.unplaced) == 1000


def test_caches_tem_limite(tmp_path):
    from app.core.optimizer import GeneticNester
    part = import_files([dxf(tmp_path / "a.dxf")]).parts[0]
    nester = GeneticNester([part], NestParams(), workers=0)
    cache = nester.decoder.cache
    cache.max_entries = 2
    a = cache.variant(part.id, 0)
    for rotation in (0, 90, 180, 270):
        b = cache.variant(part.id, rotation)
        cache.nfp_np(a, b)
    assert max(len(cache._variants), len(cache._nfp), len(cache._nfp_np)) <= 2


def test_nome_reservado_do_windows():
    from app.core.intranet import safe_name
    for name in ("CON", "aux.dxf", "LPT1.dxf", "NUL.txt", "COM9"):
        assert safe_name(name).startswith("_")
    assert not safe_name("arquivo . ").endswith((" ", "."))


def test_atualizacao_concorrente_recusada(tmp_path):
    from app.core.updater import apply_zip
    (tmp_path / ".sindri-update.lock").write_text("running")
    with pytest.raises(RuntimeError, match="outra atualização"):
        apply_zip(b"", str(tmp_path), "x")


def test_versoes_instaladas_sao_verificadas(monkeypatch):
    from app import runtime_check
    monkeypatch.setattr(runtime_check, "version", lambda name: "1.20.0")
    with pytest.raises(ImportError, match="numpy"):
        runtime_check.check()


def test_download_falha_preserva_copia_anterior(tmp_path):
    from app.ui.intranet import IntranetDialog
    from PySide6.QtWebEngineCore import QWebEngineDownloadRequest
    from types import SimpleNamespace
    final = tmp_path / "original.dxf"
    final.write_bytes(b"original")
    temp = tmp_path / ".download.part"
    temp.write_bytes(b"incompleto")
    request = SimpleNamespace(_sindri_temp=str(temp), state=lambda: QWebEngineDownloadRequest.DownloadInterrupted,
                              receivedBytes=lambda: 10)
    f = SimpleNamespace(local_path=str(final))
    obj = SimpleNamespace(_pending={"key": (None, f)}, _downloads=[request], file_rows={},
                          _log_dl=lambda msg: None, _fkey=lambda d, f: "x",
                          _remove_partial=IntranetDialog._remove_partial,
                          _dl_timer=SimpleNamespace(stop=lambda: None), _finish_send=lambda: None)
    IntranetDialog._download_finished(obj, request, "key")
    assert final.read_bytes() == b"original" and not temp.exists()
    assert f.local_path is None and not obj._downloads


def test_cancelamento_de_download_cancela_requisicao(tmp_path):
    from app.ui.intranet import IntranetDialog
    from types import SimpleNamespace
    calls = []
    temp = tmp_path / ".download.part"
    temp.write_bytes(b"incompleto")
    req = SimpleNamespace(_sindri_temp=str(temp), isFinished=lambda: False, cancel=lambda: calls.append("cancel"))
    obj = SimpleNamespace(_downloads=[req], _remove_partial=IntranetDialog._remove_partial)
    IntranetDialog._cancel_downloads(obj)
    assert calls == ["cancel"] and not temp.exists() and not obj._downloads


def test_merge_request_info_avulsa_vira_lote():
    from app.core.intranet import merge_request_info, request_codes
    single = {"code": 10, "rm": "1", "nome": "A", "materials": ["MDF 3mm"]}
    new = {"batch": True, "codes": [11], "materials": ["MDF 6mm"],
           "requests": [{"code": 11, "rm": "2", "nome": "B", "materials": ["MDF 6mm"]}]}
    m = merge_request_info(single, new)
    assert m["batch"] and request_codes(m) == ["10", "11"] and m["materials"] == ["MDF 3mm", "MDF 6mm"]
    again = merge_request_info(m, new)                       # repetida não entra duas vezes
    assert request_codes(again) == ["10", "11"]
    assert merge_request_info(None, new) is new


def test_adicionar_solicitacao_mantem_encaixe_e_corte(window, tmp_path):
    import shutil
    from app.core.intranet import RequestDetail, RequestFile, request_codes
    a = tmp_path / "10" / "a.dxf"
    b = tmp_path / "11" / "b.dxf"
    a.parent.mkdir()
    b.parent.mkdir()
    shutil.copy(fx("furos.dxf"), a)
    shutil.copy(fx("simples.dxf"), b)
    d1 = RequestDetail(10, {"RM": "1", "Nome": "A"}, [RequestFile("a.dxf", "", "MDF 3mm", 1, local_path=str(a))])
    d2 = RequestDetail(11, {"RM": "2", "Nome": "B"}, [RequestFile("b.dxf", "", "MDF 3mm", 2, local_path=str(b))])
    from app.core.intranet import request_summary
    assert window.load_files([str(a)], request_info=request_summary(d1, ["MDF 3mm"]),
                             materials={str(a): "MDF 3mm"}, request_label="10_RM1")
    # encaixe "feito à mão": uma peça por placa, e a primeira placa já cortada
    from app.core.models import Placement
    window.placements = [Placement(p.id, i, 0, 10.0 + 5 * i, 10.0, 0.0) for p in window.parts for i in range(p.quantity)]
    window.n_sheets = 1
    window.cut_sheets = {0}
    window.done_parts = {window.parts[0].id}
    before = {(pl.part_id, pl.instance): (pl.x, pl.y) for pl in window.placements}
    old_ids = {p.id for p in window.parts}

    assert window._add_requests([(d2, d2.files)])
    assert request_codes(window.request_info) == ["10", "11"] and window.request_info["batch"]
    assert window.request_label == "lote_10-11"
    assert {p.tag for p in window.parts} == {"10", "11"}
    assert old_ids <= {p.id for p in window.parts}               # peças antigas mantêm a identidade
    assert {(pl.part_id, pl.instance): (pl.x, pl.y) for pl in window.placements} == before
    assert window.cut_sheets == {0} and window.parts[0].id in window.done_parts
    new_parts = [p for p in window.parts if p.tag == "11"]
    assert new_parts and all((p.id, 0) in window.unplaced for p in new_parts)

    # a mesma solicitação de novo: nada muda
    n = len(window.parts)
    assert window._add_requests([(d2, d2.files)]) is False and len(window.parts) == n
